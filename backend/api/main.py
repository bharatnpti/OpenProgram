from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from starlette.responses import Response

from api.routers import graph, health, persona, webhooks
from config.settings import Settings, get_settings
from infra.observability.logging import configure_logging
from infra.observability.metrics import build_http_metrics
from infra.observability.tracing import configure_tracing, correlation_scope
from infra.persistence.seed_data import seed_demo_graph
from infra.registry import ServiceRegistry


def create_app(
    settings: Settings | None = None,
    registry: ServiceRegistry | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_registry = registry or ServiceRegistry(resolved_settings)
    resolved_metrics = build_http_metrics(resolved_settings.environment)
    configure_logging()
    configure_tracing(resolved_settings.otel_exporter_otlp_endpoint)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            if resolved_settings.runtime_mode == "memory":
                await seed_demo_graph(
                    resolved_registry.graph_repository(),
                    resolved_registry.time_series_repository(),
                    resolved_settings.tenant_id,
                )
            yield
        finally:
            await resolved_registry.close()

    app = FastAPI(title="PulseOps", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.registry = resolved_registry
    app.state.metrics = resolved_metrics
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def correlation_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        correlation_id = request.headers.get("x-correlation-id", str(uuid4()))
        async with correlation_scope(correlation_id):
            tracer = trace.get_tracer("pulseops.api")
            started = perf_counter()
            status_code = 500
            with tracer.start_as_current_span(f"{request.method} {request.url.path}") as span:
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.route", request.url.path)
                span.set_attribute("pulseops.correlation_id", correlation_id)
                try:
                    response = await call_next(request)
                except Exception as exc:
                    span.record_exception(exc)
                    _observe_request(request, status_code, perf_counter() - started)
                    raise
                status_code = response.status_code
                span.set_attribute("http.status_code", response.status_code)
            response.headers["x-correlation-id"] = correlation_id
            _observe_request(request, status_code, perf_counter() - started)
            return response

    app.include_router(health.router)
    app.include_router(graph.router)
    app.include_router(persona.router)
    app.include_router(webhooks.router)
    return app


def _observe_request(request: Request, status_code: int, duration: float) -> None:
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    if not isinstance(route_path, str):
        route_path = request.url.path
    request.app.state.metrics.observe_request(
        request.method,
        route_path,
        status_code,
        duration,
    )
