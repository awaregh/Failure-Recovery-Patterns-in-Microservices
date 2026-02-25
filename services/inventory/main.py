"""Inventory Service.

Simulates stock reservation with:
- PostgreSQL for stock records
- Simulated lock contention (configurable via chaos config)
- Idempotency enforcement
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel, Field

import sys
sys.path.insert(0, "/app")

from libs.observability import setup_logging, setup_tracing, ObservabilityMiddleware
from libs.observability.metrics import DUPLICATE_WRITE_RATE

setup_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

SERVICE_NAME = "inventory"
DB_DSN = os.getenv("DATABASE_URL", "postgresql://inventory:inventory@postgres-inventory:5432/inventory")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/2")

_db_pool: Optional[asyncpg.Pool] = None
_redis: Optional[aioredis.Redis] = None

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0,
    reserved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL DEFAULT 'reserved',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS reservation_idempotency_idx
    ON reservations (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Seed some products
INSERT INTO products (id, name, stock) VALUES
    ('prod-001', 'Widget A', 1000),
    ('prod-002', 'Widget B', 500),
    ('prod-003', 'Gadget X', 200),
    ('prod-004', 'Gadget Y', 100)
ON CONFLICT (id) DO NOTHING;
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_pool, _redis
    _db_pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=10)
    async with _db_pool.acquire() as conn:
        await conn.execute(CREATE_TABLES_SQL)
    _redis = await aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    logger.info("inventory_service_started")
    yield
    await _redis.aclose()
    await _db_pool.close()


app = FastAPI(title="Inventory Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(ObservabilityMiddleware, service_name=SERVICE_NAME)
app.mount("/metrics", make_asgi_app())

setup_tracing(SERVICE_NAME)


class ReserveItem(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)


class ReserveRequest(BaseModel):
    order_id: str
    items: list[ReserveItem]


async def _get_chaos_config() -> dict:
    if _redis:
        try:
            lock_ms = await _redis.get("chaos:inventory:lock_contention_ms")
            fail_rate = await _redis.get("chaos:inventory:error_rate")
            return {
                "lock_contention_ms": float(lock_ms) if lock_ms else 0.0,
                "error_rate": float(fail_rate) if fail_rate else 0.0,
            }
        except Exception:
            pass
    return {"lock_contention_ms": 0.0, "error_rate": 0.0}


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.post("/inventory/reserve", status_code=200)
async def reserve(req: ReserveRequest, request: Request):
    if _db_pool is None:
        raise HTTPException(status_code=503, detail="DB not ready")

    idempotency_key = request.headers.get("Idempotency-Key")

    # Idempotency check
    if idempotency_key and _redis:
        cached = await _redis.get(f"idem:inventory:{idempotency_key}")
        if cached:
            DUPLICATE_WRITE_RATE.labels(service=SERVICE_NAME, operation="reserve").inc()
            return JSONResponse(json.loads(cached), headers={"X-Idempotency-Replayed": "true"})

    chaos = await _get_chaos_config()

    # Simulate lock contention
    if chaos["lock_contention_ms"] > 0:
        jitter = chaos["lock_contention_ms"] * 0.3
        delay = (chaos["lock_contention_ms"] + random.uniform(-jitter, jitter)) / 1000.0
        await asyncio.sleep(delay)

    # Simulate error injection
    if random.random() < chaos["error_rate"]:
        raise HTTPException(status_code=503, detail="Inventory DB lock timeout")

    reservation_ids = []
    async with _db_pool.acquire() as conn:
        async with conn.transaction():
            for item in req.items:
                # Check stock availability
                row = await conn.fetchrow(
                    "SELECT stock, reserved FROM products WHERE id=$1 FOR UPDATE",
                    item.product_id,
                )
                if not row:
                    # Unknown product – still allow (demo)
                    row = {"stock": 9999, "reserved": 0}

                available = row["stock"] - row["reserved"]
                if available < item.quantity:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Insufficient stock for {item.product_id}: "
                               f"available={available}, requested={item.quantity}",
                    )

                res_id = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO reservations (id, order_id, product_id, quantity, idempotency_key)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """,
                    res_id, req.order_id, item.product_id, item.quantity,
                    f"{idempotency_key}:{item.product_id}" if idempotency_key else None,
                )
                await conn.execute(
                    "UPDATE products SET reserved=reserved+$1 WHERE id=$2",
                    item.quantity, item.product_id,
                )
                reservation_ids.append(res_id)

    result = {
        "order_id": req.order_id,
        "reservation_ids": reservation_ids,
        "status": "reserved",
        "items": [i.model_dump() for i in req.items],
    }

    if idempotency_key and _redis:
        await _redis.setex(f"idem:inventory:{idempotency_key}", 86400, json.dumps(result))

    logger.info("inventory_reserved", extra={
        "order_id": req.order_id,
        "items": len(req.items),
    })
    return result


@app.get("/inventory/{product_id}")
async def get_stock(product_id: str):
    if _db_pool is None:
        raise HTTPException(status_code=503, detail="DB not ready")
    async with _db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM products WHERE id=$1", product_id)
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    return {
        "product_id": row["id"],
        "name": row["name"],
        "stock": row["stock"],
        "reserved": row["reserved"],
        "available": row["stock"] - row["reserved"],
    }


@app.post("/chaos/config")
async def set_chaos(lock_contention_ms: Optional[float] = None, error_rate: Optional[float] = None):
    if _redis:
        if lock_contention_ms is not None:
            await _redis.set("chaos:inventory:lock_contention_ms", str(lock_contention_ms))
        if error_rate is not None:
            await _redis.set("chaos:inventory:error_rate", str(error_rate))
    return {"status": "updated"}


@app.delete("/chaos/config")
async def clear_chaos():
    if _redis:
        await _redis.delete("chaos:inventory:lock_contention_ms", "chaos:inventory:error_rate")
    return {"status": "cleared"}
