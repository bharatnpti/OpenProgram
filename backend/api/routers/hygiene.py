"""Jira hygiene: the board, the scan's run, a person's actions, and the configuration.

Reading a project's board keeps the readiness board's own rule
(core/application/readiness_access.py): the product owner, a manager, an admin,
an executive (each rule's counts only, never who) and a scrum master of a pod
working on the project; a developer reads none. A pod's board is for its scrum
master, a manager and an admin. Acting (Check now, waive, not applicable,
re-check, reopen) takes act_on_readiness, and a scrum master acts only on the
pods they run and the projects those pods work on. A finding of a blocking rule
is marked not applicable only by a manager or an admin. The rules and the
scan's switch are runtime config (manage_config). The tenant always comes from
the principal. Nothing here writes to Jira.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.dependencies import get_current_principal, get_forecast_service, get_registry
from api.hygiene_dtos import (
    HygieneBoardResponse,
    HygieneConfigResponse,
    HygieneFindingResponse,
    HygieneHistoryResponse,
    HygienePreviewRequest,
    HygienePreviewResponse,
    HygienePreviewScopeDto,
    HygieneReasonRequest,
    HygieneRuleDto,
    HygieneRunResponse,
    HygieneRunSummaryDto,
    HygieneSettingsDto,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.forecast_service import ForecastService
from core.application.hygiene_service import HygieneConflict, HygieneService
from core.application.readiness_access import pod_viewer, project_viewer, scope_viewer
from core.application.release_readiness_service import ReleaseReadinessService, Viewer
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.forecast import Release
from core.domain.hygiene import HygieneError, HygieneFinding
from infra.registry import ServiceRegistry

router = APIRouter(tags=["hygiene"])


def get_hygiene_service(
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> HygieneService:
    return registry.hygiene_service()


def get_readiness_service(
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> ReleaseReadinessService:
    return registry.release_readiness_service()


Service = Annotated[HygieneService, Depends(get_hygiene_service)]
Readiness = Annotated[ReleaseReadinessService, Depends(get_readiness_service)]
Forecasts = Annotated[ForecastService, Depends(get_forecast_service)]
Caller = Annotated[Principal, Depends(get_current_principal)]


# ---- the board -------------------------------------------------------------------------


@router.get("/projects/{project_id}/hygiene", response_model=HygieneBoardResponse)
async def project_hygiene(
    project_id: str,
    principal: Caller,
    service: Service,
    readiness: Readiness,
    forecasts: Forecasts,
    release_id: Annotated[str | None, Query()] = None,
    pod_id: Annotated[str | None, Query()] = None,
) -> HygieneBoardResponse:
    viewer = await _project_viewer(principal, forecasts, readiness, project_id, reading=True)
    release = await _release(forecasts, principal, project_id, release_id)
    try:
        view = await service.project_board(
            principal.tenant_id, project_id, viewer, release=release, pod_id=pod_id or None
        )
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return HygieneBoardResponse.from_view(view)


@router.get("/pods/{pod_id}/hygiene", response_model=HygieneBoardResponse)
async def pod_hygiene(
    pod_id: str, principal: Caller, service: Service, forecasts: Forecasts
) -> HygieneBoardResponse:
    viewer = await _pod_viewer(principal, forecasts, pod_id)
    try:
        view = await service.pod_board(principal.tenant_id, pod_id, viewer)
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return HygieneBoardResponse.from_view(view)


@router.post("/projects/{project_id}/hygiene/run", response_model=HygieneRunResponse)
async def run_project(
    project_id: str,
    principal: Caller,
    service: Service,
    readiness: Readiness,
    forecasts: Forecasts,
    release_id: Annotated[str | None, Query()] = None,
    pod_id: Annotated[str | None, Query()] = None,
) -> HygieneRunResponse:
    """Check now: the project, its releases and its pods, inline. Reads only synced data."""
    viewer = await _project_viewer(principal, forecasts, readiness, project_id, reading=False)
    release = await _release(forecasts, principal, project_id, release_id)
    try:
        summary = await service.run_project(principal.tenant_id, project_id, viewer)
        view = await service.project_board(
            principal.tenant_id, project_id, viewer, release=release, pod_id=pod_id or None
        )
    except (GraphNotFound, HygieneConflict, AuthorizationDenied) as exc:
        raise _error(exc) from exc
    return HygieneRunResponse(
        run=HygieneRunSummaryDto.from_domain(summary),
        board=HygieneBoardResponse.from_view(view),
    )


# ---- a person's actions ----------------------------------------------------------------


@router.post("/hygiene-findings/{finding_id}/waive", response_model=HygieneFindingResponse)
async def waive(
    finding_id: str,
    request: HygieneReasonRequest,
    principal: Caller,
    service: Service,
    readiness: Readiness,
    forecasts: Forecasts,
) -> HygieneFindingResponse:
    viewer = await _finding_viewer(principal, forecasts, readiness, service, finding_id)
    return await _respond(
        service,
        principal,
        viewer,
        service.waive(principal.tenant_id, finding_id, viewer, reason=request.reason),
    )


@router.post("/hygiene-findings/{finding_id}/not-applicable", response_model=HygieneFindingResponse)
async def not_applicable(
    finding_id: str,
    request: HygieneReasonRequest,
    principal: Caller,
    service: Service,
    readiness: Readiness,
    forecasts: Forecasts,
) -> HygieneFindingResponse:
    viewer = await _finding_viewer(principal, forecasts, readiness, service, finding_id)
    return await _respond(
        service,
        principal,
        viewer,
        service.not_applicable(principal.tenant_id, finding_id, viewer, reason=request.reason),
    )


@router.post("/hygiene-findings/{finding_id}/reopen", response_model=HygieneFindingResponse)
async def reopen(
    finding_id: str,
    principal: Caller,
    service: Service,
    readiness: Readiness,
    forecasts: Forecasts,
) -> HygieneFindingResponse:
    viewer = await _finding_viewer(principal, forecasts, readiness, service, finding_id)
    return await _respond(
        service, principal, viewer, service.reopen(principal.tenant_id, finding_id, viewer)
    )


@router.post("/hygiene-findings/{finding_id}/recheck", response_model=HygieneFindingResponse)
async def recheck(
    finding_id: str,
    principal: Caller,
    service: Service,
    readiness: Readiness,
    forecasts: Forecasts,
) -> HygieneFindingResponse:
    """Read the issue again as the last Jira sync left it, after a fix in Jira."""
    viewer = await _finding_viewer(principal, forecasts, readiness, service, finding_id)
    return await _respond(
        service, principal, viewer, service.recheck(principal.tenant_id, finding_id, viewer)
    )


@router.get("/hygiene-findings/{finding_id}/history", response_model=HygieneHistoryResponse)
async def history(
    finding_id: str,
    principal: Caller,
    service: Service,
    readiness: Readiness,
    forecasts: Forecasts,
) -> HygieneHistoryResponse:
    viewer = await _finding_viewer(
        principal, forecasts, readiness, service, finding_id, reading=True
    )
    if not viewer.sees_drafts:
        # An executive reads counts, never who.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A finding's history is read by the people who act on it.",
        )
    try:
        actions = await service.history(principal.tenant_id, finding_id)
    except GraphNotFound as exc:
        raise _error(exc) from exc
    names = await readiness.actor_names(principal.tenant_id, [item.actor for item in actions])
    return HygieneHistoryResponse.from_domain(actions, names)


# ---- configuration ---------------------------------------------------------------------


@router.get("/config/hygiene", response_model=HygieneConfigResponse)
async def hygiene_config(principal: Caller, service: Service) -> HygieneConfigResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return HygieneConfigResponse.from_view(await service.config(principal.tenant_id))


@router.put("/config/hygiene/settings", response_model=HygieneSettingsDto)
async def save_settings(
    request: HygieneSettingsDto, principal: Caller, service: Service
) -> HygieneSettingsDto:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        saved = await service.save_settings(
            request.to_domain(principal.tenant_id), actor=principal.subject
        )
    except HygieneError as exc:
        raise _error(exc) from exc
    return HygieneSettingsDto.from_domain(saved)


@router.put("/config/hygiene/rules", response_model=HygieneRuleDto)
async def save_rule(request: HygieneRuleDto, principal: Caller, service: Service) -> HygieneRuleDto:
    """Create a rule (empty id, or an example's id) or replace the one with that id."""
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        saved = await service.save_rule(
            request.to_domain(principal.tenant_id), actor=principal.subject
        )
    except (HygieneError, GraphNotFound) as exc:
        raise _error(exc) from exc
    return HygieneRuleDto.from_domain(saved)


@router.delete("/config/hygiene/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_rule(rule_id: str, principal: Caller, service: Service) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.remove_rule(principal.tenant_id, rule_id, actor=principal.subject)
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/config/hygiene/rules/preview", response_model=HygienePreviewResponse)
async def preview_rule(
    request: HygienePreviewRequest, principal: Caller, service: Service
) -> HygienePreviewResponse:
    """Try on a project: what the scan would find with this rule. Writes nothing."""
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        rows = await service.preview(
            request.rule.to_domain(principal.tenant_id), request.project_id
        )
    except (HygieneError, GraphNotFound) as exc:
        raise _error(exc) from exc
    return HygienePreviewResponse(scopes=[HygienePreviewScopeDto.from_domain(row) for row in rows])


# ---- who may ---------------------------------------------------------------------------


async def _project_viewer(
    principal: Principal,
    forecasts: ForecastService,
    readiness: ReleaseReadinessService,
    project_id: str,
    *,
    reading: bool,
) -> Viewer:
    try:
        return await project_viewer(principal, forecasts, readiness, project_id, reading=reading)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


async def _pod_viewer(principal: Principal, forecasts: ForecastService, pod_id: str) -> Viewer:
    try:
        return await pod_viewer(principal, forecasts, pod_id)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


async def _finding_viewer(
    principal: Principal,
    forecasts: ForecastService,
    readiness: ReleaseReadinessService,
    service: HygieneService,
    finding_id: str,
    *,
    reading: bool = False,
) -> Viewer:
    try:
        scope, project_id = await service.scope_of_finding(principal.tenant_id, finding_id)
        return await scope_viewer(
            principal, forecasts, readiness, scope, project_id, reading=reading
        )
    except GraphNotFound as exc:
        raise _error(exc) from exc
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


async def _release(
    forecasts: ForecastService, principal: Principal, project_id: str, release_id: str | None
) -> Release | None:
    if not release_id:
        return None
    try:
        release = await forecasts.release(principal.tenant_id, release_id)
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if release.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such release.")
    return release


async def _respond(
    service: HygieneService,
    principal: Principal,
    viewer: Viewer,
    action: Awaitable[HygieneFinding],
) -> HygieneFindingResponse:
    try:
        finding = await action
        view = await service.finding_view(principal.tenant_id, finding, viewer)
    except (GraphNotFound, AuthorizationDenied, HygieneConflict, HygieneError) as exc:
        raise _error(exc) from exc
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="The finding is no longer shown."
        )
    return HygieneFindingResponse.from_view(view)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, GraphNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, AuthorizationDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, HygieneConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
