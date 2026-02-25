"""Prometheus metrics definitions shared across all services.

All metrics are created here so import order doesn't matter.  Each service
imports the metrics it needs; unused ones stay at zero.
"""
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

# ── HTTP layer ──────────────────────────────────────────────────────────────
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "route", "method", "status"],
)

REQUEST_DURATION = Histogram(
    "request_duration_seconds",
    "HTTP request duration",
    ["service", "route", "method"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30],
)

# ── Downstream calls ─────────────────────────────────────────────────────────
DOWNSTREAM_REQUESTS_TOTAL = Counter(
    "downstream_requests_total",
    "Outgoing calls to downstream services",
    ["from_service", "to_service", "operation"],
)

DOWNSTREAM_ERRORS_TOTAL = Counter(
    "downstream_errors_total",
    "Failed outgoing calls to downstream services",
    ["from_service", "to_service", "operation", "error_type"],
)

# ── Resilience patterns ───────────────────────────────────────────────────────
RETRY_ATTEMPTS = Counter(
    "retry_attempts_total",
    "Number of retry attempts",
    ["service", "operation"],
)

BREAKER_STATE = Gauge(
    "breaker_state",
    "Circuit breaker state: 0=closed 1=open 2=half_open",
    ["downstream"],
)

BREAKER_OPEN_TOTAL = Counter(
    "breaker_open_total",
    "Number of times the circuit breaker tripped to OPEN",
    ["downstream"],
)

BULKHEAD_REJECTIONS = Counter(
    "bulkhead_rejections_total",
    "Requests rejected by bulkhead semaphore",
    ["downstream"],
)

IDEMPOTENCY_HITS = Counter(
    "idempotency_hits_total",
    "Idempotent requests served from cache",
    ["service"],
)

IDEMPOTENCY_CONFLICTS = Counter(
    "idempotency_conflicts_total",
    "Concurrent duplicate requests detected",
    ["service"],
)

LOAD_SHED_TOTAL = Counter(
    "load_shed_total",
    "Requests shed by backpressure admission control",
    ["service"],
)

# ── Outbox ───────────────────────────────────────────────────────────────────
OUTBOX_PUBLISHED_TOTAL = Counter(
    "outbox_published_total",
    "Outbox events successfully published",
    ["service", "event_type"],
)

OUTBOX_PENDING_GAUGE = Gauge(
    "outbox_pending",
    "Current number of unpublished outbox events",
    ["service"],
)

# ── Business metrics ─────────────────────────────────────────────────────────
ORDERS_CREATED_TOTAL = Counter(
    "orders_created_total",
    "Total orders successfully created",
    [],
)

DUPLICATE_WRITE_RATE = Counter(
    "duplicate_write_total",
    "Duplicate write attempts detected",
    ["service", "operation"],
)

# ASGI app that Prometheus can scrape
metrics_app = make_asgi_app()
