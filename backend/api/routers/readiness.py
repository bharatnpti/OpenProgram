"""Release readiness: the board, the agent's run, a person's actions, and the configuration.

Reading a project's board takes read_project_progress (product owner, manager,
executive, admin), or a scrum master of a pod working on the project; a
developer has nothing to act on and reads none. A pod's board is for its
scrum master, a manager and an admin. Acting (run now, link, not applicable,
dismiss, edit, create, reopen) takes act_on_readiness, and a scrum master acts
only on the pods they run and the projects those pods work on. A blocking
criterion is marked not applicable only by a manager or an admin. The
criteria and the agent's switches are runtime config (manage_config). The
tenant always comes from the principal.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.dependencies import get_current_principal, get_forecast_service, get_registry
from api.readiness_dtos import (
    ReadinessBoardResponse,
    ReadinessConfigResponse,
    ReadinessCreateRequest,
    ReadinessCreateResponse,
    ReadinessCriterionDto,
    ReadinessDraftUpdateRequest,
    ReadinessFindingResponse,
    ReadinessHistoryResponse,
    ReadinessLinkRequest,
    ReadinessPreviewRequest,
    ReadinessPreviewResponse,
    ReadinessPreviewRowResponse,
    ReadinessReasonRequest,
    ReadinessRunResponse,
    ReadinessRunSummaryDto,
    ReadinessSettingsDto,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.delivery_scope import PROJECT_OUTSIDE_SCOPE
from core.application.forecast_service import ForecastService
from core.application.release_readiness_service import (
    FindingView,
    ReadinessConflict,
    ReleaseReadinessService,
    Viewer,
    create_failure_words,
)
from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied, GraphNotFound, IssueCreateFailed
from core.domain.forecast import Release
from core.domain.release_readiness import ReadinessError, ScopeKind, ScopeRef
from infra.registry import ServiceRegistry

router = APIRouter(tags=["readiness"])

_NOT_A_READER = "Release readiness is read by the people who decide on a release."
_NOT_YOURS = "You can act on readiness only for pods you run and the projects they work on."


def get_readiness_service(
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> ReleaseReadinessService:
    return registry.release_readiness_service()


Service = Annotated[ReleaseReadinessService, Depends(get_readiness_service)]
Forecasts = Annotated[ForecastService, Depends(get_forecast_service)]
Caller = Annotated[Principal, Depends(get_current_principal)]


# ---- the board -------------------------------------------------------------------------


@router.get("/projects/{project_id}/readiness", response_model=ReadinessBoardResponse)
async def project_readiness(
    project_id: str,
    principal: Caller,
    service: Service,
    forecasts: Forecasts,
    release_id: Annotated[str | None, Query()] = None,
) -> ReadinessBoardResponse:
    viewer = await _project_viewer(principal, forecasts, service, project_id, reading=True)
    release = await _release(forecasts, principal, project_id, release_id)
    try:
        view = await service.project_board(principal.tenant_id, project_id, viewer, release=release)
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return ReadinessBoardResponse.from_view(view)


@router.get("/pods/{pod_id}/readiness", response_model=ReadinessBoardResponse)
async def pod_readiness(
    pod_id: str, principal: Caller, service: Service, forecasts: Forecasts
) -> ReadinessBoardResponse:
    viewer = await _pod_viewer(principal, forecasts, pod_id)
    try:
        view = await service.pod_board(principal.tenant_id, pod_id, viewer)
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return ReadinessBoardResponse.from_view(view)


@router.post("/projects/{project_id}/readiness/run", response_model=ReadinessRunResponse)
async def run_project(
    project_id: str,
    principal: Caller,
    service: Service,
    forecasts: Forecasts,
    release_id: Annotated[str | None, Query()] = None,
) -> ReadinessRunResponse:
    viewer = await _project_viewer(principal, forecasts, service, project_id, reading=False)
    release = await _release(forecasts, principal, project_id, release_id)
    try:
        summary = await service.run_project(principal.tenant_id, project_id, viewer)
        view = await service.project_board(principal.tenant_id, project_id, viewer, release=release)
    except (GraphNotFound, ReadinessConflict, AuthorizationDenied) as exc:
        raise _error(exc) from exc
    return ReadinessRunResponse(
        run=ReadinessRunSummaryDto.from_domain(summary),
        board=ReadinessBoardResponse.from_view(view),
    )


@router.post("/pods/{pod_id}/readiness/run", response_model=ReadinessRunResponse)
async def run_pod(
    pod_id: str, principal: Caller, service: Service, forecasts: Forecasts
) -> ReadinessRunResponse:
    viewer = await _pod_viewer(principal, forecasts, pod_id)
    try:
        summary = await service.run_pod(principal.tenant_id, pod_id, viewer)
        view = await service.pod_board(principal.tenant_id, pod_id, viewer)
    except (GraphNotFound, ReadinessConflict, AuthorizationDenied) as exc:
        raise _error(exc) from exc
    return ReadinessRunResponse(
        run=ReadinessRunSummaryDto.from_domain(summary),
        board=ReadinessBoardResponse.from_view(view),
    )


# ---- a person's actions ----------------------------------------------------------------


@router.post("/readiness-findings/{finding_id}/link", response_model=ReadinessFindingResponse)
async def link(
    finding_id: str,
    request: ReadinessLinkRequest,
    principal: Caller,
    service: Service,
    forecasts: Forecasts,
) -> ReadinessFindingResponse:
    viewer = await _finding_viewer(principal, forecasts, service, finding_id)
    return await _respond(
        service,
        principal,
        service.link(
            principal.tenant_id,
            finding_id,
            viewer,
            issue_key=(request.issue_key or "").strip() or None,
            url=(request.evidence_url or "").strip() or None,
            note=request.note,
        ),
    )


@router.post(
    "/readiness-findings/{finding_id}/not-applicable", response_model=ReadinessFindingResponse
)
async def not_applicable(
    finding_id: str,
    request: ReadinessReasonRequest,
    principal: Caller,
    service: Service,
    forecasts: Forecasts,
) -> ReadinessFindingResponse:
    viewer = await _finding_viewer(principal, forecasts, service, finding_id)
    return await _respond(
        service,
        principal,
        service.not_applicable(principal.tenant_id, finding_id, viewer, reason=request.reason),
    )


@router.post("/readiness-findings/{finding_id}/reopen", response_model=ReadinessFindingResponse)
async def reopen(
    finding_id: str, principal: Caller, service: Service, forecasts: Forecasts
) -> ReadinessFindingResponse:
    viewer = await _finding_viewer(principal, forecasts, service, finding_id)
    return await _respond(
        service, principal, service.reopen(principal.tenant_id, finding_id, viewer)
    )


@router.post("/readiness-findings/{finding_id}/draft", response_model=ReadinessFindingResponse)
async def draft_now(
    finding_id: str, principal: Caller, service: Service, forecasts: Forecasts
) -> ReadinessFindingResponse:
    """Draft an issue on request, for a tenant whose agent drafts none by itself."""
    viewer = await _finding_viewer(principal, forecasts, service, finding_id)
    return await _respond(
        service, principal, service.draft_now(principal.tenant_id, finding_id, viewer)
    )


@router.get("/readiness-findings/{finding_id}/history", response_model=ReadinessHistoryResponse)
async def history(
    finding_id: str, principal: Caller, service: Service, forecasts: Forecasts
) -> ReadinessHistoryResponse:
    viewer = await _finding_viewer(principal, forecasts, service, finding_id, reading=True)
    try:
        scope, _project = await service.scope_of_finding(principal.tenant_id, finding_id)
        if not viewer.sees(scope):
            raise AuthorizationDenied(_NOT_YOURS)
        actions = await service.history(principal.tenant_id, finding_id)
    except (GraphNotFound, AuthorizationDenied) as exc:
        raise _error(exc) from exc
    names = await service.actor_names(principal.tenant_id, (item.actor for item in actions))
    return ReadinessHistoryResponse.from_domain(finding_id, actions, names)


@router.put("/readiness-suggestions/{suggestion_id}", response_model=ReadinessFindingResponse)
async def edit_draft(
    suggestion_id: str,
    request: ReadinessDraftUpdateRequest,
    principal: Caller,
    service: Service,
    forecasts: Forecasts,
) -> ReadinessFindingResponse:
    viewer = await _suggestion_viewer(principal, forecasts, service, suggestion_id)
    return await _respond(
        service,
        principal,
        service.edit_draft(
            principal.tenant_id,
            suggestion_id,
            viewer,
            version=request.version,
            draft=request.draft.to_domain(),
        ),
    )


@router.post(
    "/readiness-suggestions/{suggestion_id}/dismiss", response_model=ReadinessFindingResponse
)
async def dismiss(
    suggestion_id: str,
    request: ReadinessReasonRequest,
    principal: Caller,
    service: Service,
    forecasts: Forecasts,
) -> ReadinessFindingResponse:
    viewer = await _suggestion_viewer(principal, forecasts, service, suggestion_id)
    return await _respond(
        service,
        principal,
        service.dismiss(principal.tenant_id, suggestion_id, viewer, reason=request.reason),
    )


@router.post(
    "/readiness-suggestions/{suggestion_id}/create",
    response_model=ReadinessCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": ReadinessCreateResponse, "description": "Already created."}},
)
async def create(
    suggestion_id: str,
    request: ReadinessCreateRequest,
    response: Response,
    principal: Caller,
    service: Service,
    forecasts: Forecasts,
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> ReadinessCreateResponse:
    """Create one approved draft in Jira. 201 when created, 200 when it already was."""
    viewer = await _suggestion_viewer(principal, forecasts, service, suggestion_id)
    try:
        result = await service.create(
            principal.tenant_id, suggestion_id, viewer, version=request.version
        )
    except IssueCreateFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=create_failure_words(exc)
        ) from exc
    except (GraphNotFound, AuthorizationDenied, ReadinessConflict, ReadinessError) as exc:
        raise _error(exc) from exc
    if not result.created:
        response.status_code = status.HTTP_200_OK
    base = (registry.settings.jira_base_url or "").rstrip("/")
    names = await service.actor_names(principal.tenant_id, _actors(result.finding))
    return ReadinessCreateResponse(
        issue_key=result.issue_key,
        url=f"{base}/browse/{result.issue_key}" if base else None,
        created=result.created,
        finding=ReadinessFindingResponse.from_view(result.finding, names),
    )


# ---- configuration ---------------------------------------------------------------------


@router.get("/config/readiness", response_model=ReadinessConfigResponse)
async def readiness_config(principal: Caller, service: Service) -> ReadinessConfigResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ReadinessConfigResponse.from_view(await service.config(principal.tenant_id))


@router.put("/config/readiness/settings", response_model=ReadinessSettingsDto)
async def save_settings(
    request: ReadinessSettingsDto, principal: Caller, service: Service
) -> ReadinessSettingsDto:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        saved = await service.save_settings(
            request.to_domain(principal.tenant_id), actor=principal.subject
        )
    except ReadinessError as exc:
        raise _error(exc) from exc
    return ReadinessSettingsDto.from_domain(saved)


@router.put("/config/readiness/criteria", response_model=ReadinessCriterionDto)
async def save_criterion(
    request: ReadinessCriterionDto, principal: Caller, service: Service
) -> ReadinessCriterionDto:
    """Create a criterion (empty id, or an example's id) or replace the one with that id."""
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        saved = await service.save_criterion(
            request.to_domain(principal.tenant_id), actor=principal.subject
        )
    except (ReadinessError, GraphNotFound) as exc:
        raise _error(exc) from exc
    return ReadinessCriterionDto.from_domain(saved)


@router.delete("/config/readiness/criteria/{criterion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_criterion(criterion_id: str, principal: Caller, service: Service) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.remove_criterion(principal.tenant_id, criterion_id, actor=principal.subject)
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/config/readiness/criteria/preview", response_model=ReadinessPreviewResponse)
async def preview_criterion(
    request: ReadinessPreviewRequest, principal: Caller, service: Service
) -> ReadinessPreviewResponse:
    """What the agent would find on one project with this criterion. Writes nothing."""
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        rows = await service.preview(
            request.criterion.to_domain(principal.tenant_id), request.project_id
        )
    except (ReadinessError, GraphNotFound) as exc:
        raise _error(exc) from exc
    return ReadinessPreviewResponse(
        rows=[ReadinessPreviewRowResponse.from_domain(row) for row in rows]
    )


# ---- who may ---------------------------------------------------------------------------


def _broad(principal: Principal) -> bool:
    """Reaches every project and pod: an admin, a manager or a product owner."""
    return any(principal.has_role(role) for role in (Role.ADMIN, Role.MGR, Role.PO))


async def _project_viewer(
    principal: Principal,
    forecasts: ForecastService,
    service: ReleaseReadinessService,
    project_id: str,
    *,
    reading: bool,
) -> Viewer:
    policy = AuthorizationPolicy()
    may_act = policy.can(principal, Capability.ACT_ON_READINESS)
    if _broad(principal):
        return Viewer(subject=principal.subject, roles=principal.roles, may_act=may_act)
    if principal.has_role(Role.SM):
        runs = await forecasts.runs_project_pod(principal.tenant_id, project_id, principal.subject)
        if runs:
            pods = await service.pods_run_by(principal.tenant_id, project_id, principal.subject)
            return Viewer(
                subject=principal.subject,
                roles=principal.roles,
                may_act=may_act,
                pods=frozenset(pods),
                project_reach=True,
            )
    if reading and policy.can(principal, Capability.READ_PROJECT_PROGRESS):
        # An executive reads the project's and releases' rows; nothing to act on,
        # no drafts, and no pod's rows.
        return Viewer(
            subject=principal.subject,
            roles=principal.roles,
            may_act=False,
            pods=frozenset(),
            project_reach=False,
            sees_drafts=False,
        )
    if reading and principal.has_role(Role.SM):
        # A read, in the words of every scoped project read (delivery_scope).
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_OUTSIDE_SCOPE)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=_NOT_YOURS if may_act else _NOT_A_READER,
    )


async def _pod_viewer(principal: Principal, forecasts: ForecastService, pod_id: str) -> Viewer:
    policy = AuthorizationPolicy()
    may_act = policy.can(principal, Capability.ACT_ON_READINESS)
    if principal.has_role(Role.ADMIN) or principal.has_role(Role.MGR):
        return Viewer(subject=principal.subject, roles=principal.roles, may_act=may_act)
    if principal.has_role(Role.SM) and await forecasts.runs_pod(
        principal.tenant_id, pod_id, principal.subject, date.today()
    ):
        return Viewer(
            subject=principal.subject,
            roles=principal.roles,
            may_act=may_act,
            pods=frozenset({pod_id}),
            project_reach=False,
        )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_YOURS)


async def _finding_viewer(
    principal: Principal,
    forecasts: ForecastService,
    service: ReleaseReadinessService,
    finding_id: str,
    *,
    reading: bool = False,
) -> Viewer:
    try:
        scope, project_id = await service.scope_of_finding(principal.tenant_id, finding_id)
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return await _scope_viewer(principal, forecasts, service, scope, project_id, reading=reading)


async def _suggestion_viewer(
    principal: Principal,
    forecasts: ForecastService,
    service: ReleaseReadinessService,
    suggestion_id: str,
) -> Viewer:
    try:
        scope, project_id = await service.scope_of_suggestion(principal.tenant_id, suggestion_id)
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return await _scope_viewer(principal, forecasts, service, scope, project_id, reading=False)


async def _scope_viewer(
    principal: Principal,
    forecasts: ForecastService,
    service: ReleaseReadinessService,
    scope: ScopeRef,
    project_id: str | None,
    *,
    reading: bool,
) -> Viewer:
    if scope.kind is ScopeKind.POD:
        if _broad(principal):
            may_act = AuthorizationPolicy().can(principal, Capability.ACT_ON_READINESS)
            return Viewer(subject=principal.subject, roles=principal.roles, may_act=may_act)
        return await _pod_viewer(principal, forecasts, scope.id)
    return await _project_viewer(principal, forecasts, service, project_id or "", reading=reading)


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
    service: ReleaseReadinessService, principal: Principal, action: Awaitable[FindingView]
) -> ReadinessFindingResponse:
    try:
        view = await action
    except (GraphNotFound, AuthorizationDenied, ReadinessConflict, ReadinessError) as exc:
        raise _error(exc) from exc
    names = await service.actor_names(principal.tenant_id, _actors(view))
    return ReadinessFindingResponse.from_view(view, names)


def _actors(view: FindingView) -> list[str]:
    suggestion = view.suggestion
    return [
        actor
        for actor in (
            view.finding.person.by if view.finding.person else None,
            suggestion.dismissed_by if suggestion else None,
            suggestion.created_by if suggestion else None,
        )
        if actor
    ]


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, GraphNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, AuthorizationDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ReadinessConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
