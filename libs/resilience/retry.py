"""Retry with exponential backoff + full jitter.

Design decisions:
- Full jitter (not equal jitter) reduces collision probability under load
- retry_budget_remaining is threaded as a mutable int so callers can share
  a budget across a chain of calls within a single incoming request
- Only retries on network errors or explicitly listed HTTP status codes to
  avoid masking application-level bugs (e.g. 400 Bad Request is NOT retried)
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Collection, Optional, TypeVar

import httpx

from libs.observability.metrics import RETRY_ATTEMPTS

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Status codes that are safe to retry (server-side transient errors only)
DEFAULT_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 0.1        # seconds
    max_delay: float = 30.0        # seconds
    multiplier: float = 2.0
    jitter: bool = True
    retryable_status_codes: Collection[int] = field(
        default_factory=lambda: set(DEFAULT_RETRYABLE_STATUS)
    )
    # Per-request retry budget; shared mutable int.  None = unlimited.
    retry_budget: Optional[list[int]] = None


async def retry_with_backoff(
    func: Callable[[], Awaitable[T]],
    config: RetryConfig,
    *,
    service_name: str = "unknown",
    operation: str = "unknown",
) -> T:
    """Execute *func* with retry logic defined by *config*.

    Raises the last exception after exhausting attempts.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(config.max_attempts):
        # Check shared retry budget
        if config.retry_budget is not None and config.retry_budget[0] <= 0:
            logger.warning(
                "retry_budget_exhausted",
                extra={"service": service_name, "operation": operation},
            )
            raise last_exc or RuntimeError("Retry budget exhausted")

        try:
            result = await func()
            # If result is an httpx.Response, check the status code
            if isinstance(result, httpx.Response):
                if result.status_code in config.retryable_status_codes:
                    if attempt < config.max_attempts - 1:
                        delay = _backoff_delay(attempt, config)
                        RETRY_ATTEMPTS.labels(
                            service=service_name, operation=operation
                        ).inc()
                        logger.info(
                            "retrying_request",
                            extra={
                                "attempt": attempt + 1,
                                "status_code": result.status_code,
                                "delay": delay,
                                "service": service_name,
                                "operation": operation,
                            },
                        )
                        if config.retry_budget is not None:
                            config.retry_budget[0] -= 1
                        await asyncio.sleep(delay)
                        last_exc = httpx.HTTPStatusError(
                            f"Retryable status {result.status_code}",
                            request=result.request,
                            response=result,
                        )
                        continue
            return result
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.RemoteProtocolError,
            ConnectionError,
            asyncio.TimeoutError,
        ) as exc:
            last_exc = exc
            if attempt < config.max_attempts - 1:
                delay = _backoff_delay(attempt, config)
                RETRY_ATTEMPTS.labels(
                    service=service_name, operation=operation
                ).inc()
                logger.info(
                    "retrying_after_error",
                    extra={
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "delay": delay,
                        "service": service_name,
                        "operation": operation,
                    },
                )
                if config.retry_budget is not None:
                    config.retry_budget[0] -= 1
                await asyncio.sleep(delay)
            else:
                raise
        except Exception:
            raise  # non-retryable

    raise last_exc or RuntimeError("Retry failed without exception")


def _backoff_delay(attempt: int, config: RetryConfig) -> float:
    """Compute exponential backoff with optional full jitter."""
    base = min(config.base_delay * (config.multiplier ** attempt), config.max_delay)
    if config.jitter:
        return random.uniform(0, base)
    return base
