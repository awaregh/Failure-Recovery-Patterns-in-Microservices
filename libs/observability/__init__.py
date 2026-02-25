"""Observability library: logging, metrics, tracing."""

from .logging import setup_logging, get_logger
from .metrics import (
    HTTP_REQUESTS_TOTAL,
    REQUEST_DURATION,
    DOWNSTREAM_REQUESTS_TOTAL,
    DOWNSTREAM_ERRORS_TOTAL,
    RETRY_ATTEMPTS,
    BREAKER_STATE,
    BREAKER_OPEN_TOTAL,
    BULKHEAD_REJECTIONS,
    IDEMPOTENCY_HITS,
    IDEMPOTENCY_CONFLICTS,
    LOAD_SHED_TOTAL,
    metrics_app,
)
from .tracing import setup_tracing, get_tracer
from .middleware import ObservabilityMiddleware

__all__ = [
    "setup_logging",
    "get_logger",
    "HTTP_REQUESTS_TOTAL",
    "REQUEST_DURATION",
    "DOWNSTREAM_REQUESTS_TOTAL",
    "DOWNSTREAM_ERRORS_TOTAL",
    "RETRY_ATTEMPTS",
    "BREAKER_STATE",
    "BREAKER_OPEN_TOTAL",
    "BULKHEAD_REJECTIONS",
    "IDEMPOTENCY_HITS",
    "IDEMPOTENCY_CONFLICTS",
    "LOAD_SHED_TOTAL",
    "metrics_app",
    "setup_tracing",
    "get_tracer",
    "ObservabilityMiddleware",
]
