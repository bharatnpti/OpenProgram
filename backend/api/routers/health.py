from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST

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


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    registry = get_registry(request)
    settings = get_settings_from_request(request)
    metrics = request.app.state.metrics
    try:
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.inbound_events_grace_seconds)
        stuck = await registry.inbound_chat_event_repository().list_stuck(
            settings.tenant_id, cutoff
        )
        open_dead_letters = await registry.dead_letter_repository().count_open_dead_letters(
            settings.tenant_id
        )
        metrics.set_workflow_backlog(open_dead_letters, len(stuck))
        applied = await registry.writeback_audit_repository().count_applied_writebacks(
            settings.tenant_id
        )
        metrics.set_writeback_applied(applied)
    except Exception:
        # Metrics scraping must never fail the endpoint; leave prior gauge values.
        pass
    return Response(
        content=metrics.render(),
        media_type=CONTENT_TYPE_LATEST,
    )
