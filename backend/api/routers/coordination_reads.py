"""Requests and pods, the reads lane R adds: a request's trail, the board as counts, the
resolved asks, and a project's check-in counts.

- ``GET /cross-person-requests/{id}/trail``: what happened to an ask, and what
  is due (D91). For its requester, the person asked, and the board's readers;
  an executive reads it with no one named.
- ``GET /cross-person-requests/counts``: the board as counts, for every reader
  of it; an executive's whole view of it (D87).
- ``GET /cross-person-requests/resolved``: the asks resolved lately, for the
  board's readers who read names.
- ``GET /projects/{id}/checkin-coverage``: the day's check-ins across the
  project's people as counts, for the project's readers.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.dependencies import (
    get_current_principal,
    get_delivery_scope_service,
    get_person_names,
    get_persona_view_service,
    get_registry,
)
from api.dtos import CrossPersonRequestResponse, CrossPersonRequestsResponse
from api.pod_checkin_dtos import ProjectCheckinCoverageResponse
from api.request_trail_dtos import RequestCountsResponse, RequestTrailResponse
from core.application.delivery_scope import DeliveryScopeService
from core.application.people_words import is_small
from core.application.persona_views import PersonaViewService
from core.application.portfolio_privacy import reads_people
from core.application.request_trail_service import (
    RESOLVED_WINDOW_DAYS,
    RequestNotFound,
    RequestTrailService,
)
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound

router = APIRouter(tags=["coordination"])


def get_request_trail_service(request: Request) -> RequestTrailService:
    registry = get_registry(request)
    return RequestTrailService(
        requests=registry.cross_person_request_repository(),
        facts=registry.time_series_repository(),
        graph=registry.graph_repository(),
        builder=registry.day_report_builder(),
        escalation=registry.escalation_matrix_service(),
        reports=registry.day_report_repository(),
        names=get_person_names(request),
    )


@router.get("/cross-person-requests/counts", response_model=RequestCountsResponse)
async def cross_person_request_counts(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[RequestTrailService, Depends(get_request_trail_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
) -> RequestCountsResponse:
    try:
        view = await service.counts(principal, as_of)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return RequestCountsResponse.from_view(view)


@router.get("/cross-person-requests/resolved", response_model=CrossPersonRequestsResponse)
async def resolved_cross_person_requests(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[RequestTrailService, Depends(get_request_trail_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
    days: Annotated[int, Query(ge=1, le=RESOLVED_WINDOW_DAYS * 3)] = RESOLVED_WINDOW_DAYS,
) -> CrossPersonRequestsResponse:
    """Asks resolved in the ``days`` days up to ``as_of``, newest first; never dismissed copies."""
    try:
        requests = await service.resolved(principal, as_of, days)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return CrossPersonRequestsResponse(
        requests=[CrossPersonRequestResponse.from_domain(request) for request in requests],
    )


@router.get("/cross-person-requests/{request_id}/trail", response_model=RequestTrailResponse)
async def cross_person_request_trail(
    request_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[RequestTrailService, Depends(get_request_trail_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
) -> RequestTrailResponse:
    try:
        view = await service.trail(principal, request_id, as_of)
    except RequestNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return RequestTrailResponse.from_view(view)


@router.get(
    "/projects/{project_id}/checkin-coverage", response_model=ProjectCheckinCoverageResponse
)
async def project_checkin_coverage(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
    scope: Annotated[DeliveryScopeService, Depends(get_delivery_scope_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
) -> ProjectCheckinCoverageResponse:
    """Counts only, no one named: every reader of the project's progress gets them.

    A reader who counts people only (an executive) gets none for a project of
    fewer than five people (D104): its dates and verdict are on its page.
    """
    try:
        await scope.ensure_project_read(principal, project_id)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        view = await persona_service.project_checkin_coverage(
            principal.tenant_id, project_id, as_of
        )
    except GraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response = ProjectCheckinCoverageResponse.from_view(view)
    if not reads_people(principal) and is_small(view.people):
        return response.model_copy(update={"people": None, "coverage": None})
    return response
