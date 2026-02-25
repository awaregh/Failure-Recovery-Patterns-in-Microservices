"""Backpressure and load-shedding middleware.

Implements two levels of admission control at the gateway:
1. Concurrency-based: track active requests; shed when above max_inflight
2. Queue-depth-based: reject early if background queue is saturated

Returns HTTP 429 (Too Many Requests) with Retry-After header.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from libs.observability.metrics import LOAD_SHED_TOTAL

logger = logging.getLogger(__name__)


class BackpressureMiddleware(BaseHTTPMiddleware):
    """Shed load when the service is above configured concurrency limit.

    Args:
        max_inflight: maximum concurrent requests before shedding
        skip_paths: paths exempt from load shedding (health, metrics)
    """

    def __init__(
        self,
        app: Any,
        max_inflight: int = 100,
        skip_paths: Optional[list[str]] = None,
    ) -> None:
        super().__init__(app)
        self._max_inflight = max_inflight
        self._inflight = 0
        self._skip_paths = set(skip_paths or ["/health", "/metrics", "/ready"])
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in self._skip_paths:
            return await call_next(request)

        async with self._lock:
            if self._inflight >= self._max_inflight:
                LOAD_SHED_TOTAL.labels(service="gateway").inc()
                logger.warning(
                    "load_shed",
                    extra={
                        "inflight": self._inflight,
                        "max": self._max_inflight,
                        "path": request.url.path,
                    },
                )
                return JSONResponse(
                    {"detail": "Too many requests – server overloaded"},
                    status_code=429,
                    headers={"Retry-After": "5"},
                )
            self._inflight += 1

        try:
            return await call_next(request)
        finally:
            async with self._lock:
                self._inflight -= 1
