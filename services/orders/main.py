"""Orders Service.

Flow:
1. Receive POST /orders
2. Persist order record in Postgres (status=pending)
3. Call Payments (charge) + Inventory (reserve) in parallel
4. Persist outbox event for Notifications (within same transaction)
5. Update order status to confirmed / failed
6. Background worker polls outbox and publishes to notification queue

Resilience applied:
- Idempotency via middleware (Redis-backed)
- Circuit breaker + bulkhead + retry for Payments and Inventory
- Per-hop timeout + deadline propagation
- Outbox pattern for async notifications
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel, Field

import sys
sys.path.insert(0, "/app")

from libs.observability import (
    setup_logging,
    setup_tracing,
    ObservabilityMiddleware,
)
from libs.observability.metrics import (
    DOWNSTREAM_REQUESTS_TOTAL,
    DOWNSTREAM_ERRORS_TOTAL,
    ORDERS_CREATED_TOTAL,
    OUTBOX_PUBLISHED_TOTAL,
    OUTBOX_PENDING_GAUGE,
    DUPLICATE_WRITE_RATE,
)
from libs.resilience import (
    RetryConfig,
    retry_with_backoff,
    CircuitBreaker,
    Bulkhead,
    IdempotencyMiddleware,
    IdempotencyStore,
)
from libs.resilience.timeout import DEADLINE_HEADER

setup_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

SERVICE_NAME = "orders"
DB_DSN = os.getenv("DATABASE_URL", "postgresql://orders:orders@postgres-orders:5432/orders")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PAYMENTS_URL = os.getenv("PAYMENTS_URL", "http://payments:8002")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://inventory:8003")
NOTIFICATIONS_URL = os.getenv("NOTIFICATIONS_URL", "http://notifications:8004")

# ── Resilience ────────────────────────────────────────────────────────────────
_payments_breaker = CircuitBreaker("payments", failure_threshold=5, open_duration=30)
_inventory_breaker = CircuitBreaker("inventory", failure_threshold=5, open_duration=30)
_payments_bulkhead = Bulkhead("payments", max_concurrent=20, max_wait=1.0)
_inventory_bulkhead = Bulkhead("inventory", max_concurrent=20, max_wait=1.0)
_retry_cfg = RetryConfig(max_attempts=3, base_delay=0.1, max_delay=5.0)

# ── Global resources (set during lifespan) ────────────────────────────────────
_db_pool: Optional[asyncpg.Pool] = None
_redis: Optional[aioredis.Redis] = None
_outbox_task: Optional[asyncio.Task] = None


# ── DB helpers ────────────────────────────────────────────────────────────────
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id TEXT NOT NULL,
    items JSONB NOT NULL,
    total_amount NUMERIC(12,2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    idempotency_key TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS orders_idempotency_key_idx
    ON orders (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type TEXT NOT NULL,
    aggregate_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    published BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    published_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS outbox_unpublished_idx
    ON outbox_events (created_at)
    WHERE NOT published;
"""


async def init_db() -> asyncpg.Pool:
    pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLES_SQL)
    return pool


# ── Outbox background worker ───────────────────────────────────────────────────
async def outbox_worker(db_pool: asyncpg.Pool, notifications_url: str) -> None:
    """Poll the outbox table and publish unpublished events."""
    logger.info("outbox_worker_started")
    while True:
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, event_type, payload, aggregate_id
                    FROM outbox_events
                    WHERE NOT published
                    ORDER BY created_at
                    LIMIT 50
                    FOR UPDATE SKIP LOCKED
                    """
                )
            if not rows:
                await asyncio.sleep(1)
                continue

            for row in rows:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        resp = await client.post(
                            f"{notifications_url}/events",
                            json={
                                "event_type": row["event_type"],
                                "aggregate_id": str(row["aggregate_id"]),
                                "payload": dict(row["payload"]),
                            },
                        )
                        if resp.status_code < 300:
                            async with db_pool.acquire() as conn:
                                await conn.execute(
                                    "UPDATE outbox_events SET published=TRUE, "
                                    "published_at=NOW() WHERE id=$1",
                                    row["id"],
                                )
                            OUTBOX_PUBLISHED_TOTAL.labels(
                                service=SERVICE_NAME, event_type=row["event_type"]
                            ).inc()
                except Exception as exc:
                    logger.warning(
                        "outbox_publish_error",
                        extra={"event_id": str(row["id"]), "error": str(exc)},
                    )

            # Update pending gauge
            async with db_pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM outbox_events WHERE NOT published"
                )
            OUTBOX_PENDING_GAUGE.labels(service=SERVICE_NAME).set(count or 0)

        except Exception as exc:
            logger.error("outbox_worker_error", extra={"error": str(exc)})
            await asyncio.sleep(5)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_pool, _redis, _outbox_task
    _db_pool = await init_db()
    _redis = await aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    _outbox_task = asyncio.create_task(outbox_worker(_db_pool, NOTIFICATIONS_URL))
    logger.info("orders_service_started")
    yield
    _outbox_task.cancel()
    await _redis.aclose()
    await _db_pool.close()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Orders Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(ObservabilityMiddleware, service_name=SERVICE_NAME)
app.mount("/metrics", make_asgi_app())

setup_tracing(SERVICE_NAME)


async def _add_idempotency_middleware():
    """Add idempotency middleware once Redis is available."""
    pass  # Applied lazily below after lifespan sets _redis


# ── Models ────────────────────────────────────────────────────────────────────
class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    unit_price: float = Field(gt=0)


class CreateOrderRequest(BaseModel):
    customer_id: str
    items: list[OrderItem]
    idempotency_key: Optional[str] = None


# ── Downstream helpers ────────────────────────────────────────────────────────
def _get_forward_headers(request: Request) -> dict:
    headers = {}
    for h in ["X-Correlation-ID", DEADLINE_HEADER, "Idempotency-Key"]:
        if v := request.headers.get(h):
            headers[h] = v
    return headers


async def _call_payments(order_id: str, amount: float, headers: dict) -> dict:
    budget = [3]  # max 3 retries within this request
    retry_cfg = RetryConfig(
        max_attempts=3, base_delay=0.2, max_delay=5.0, retry_budget=[budget[0]]
    )

    async def _do():
        DOWNSTREAM_REQUESTS_TOTAL.labels(
            from_service=SERVICE_NAME, to_service="payments", operation="charge"
        ).inc()
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{PAYMENTS_URL}/payments/charge",
                json={"order_id": order_id, "amount": amount},
                headers=headers,
            )
            if resp.status_code >= 500:
                DOWNSTREAM_ERRORS_TOTAL.labels(
                    from_service=SERVICE_NAME, to_service="payments",
                    operation="charge", error_type="http_5xx",
                ).inc()
                resp.raise_for_status()
            return resp

    resp = await _payments_bulkhead.call(
        lambda: _payments_breaker.call(
            lambda: retry_with_backoff(_do, retry_cfg,
                                       service_name=SERVICE_NAME, operation="charge")
        )
    )
    return resp.json()


async def _call_inventory(order_id: str, items: list, headers: dict) -> dict:
    retry_cfg = RetryConfig(max_attempts=3, base_delay=0.2, max_delay=5.0)

    async def _do():
        DOWNSTREAM_REQUESTS_TOTAL.labels(
            from_service=SERVICE_NAME, to_service="inventory", operation="reserve"
        ).inc()
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{INVENTORY_URL}/inventory/reserve",
                json={
                    "order_id": order_id,
                    "items": [i.model_dump() for i in items],
                },
                headers=headers,
            )
            if resp.status_code >= 500:
                DOWNSTREAM_ERRORS_TOTAL.labels(
                    from_service=SERVICE_NAME, to_service="inventory",
                    operation="reserve", error_type="http_5xx",
                ).inc()
                resp.raise_for_status()
            return resp

    resp = await _inventory_bulkhead.call(
        lambda: _inventory_breaker.call(
            lambda: retry_with_backoff(_do, retry_cfg,
                                       service_name=SERVICE_NAME, operation="reserve")
        )
    )
    return resp.json()


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.post("/orders", status_code=201)
async def create_order(req: CreateOrderRequest, request: Request):
    """Create an order and trigger payment + inventory in parallel."""
    if _db_pool is None:
        raise HTTPException(status_code=503, detail="DB not ready")

    idempotency_key = request.headers.get("Idempotency-Key") or req.idempotency_key
    forward_headers = _get_forward_headers(request)

    # Check idempotency via Redis
    if idempotency_key and _redis:
        cached = await _redis.get(f"idem:orders:{idempotency_key}")
        if cached:
            DUPLICATE_WRITE_RATE.labels(service=SERVICE_NAME, operation="create_order").inc()
            return JSONResponse(json.loads(cached), status_code=200,
                                headers={"X-Idempotency-Replayed": "true"})

    total = sum(i.quantity * i.unit_price for i in req.items)
    order_id = str(uuid.uuid4())

    # Persist order + outbox event atomically
    async with _db_pool.acquire() as conn:
        async with conn.transaction():
            # Check for duplicate idempotency key at DB level
            if idempotency_key:
                existing = await conn.fetchrow(
                    "SELECT id FROM orders WHERE idempotency_key=$1", idempotency_key
                )
                if existing:
                    DUPLICATE_WRITE_RATE.labels(
                        service=SERVICE_NAME, operation="create_order"
                    ).inc()
                    row = await conn.fetchrow(
                        "SELECT * FROM orders WHERE id=$1", existing["id"]
                    )
                    result = _order_row_to_dict(row)
                    return JSONResponse(result, status_code=200,
                                        headers={"X-Idempotency-Replayed": "true"})

            await conn.execute(
                """
                INSERT INTO orders (id, customer_id, items, total_amount, status, idempotency_key)
                VALUES ($1, $2, $3, $4, 'pending', $5)
                """,
                order_id,
                req.customer_id,
                json.dumps([i.model_dump() for i in req.items]),
                total,
                idempotency_key,
            )
            # Outbox event
            await conn.execute(
                """
                INSERT INTO outbox_events (aggregate_type, aggregate_id, event_type, payload)
                VALUES ('order', $1, 'order_created', $2)
                """,
                order_id,
                json.dumps({
                    "order_id": order_id,
                    "customer_id": req.customer_id,
                    "total_amount": float(total),
                }),
            )

    # Call payments + inventory in parallel
    payment_result = None
    inventory_result = None
    final_status = "confirmed"

    try:
        payment_result, inventory_result = await asyncio.gather(
            _call_payments(order_id, total, forward_headers),
            _call_inventory(order_id, req.items, forward_headers),
            return_exceptions=True,
        )

        if isinstance(payment_result, Exception):
            logger.error("payment_failed", extra={"order_id": order_id,
                                                    "error": str(payment_result)})
            final_status = "payment_failed"
        if isinstance(inventory_result, Exception):
            logger.error("inventory_failed", extra={"order_id": order_id,
                                                      "error": str(inventory_result)})
            final_status = "inventory_failed" if final_status != "payment_failed" else "failed"

    except Exception as exc:
        logger.error("order_processing_error", extra={"order_id": order_id, "error": str(exc)})
        final_status = "failed"

    # Update order status
    async with _db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE orders SET status=$1, updated_at=NOW() WHERE id=$2",
            final_status, order_id,
        )
        # Add final outbox event
        await conn.execute(
            """
            INSERT INTO outbox_events (aggregate_type, aggregate_id, event_type, payload)
            VALUES ('order', $1, 'order_status_updated', $2)
            """,
            order_id,
            json.dumps({"order_id": order_id, "status": final_status}),
        )

    ORDERS_CREATED_TOTAL.inc()

    result = {
        "order_id": order_id,
        "status": final_status,
        "customer_id": req.customer_id,
        "total_amount": float(total),
        "payment": payment_result if not isinstance(payment_result, Exception) else None,
        "inventory": inventory_result if not isinstance(inventory_result, Exception) else None,
    }

    # Cache in Redis for idempotency
    if idempotency_key and _redis:
        await _redis.setex(
            f"idem:orders:{idempotency_key}", 86400, json.dumps(result)
        )

    status_code = 201 if final_status == "confirmed" else 202
    return JSONResponse(result, status_code=status_code)


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    if _db_pool is None:
        raise HTTPException(status_code=503, detail="DB not ready")
    async with _db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM orders WHERE id=$1", order_id)
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return _order_row_to_dict(row)


@app.get("/orders")
async def list_orders(limit: int = 20):
    if _db_pool is None:
        raise HTTPException(status_code=503, detail="DB not ready")
    async with _db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT $1", limit
        )
    return [_order_row_to_dict(r) for r in rows]


def _order_row_to_dict(row) -> dict:
    return {
        "order_id": str(row["id"]),
        "customer_id": row["customer_id"],
        "items": row["items"],
        "total_amount": float(row["total_amount"]),
        "status": row["status"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
