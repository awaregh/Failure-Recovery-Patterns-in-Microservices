"""Notifications Service.

Consumes order events (via HTTP from outbox worker or direct async queue).
In production this would be a Kafka/Redis Streams consumer.
Here it uses an in-memory queue with a swappable backend.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Optional

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel

import sys
sys.path.insert(0, "/app")

from libs.observability import setup_logging, setup_tracing, ObservabilityMiddleware
from libs.observability.metrics import DUPLICATE_WRITE_RATE

setup_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

SERVICE_NAME = "notifications"
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/3")

_redis: Optional[aioredis.Redis] = None
# In-memory event dedup store (would be Redis in production)
_processed_events: set[str] = set()
_event_log: deque = deque(maxlen=1000)
_consumer_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis, _consumer_task
    _redis = await aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    _consumer_task = asyncio.create_task(_redis_stream_consumer())
    logger.info("notifications_service_started")
    yield
    _consumer_task.cancel()
    await _redis.aclose()


async def _redis_stream_consumer():
    """Background task: consume events from Redis Stream."""
    stream_key = "notifications:events"
    group_name = "notifications-group"
    consumer_name = f"consumer-{os.getpid()}"

    # Create stream group if not exists
    try:
        if _redis:
            await _redis.xgroup_create(stream_key, group_name, id="0", mkstream=True)
    except Exception:
        pass  # group already exists

    while True:
        try:
            if _redis is None:
                await asyncio.sleep(1)
                continue
            messages = await _redis.xreadgroup(
                group_name, consumer_name, {stream_key: ">"}, count=10, block=1000
            )
            for _stream, entries in (messages or []):
                for msg_id, data in entries:
                    event_id = data.get("event_id", str(msg_id))
                    if event_id in _processed_events:
                        # Dedup: already processed
                        DUPLICATE_WRITE_RATE.labels(
                            service=SERVICE_NAME, operation="consume_event"
                        ).inc()
                        await _redis.xack(stream_key, group_name, msg_id)
                        continue

                    _processed_events.add(event_id)
                    _event_log.append({
                        "event_id": event_id,
                        "data": data,
                        "processed_at": time.time(),
                    })
                    logger.info("notification_sent", extra={"event_id": event_id, "data": data})
                    await _redis.xack(stream_key, group_name, msg_id)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("consumer_error", extra={"error": str(exc)})
            await asyncio.sleep(2)


app = FastAPI(title="Notifications Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(ObservabilityMiddleware, service_name=SERVICE_NAME)
app.mount("/metrics", make_asgi_app())

setup_tracing(SERVICE_NAME)


class EventPayload(BaseModel):
    event_type: str
    aggregate_id: str
    payload: dict


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.post("/events", status_code=202)
async def receive_event(event: EventPayload):
    """HTTP ingestion endpoint (used by outbox worker)."""
    event_id = f"{event.event_type}:{event.aggregate_id}"

    # Dedup check
    if event_id in _processed_events:
        DUPLICATE_WRITE_RATE.labels(service=SERVICE_NAME, operation="receive_event").inc()
        return JSONResponse(
            {"status": "already_processed", "event_id": event_id},
            headers={"X-Idempotency-Replayed": "true"},
        )

    _processed_events.add(event_id)
    _event_log.append({
        "event_id": event_id,
        "event_type": event.event_type,
        "aggregate_id": event.aggregate_id,
        "payload": event.payload,
        "received_at": time.time(),
    })

    # Publish to Redis Stream for async consumers
    if _redis:
        try:
            await _redis.xadd(
                "notifications:events",
                {
                    "event_id": event_id,
                    "event_type": event.event_type,
                    "aggregate_id": event.aggregate_id,
                    "payload": json.dumps(event.payload),
                },
            )
        except Exception as exc:
            logger.warning("redis_stream_publish_error", extra={"error": str(exc)})

    logger.info("event_received", extra={
        "event_type": event.event_type,
        "aggregate_id": event.aggregate_id,
    })
    return {"status": "accepted", "event_id": event_id}


@app.get("/events")
async def list_events(limit: int = 50):
    events = list(_event_log)[-limit:]
    return {"events": events, "total": len(_event_log)}
