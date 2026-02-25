"""Structured JSON logging setup.

Every log record includes:
- timestamp (ISO-8601)
- level
- logger name
- service name (from SERVICE_NAME env var)
- correlation_id (from contextvars, set by middleware)
- trace_id / span_id (if OpenTelemetry trace is active)
- message
- any extra kwargs
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextvars import ContextVar
from typing import Optional

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Try to get OTel trace context
        trace_id = ""
        span_id = ""
        try:
            from opentelemetry import trace as otel_trace
            span = otel_trace.get_current_span()
            ctx = span.get_span_context()
            if ctx.is_valid:
                trace_id = format(ctx.trace_id, "032x")
                span_id = format(ctx.span_id, "016x")
        except Exception:
            pass

        log_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                         + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "service": SERVICE_NAME,
            "correlation_id": correlation_id_var.get(""),
            "trace_id": trace_id,
            "span_id": span_id,
            "message": record.getMessage(),
        }

        # Merge any extra fields added via extra={} in log calls
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            }:
                log_entry[key] = value

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger to emit structured JSON."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Suppress noisy uvicorn access logs in favour of our middleware
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
