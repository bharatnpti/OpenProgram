"""The reads behind Today and Check-in: the own conversation, who must act, and the counts.

- ``GET /me/conversation?day=``: the caller's own chat turns of one day, read
  without refreshing their retention. No person id is taken: nobody, an admin
  included, reads anyone else's conversation here.
- ``GET /projects/{id}/asks?as_of=``: the project's "who must act", the day
  report's asks as one flat list, for a reader of the project's progress. An
  executive gets the owning team instead of names.
- ``GET /me/today-counts?as_of=``: replies of the day and open warnings over the
  caller's part. A developer reads no aggregate (403).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import (
    as_of_or_tenant_today,
    day_or_tenant_today,
    get_current_principal,
    get_delivery_scope_service,
    get_registry,
    get_risk_service,
    get_settings_from_request,
)
from api.today_dtos import ConversationResponse, ProjectAsksResponse, TodayCountsResponse
from config.settings import Settings
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.delivery_scope import DeliveryScopeService
from core.application.risk_service import RiskService
from core.application.today_service import TodayService
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound
from infra.registry import ServiceRegistry

router = APIRouter(tags=["persona"])

Caller = Annotated[Principal, Depends(get_current_principal)]


def get_today_service(
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
    risks: Annotated[RiskService, Depends(get_risk_service)],
    scope: Annotated[DeliveryScopeService, Depends(get_delivery_scope_service)],
) -> TodayService:
    return TodayService(
        conversation_repository=registry.conversation_repository(),
        status_repository=registry.status_repository(),
        graph_repository=registry.graph_repository(),
        day_reports=registry.day_report_builder(),
        delivery_scope=scope,
        risk_service=risks,
        retention_days=settings.conversation_retention_days,
        today=registry.tenant_today,
    )


Service = Annotated[TodayService, Depends(get_today_service)]


@router.get("/me/conversation", response_model=ConversationResponse)
async def my_conversation(
    principal: Caller,
    service: Service,
    day: Annotated[date, Depends(day_or_tenant_today)],
) -> ConversationResponse:
    """The caller's own conversation of ``day``, read-only; past the window, "not kept"."""
    _ensure(principal, Capability.READ_OWN_WORK)
    return ConversationResponse.from_view(
        await service.own_conversation(principal.tenant_id, principal.subject, day)
    )


@router.get(
    "/projects/{project_id}/asks",
    response_model=ProjectAsksResponse,
    responses={404: {"description": "No such project."}},
)
async def project_asks(
    project_id: str,
    principal: Caller,
    service: Service,
    as_of: Annotated[date, Depends(as_of_or_tenant_today)],
) -> ProjectAsksResponse:
    """Who must act on the project: each ask's what, who, how long and why, by the item."""
    try:
        view = await service.project_asks(principal, project_id, as_of)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProjectAsksResponse.from_view(view)


@router.get("/me/today-counts", response_model=TodayCountsResponse)
async def my_today_counts(
    principal: Caller,
    service: Service,
    as_of: Annotated[date, Depends(as_of_or_tenant_today)],
) -> TodayCountsResponse:
    """Replies of the day and open warnings over the caller's part; none for a developer."""
    try:
        view = await service.today_counts(principal, as_of)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return TodayCountsResponse.from_view(view)


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
