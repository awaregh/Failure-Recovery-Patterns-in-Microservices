"""OpenTelemetry tracing setup.

Configures OTLP gRPC exporter (Jaeger/Tempo/Collector compatible).
Falls back to NoOp if the exporter is unavailable so services boot
without a collector present.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_tracer = None


def setup_tracing(service_name: str) -> None:
    global _tracer
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
                exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
                provider.add_span_processor(BatchSpanProcessor(exporter))
                logger.info("otel_tracing_configured", extra={"endpoint": endpoint})
            except Exception as exc:
                logger.warning(
                    "otel_exporter_unavailable",
                    extra={"error": str(exc)},
                )

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)

        # Instrument httpx and FastAPI automatically
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor().instrument()
        except Exception:
            pass
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            HTTPXClientInstrumentor().instrument()
        except Exception:
            pass

    except ImportError:
        logger.warning("opentelemetry_not_installed")


def get_tracer(name: str = "default"):
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


class _NoOpTracer:
    """Minimal no-op tracer so code doesn't need null checks."""
    def start_as_current_span(self, name, **kwargs):
        return _NoOpSpan()


class _NoOpSpan:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, *args):
        pass

    def record_exception(self, *args):
        pass

    def set_status(self, *args):
        pass
