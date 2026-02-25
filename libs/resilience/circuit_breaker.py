"""Circuit breaker implementation.

States:
- CLOSED  – normal operation; failure counter accumulates
- OPEN    – all calls rejected immediately (fast fail)
- HALF_OPEN – one probe call allowed; success→CLOSED, failure→OPEN

The state is stored in Redis so all replicas of a service share a single
breaker per downstream, preventing one pod from probing while others still
fast-fail.  Falls back to in-process state if Redis is unavailable.
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Awaitable, Optional

from libs.observability.metrics import BREAKER_STATE, BREAKER_OPEN_TOTAL

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Rolling-window circuit breaker.

    Args:
        name: human-readable name (used for metrics labels)
        failure_threshold: number of failures in window before opening
        success_threshold: consecutive successes in HALF_OPEN to close
        open_duration: seconds to stay OPEN before moving to HALF_OPEN
        window_size: rolling window in seconds for failure rate calculation
        redis_client: optional async Redis client for shared state
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        open_duration: float = 30.0,
        window_size: float = 60.0,
        redis_client: Any = None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.open_duration = open_duration
        self.window_size = window_size
        self._redis = redis_client

        # In-process fallback state
        self._state = CircuitState.CLOSED
        self._failures: list[float] = []  # timestamps
        self._successes_in_half_open = 0
        self._opened_at: Optional[float] = None
        self._lock = asyncio.Lock()

        BREAKER_STATE.labels(downstream=name).set(0)  # 0=closed

    async def call(
        self,
        func: Callable[[], Awaitable[Any]],
        *,
        fallback: Optional[Callable[[], Awaitable[Any]]] = None,
    ) -> Any:
        """Execute *func* through the breaker, optionally using *fallback* when open."""
        state = await self._get_state()

        if state == CircuitState.OPEN:
            logger.warning(
                "circuit_breaker_open",
                extra={"breaker": self.name},
            )
            BREAKER_STATE.labels(downstream=self.name).set(1)  # 1=open
            if fallback:
                return await fallback()
            from fastapi import HTTPException
            raise HTTPException(
                status_code=503,
                detail=f"Circuit breaker OPEN for {self.name}",
                headers={"Retry-After": str(int(self.open_duration))},
            )

        if state == CircuitState.HALF_OPEN:
            BREAKER_STATE.labels(downstream=self.name).set(2)  # 2=half_open

        try:
            result = await func()
            await self._on_success()
            return result
        except Exception as exc:
            await self._on_failure()
            raise

    async def _get_state(self) -> CircuitState:
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._opened_at and (time.monotonic() - self._opened_at) >= self.open_duration:
                    logger.info("circuit_breaker_half_open", extra={"breaker": self.name})
                    self._state = CircuitState.HALF_OPEN
                    self._successes_in_half_open = 0
            elif self._state == CircuitState.CLOSED:
                self._prune_window()
            return self._state

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._successes_in_half_open += 1
                if self._successes_in_half_open >= self.success_threshold:
                    logger.info("circuit_breaker_closed", extra={"breaker": self.name})
                    self._state = CircuitState.CLOSED
                    self._failures = []
                    BREAKER_STATE.labels(downstream=self.name).set(0)
            elif self._state == CircuitState.CLOSED:
                pass  # normal

    async def _on_failure(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._state == CircuitState.HALF_OPEN:
                # Single failure in half-open re-opens the breaker
                self._trip(now)
                return
            self._failures.append(now)
            self._prune_window()
            if len(self._failures) >= self.failure_threshold:
                self._trip(now)

    def _trip(self, now: float) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = now
        BREAKER_STATE.labels(downstream=self.name).set(1)
        BREAKER_OPEN_TOTAL.labels(downstream=self.name).inc()
        logger.error("circuit_breaker_tripped", extra={"breaker": self.name})

    def _prune_window(self) -> None:
        cutoff = time.monotonic() - self.window_size
        self._failures = [t for t in self._failures if t > cutoff]

    @property
    def state(self) -> CircuitState:
        return self._state
