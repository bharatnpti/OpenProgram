from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_tracing_configured = False


def configure_tracing(otlp_endpoint: str | None) -> None:
    global _tracing_configured
    if _tracing_configured or not otlp_endpoint:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "pulseops-backend"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    trace.set_tracer_provider(provider)
    _tracing_configured = True


def current_correlation_id() -> str:
    existing = correlation_id_var.get()
    if existing is not None:
        return existing
    generated = str(uuid4())
    correlation_id_var.set(generated)
    return generated


@asynccontextmanager
async def correlation_scope(correlation_id: str) -> AsyncIterator[None]:
    token = correlation_id_var.set(correlation_id)
    try:
        yield
    finally:
        correlation_id_var.reset(token)
