from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_current_principal, get_persona_view_service
from api.dtos import (
    FocusResponse,
    PodBlockersResponse,
    PodCheckinsResponse,
    PortfolioHeatmapResponse,
    ProgramTreeResponse,
    ProjectProgressResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.persona_views import PersonaViewService
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied

router = APIRouter(tags=["personas"])


@router.get("/me/focus", response_model=FocusResponse)
async def my_focus(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> FocusResponse:
    _ensure(principal, Capability.READ_OWN_WORK)
    view = await persona_service.focus(principal.tenant_id, principal.subject, as_of)
    return FocusResponse.from_view(view)


@router.get("/pods/{pod_id}/blockers", response_model=PodBlockersResponse)
async def pod_blockers(
    pod_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> PodBlockersResponse:
    _ensure(principal, Capability.READ_POD_BLOCKERS)
    view = await persona_service.pod_blockers(principal.tenant_id, pod_id, as_of)
    return PodBlockersResponse.from_view(view)


@router.get("/pods/{pod_id}/checkins", response_model=PodCheckinsResponse)
async def pod_checkins(
    pod_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> PodCheckinsResponse:
    _ensure(principal, Capability.READ_POD_CHECKINS)
    view = await persona_service.pod_checkins(principal.tenant_id, pod_id, as_of)
    return PodCheckinsResponse.from_view(view)


@router.get("/projects/{project_id}/progress", response_model=ProjectProgressResponse)
async def project_progress(
    project_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> ProjectProgressResponse:
    _ensure(principal, Capability.READ_PROJECT_PROGRESS)
    view = await persona_service.project_progress(principal.tenant_id, project_id, as_of)
    return ProjectProgressResponse.from_view(view)


@router.get("/programs/{program_id}/tree", response_model=ProgramTreeResponse)
async def program_tree(
    program_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> ProgramTreeResponse:
    _ensure(principal, Capability.READ_PROGRAM_ROLLUP)
    view = await persona_service.program_tree(principal.tenant_id, program_id, as_of)
    return ProgramTreeResponse.from_view(view)


@router.get("/portfolio/heatmap", response_model=PortfolioHeatmapResponse)
async def portfolio_heatmap(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
    program_root_id: Annotated[str | None, Query()] = None,
) -> PortfolioHeatmapResponse:
    _ensure(principal, Capability.READ_PORTFOLIO_HEATMAP)
    view = await persona_service.portfolio_heatmap(principal.tenant_id, as_of, program_root_id)
    return PortfolioHeatmapResponse.from_view(view)


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
