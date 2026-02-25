"""Bulkhead: semaphore-based concurrency limiter per downstream.

Prevents one slow downstream from exhausting the entire worker pool.
Each downstream gets its own asyncio.Semaphore so a spike of payment
requests cannot starve inventory calls, for example.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from libs.observability.metrics import BULKHEAD_REJECTIONS

logger = logging.getLogger(__name__)


class Bulkhead:
    """Async semaphore bulkhead.

    Args:
        name: identifies the downstream (for metrics/logs)
        max_concurrent: maximum simultaneous in-flight requests
        max_wait: seconds to wait for a semaphore slot before rejecting
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int = 20,
        max_wait: float = 1.0,
    ) -> None:
        self.name = name
        self.max_concurrent = max_concurrent
        self.max_wait = max_wait
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def call(self, func: Callable[[], Awaitable[Any]]) -> Any:
        """Execute *func* within the bulkhead, rejecting if at capacity."""
        try:
            acquired = await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self.max_wait
            )
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(downstream=self.name).inc()
            logger.warning(
                "bulkhead_rejected",
                extra={"downstream": self.name, "max_concurrent": self.max_concurrent},
            )
            from fastapi import HTTPException
            raise HTTPException(
                status_code=503,
                detail=f"Bulkhead full for {self.name} (max {self.max_concurrent})",
                headers={"Retry-After": "2"},
            )
        try:
            return await func()
        finally:
            self._semaphore.release()

    @property
    def available_slots(self) -> int:
        return self._semaphore._value  # internal, acceptable for observability
