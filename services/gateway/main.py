"""Gateway Service – single entry point for external traffic.

Responsibilities:
- Route to Orders, Payments, Inventory
- Enforce admission control (backpressure / load shedding)
- Propagate X-Request-Deadline and X-Correlation-ID headers
- Expose /health and /metrics
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from starlette.routing import Mount

import sys
sys.path.insert(0, "/app")

from libs.observability import setup_logging, setup_tracing, ObservabilityMiddleware
from libs.resilience import (
    BackpressureMiddleware,
    RetryConfig,
    retry_with_backoff,
    CircuitBreaker,
    Bulkhead,
    TimeoutConfig,
)
from libs.resilience.timeout import DEADLINE_HEADER

setup_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

SERVICE_NAME = "gateway"
ORDERS_URL = os.getenv("ORDERS_URL", "http://orders:8001")
PAYMENTS_URL = os.getenv("PAYMENTS_URL", "http://payments:8002")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://inventory:8003")

# ── Resilience components (shared within this process) ──────────────────────
_orders_breaker = CircuitBreaker("orders", failure_threshold=5, open_duration=30)
_payments_breaker = CircuitBreaker("payments", failure_threshold=5, open_duration=30)
_inventory_breaker = CircuitBreaker("inventory", failure_threshold=5, open_duration=30)

_orders_bulkhead = Bulkhead("orders", max_concurrent=50, max_wait=1.0)
_payments_bulkhead = Bulkhead("payments", max_concurrent=30, max_wait=1.0)
_inventory_bulkhead = Bulkhead("inventory", max_concurrent=30, max_wait=1.0)

_retry_config = RetryConfig(max_attempts=3, base_delay=0.1, max_delay=5.0)

# ── App setup ────────────────────────────────────────────────────────────────
app = FastAPI(title="Gateway", version="1.0.0")
app.add_middleware(ObservabilityMiddleware, service_name=SERVICE_NAME)
app.add_middleware(BackpressureMiddleware, max_inflight=200)
app.mount("/metrics", make_asgi_app())

setup_tracing(SERVICE_NAME)


def _build_headers(request: Request) -> dict:
    """Forward tracing + correlation headers and set a deadline."""
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    deadline = str(time.time() + 25.0)  # 25-second overall deadline
    return {
        "X-Correlation-ID": correlation_id,
        DEADLINE_HEADER: deadline,
        "Content-Type": "application/json",
    }


async def _forward(
    method: str,
    url: str,
    headers: dict,
    body: bytes | None,
    breaker: CircuitBreaker,
    bulkhead: Bulkhead,
) -> httpx.Response:
    """Forward request through bulkhead → circuit breaker → retry."""

    async def _do_request() -> httpx.Response:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.request(method, url, headers=headers, content=body)
            if resp.status_code >= 500:
                resp.raise_for_status()
            return resp

    async def _in_breaker() -> httpx.Response:
        return await breaker.call(_do_request)

    async def _in_bulkhead() -> httpx.Response:
        return await bulkhead.call(_in_breaker)

    return await retry_with_backoff(
        _in_bulkhead,
        _retry_config,
        service_name=SERVICE_NAME,
        operation=url,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/ready")
async def ready():
    """Readiness: verify downstream reachability."""
    return {"status": "ready"}


# ── Order proxying ────────────────────────────────────────────────────────────
@app.post("/orders")
async def create_order(request: Request):
    headers = _build_headers(request)
    body = await request.body()
    try:
        resp = await _forward("POST", f"{ORDERS_URL}/orders", headers, body,
                               _orders_breaker, _orders_bulkhead)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("gateway_orders_error", extra={"error": str(exc)})
        raise HTTPException(status_code=502, detail="Orders service unavailable")
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@app.get("/orders/{order_id}")
async def get_order(order_id: str, request: Request):
    headers = _build_headers(request)
    try:
        resp = await _forward("GET", f"{ORDERS_URL}/orders/{order_id}", headers, None,
                               _orders_breaker, _orders_bulkhead)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@app.get("/orders")
async def list_orders(request: Request):
    headers = _build_headers(request)
    try:
        resp = await _forward("GET", f"{ORDERS_URL}/orders", headers, None,
                               _orders_breaker, _orders_bulkhead)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


# ── Status endpoint ────────────────────────────────────────────────────────────
@app.get("/status/breakers")
async def breaker_status():
    return {
        "orders": _orders_breaker.state,
        "payments": _payments_breaker.state,
        "inventory": _inventory_breaker.state,
    }
