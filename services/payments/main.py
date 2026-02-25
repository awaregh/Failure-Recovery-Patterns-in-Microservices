"""Payments Service.

Simulates an external payment processor with:
- Configurable latency (PAYMENT_LATENCY_P95_MS env var)
- Configurable error rate (PAYMENT_ERROR_RATE env var, 0.0–1.0)
- Idempotency enforcement
- Prometheus metrics
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

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel, Field

import sys
sys.path.insert(0, "/app")

from libs.observability import setup_logging, setup_tracing, ObservabilityMiddleware
from libs.observability.metrics import DUPLICATE_WRITE_RATE
from libs.resilience import IdempotencyMiddleware, IdempotencyStore

setup_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

SERVICE_NAME = "payments"
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/1")

# Chaos knobs – toggled by chaos scripts via Redis flags
_FAULT_LATENCY_MS = float(os.getenv("PAYMENT_LATENCY_MS", "100"))
_FAULT_ERROR_RATE = float(os.getenv("PAYMENT_ERROR_RATE", "0.0"))

_redis: Optional[aioredis.Redis] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis
    _redis = await aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    logger.info("payments_service_started")
    yield
    await _redis.aclose()


app = FastAPI(title="Payments Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(ObservabilityMiddleware, service_name=SERVICE_NAME)
app.mount("/metrics", make_asgi_app())

setup_tracing(SERVICE_NAME)


class ChargeRequest(BaseModel):
    order_id: str
    amount: float = Field(gt=0)


async def _get_fault_config() -> tuple[float, float]:
    """Read current fault config from Redis (allows runtime toggling)."""
    if _redis:
        try:
            lat_ms = await _redis.get("chaos:payments:latency_ms")
            err_rate = await _redis.get("chaos:payments:error_rate")
            latency = float(lat_ms) if lat_ms else _FAULT_LATENCY_MS
            error_rate = float(err_rate) if err_rate else _FAULT_ERROR_RATE
            return latency, error_rate
        except Exception:
            pass
    return _FAULT_LATENCY_MS, _FAULT_ERROR_RATE


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.post("/payments/charge", status_code=200)
async def charge(req: ChargeRequest, request: Request):
    idempotency_key = request.headers.get("Idempotency-Key")

    # Check idempotency cache
    if idempotency_key and _redis:
        cached = await _redis.get(f"idem:payments:{idempotency_key}")
        if cached:
            DUPLICATE_WRITE_RATE.labels(service=SERVICE_NAME, operation="charge").inc()
            logger.info("payment_idempotency_hit", extra={"key": idempotency_key})
            return JSONResponse(json.loads(cached), headers={"X-Idempotency-Replayed": "true"})

    # Simulate latency + errors (chaos injection)
    latency_ms, error_rate = await _get_fault_config()
    if latency_ms > 0:
        # Add jitter ±20%
        jitter = latency_ms * 0.2
        actual_latency = (latency_ms + random.uniform(-jitter, jitter)) / 1000.0
        await asyncio.sleep(actual_latency)

    if random.random() < error_rate:
        logger.warning("payment_fault_injected", extra={"order_id": req.order_id})
        raise HTTPException(status_code=503, detail="Payment processor temporarily unavailable")

    # Simulate payment processing
    transaction_id = str(uuid.uuid4())
    result = {
        "transaction_id": transaction_id,
        "order_id": req.order_id,
        "amount": req.amount,
        "status": "charged",
        "timestamp": time.time(),
    }

    # Cache result for idempotency
    if idempotency_key and _redis:
        await _redis.setex(f"idem:payments:{idempotency_key}", 86400, json.dumps(result))

    logger.info("payment_charged", extra={
        "order_id": req.order_id,
        "amount": req.amount,
        "transaction_id": transaction_id,
    })
    return result


@app.get("/payments/{transaction_id}")
async def get_payment(transaction_id: str):
    return {"transaction_id": transaction_id, "status": "charged"}


@app.post("/chaos/config")
async def set_chaos_config(latency_ms: Optional[float] = None, error_rate: Optional[float] = None):
    """Runtime chaos configuration endpoint."""
    if _redis:
        if latency_ms is not None:
            await _redis.set("chaos:payments:latency_ms", str(latency_ms))
        if error_rate is not None:
            await _redis.set("chaos:payments:error_rate", str(error_rate))
    return {"status": "updated", "latency_ms": latency_ms, "error_rate": error_rate}


@app.delete("/chaos/config")
async def clear_chaos_config():
    """Reset chaos configuration."""
    if _redis:
        await _redis.delete("chaos:payments:latency_ms", "chaos:payments:error_rate")
    return {"status": "cleared"}
