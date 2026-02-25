"""Timeout helpers with deadline propagation.

Per-hop timeouts are enforced at the HTTP client level (httpx).
The *overall* request deadline is propagated via the X-Request-Deadline
header so downstream services can cancel work early and avoid wasted
compute after the upstream has already given up.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

DEADLINE_HEADER = "X-Request-Deadline"


@dataclass
class TimeoutConfig:
    # Per-hop timeout in seconds
    connect_timeout: float = 2.0
    read_timeout: float = 10.0
    write_timeout: float = 5.0
    # Overall request deadline (absolute epoch time); None = no limit
    deadline: Optional[float] = None

    @classmethod
    def from_deadline_header(cls, header_value: Optional[str]) -> "TimeoutConfig":
        """Construct config from the X-Request-Deadline header value."""
        config = cls()
        if header_value:
            try:
                config.deadline = float(header_value)
                # Remaining time shrinks per-hop timeout
                remaining = config.deadline - time.time()
                if remaining > 0:
                    config.read_timeout = min(config.read_timeout, remaining)
                else:
                    config.read_timeout = 0.001  # already expired
            except (ValueError, TypeError):
                pass
        return config

    def remaining_seconds(self) -> Optional[float]:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.time())

    def is_expired(self) -> bool:
        if self.deadline is None:
            return False
        return time.time() >= self.deadline


async def with_timeout(
    func: Callable[[], Awaitable[Any]],
    timeout_seconds: float,
    *,
    operation: str = "unknown",
) -> Any:
    """Run *func* with a hard timeout, raising asyncio.TimeoutError on breach."""
    try:
        return await asyncio.wait_for(func(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning(
            "operation_timed_out",
            extra={"operation": operation, "timeout": timeout_seconds},
        )
        raise
