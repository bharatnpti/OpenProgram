from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from starlette.responses import Response

from api.routers import graph, health, webhooks
from config.settings import Settings, get_settings
from infra.observability.logging import configure_logging
from infra.observability.tracing import correlation_scope
from infra.persistence.seed_data import seed_demo_graph
from infra.registry import ServiceRegistry


def create_app(
    settings: Settings | None = None,
    registry: ServiceRegistry | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_registry = registry or ServiceRegistry(resolved_settings)
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await seed_demo_graph(
            resolved_registry.graph_repository(),
            resolved_registry.time_series_repository(),
            resolved_settings.tenant_id,
        )
        yield

    app = FastAPI(title="PulseOps", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.registry = resolved_registry
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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
            with tracer.start_as_current_span(f"{request.method} {request.url.path}") as span:
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.route", request.url.path)
                span.set_attribute("pulseops.correlation_id", correlation_id)
                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
            response.headers["x-correlation-id"] = correlation_id
            return response

    app.include_router(health.router)
    app.include_router(graph.router)
    app.include_router(webhooks.router)
    return app
