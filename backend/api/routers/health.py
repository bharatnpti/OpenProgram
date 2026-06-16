from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from api.dependencies import get_registry, get_settings_from_request
from api.dtos import HealthResponse, ReadyResponse
from config.settings import Settings
from infra.observability.tracing import current_correlation_id
from infra.registry import ServiceRegistry

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> HealthResponse:
    return HealthResponse(
        status="ok",
        environment=settings.environment,
        tenant_id=settings.tenant_id,
        correlation_id=current_correlation_id(),
    )


@router.get("/ready", response_model=ReadyResponse)
async def ready(registry: Annotated[ServiceRegistry, Depends(get_registry)]) -> ReadyResponse:
    dependencies = await registry.readiness()
    return ReadyResponse(
        status="ok" if all(dependencies.values()) else "degraded",
        dependencies=dependencies,
    )


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request) -> str:
    settings = get_settings_from_request(request)
    return f'pulseops_info{{environment="{settings.environment}"}} 1\n'
