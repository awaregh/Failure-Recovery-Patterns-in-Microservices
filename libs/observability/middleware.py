"""FastAPI middleware that emits Prometheus metrics and structured access logs."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from libs.observability.logging import correlation_id_var
from libs.observability.metrics import HTTP_REQUESTS_TOTAL, REQUEST_DURATION

logger = logging.getLogger(__name__)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Emit metrics + structured access log per request."""

    def __init__(self, app: Any, service_name: str) -> None:
        super().__init__(app)
        self._service = service_name

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        token = correlation_id_var.set(correlation_id)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            raise
        finally:
            elapsed = time.perf_counter() - start
            route = request.url.path

            # Don't track metrics path itself to avoid cardinality explosion
            if route != "/metrics":
                HTTP_REQUESTS_TOTAL.labels(
                    service=self._service,
                    route=route,
                    method=request.method,
                    status=str(status_code),
                ).inc()
                REQUEST_DURATION.labels(
                    service=self._service,
                    route=route,
                    method=request.method,
                ).observe(elapsed)

            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": route,
                    "status": status_code,
                    "duration_ms": round(elapsed * 1000, 2),
                    "correlation_id": correlation_id,
                },
            )
            correlation_id_var.reset(token)
