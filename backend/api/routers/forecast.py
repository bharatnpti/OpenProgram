"""Committed delivery dates, releases, and their forecasts.

Reading a project's delivery needs read_project_progress. A project's or a
release's date, and the releases themselves, need set_project_dates (the
product owner and manager). A pod's date needs set_pod_dates; a scrum master
sets it only for a pod they run (its scrum master contact, or a member). The
tenant always comes from the principal.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.dependencies import get_current_principal, get_forecast_service
from api.dtos import (
    CommitmentResponse,
    DeliveryDateRequest,
    ForecastHistoryResponse,
    PodDeliveryResponse,
    PodProjectDeliveryResponse,
    ProjectDeliveryResponse,
    ReleaseCandidateResponse,
    ReleaseRequest,
    ReleaseResponse,
    ScopeDeliveryResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.forecast_service import MAX_FORECAST_HISTORY_DAYS, ForecastService
from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied, GraphNotFound, OpenProgramError
from core.domain.forecast import (
    HISTORY_DAYS,
    CommitmentError,
    CommitmentScope,
    CommitmentScopeKind,
    ReleaseMatch,
)

router = APIRouter(tags=["delivery"])


@router.get("/projects/{project_id}/delivery", response_model=ProjectDeliveryResponse)
async def project_delivery(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
) -> ProjectDeliveryResponse:
    _ensure(principal, Capability.READ_PROJECT_PROGRESS)
    try:
        view = await service.project_delivery(principal.tenant_id, project_id, as_of)
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProjectDeliveryResponse.from_view(view)


@router.get("/projects/{project_id}/delivery/history", response_model=ForecastHistoryResponse)
async def project_forecast_history(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
    days: Annotated[int, Query(ge=1, le=MAX_FORECAST_HISTORY_DAYS)] = HISTORY_DAYS,
    release_id: Annotated[str | None, Query(description="One release of the project.")] = None,
) -> ForecastHistoryResponse:
    """The history forecast (p50, p85) as it stood on each day with a snapshot, oldest
    first, for the project or one release: what the delivery read said on that day."""
    _ensure(principal, Capability.READ_PROJECT_PROGRESS)
    try:
        view = await service.forecast_history(
            principal.tenant_id, project_id, as_of, release_id=release_id, days=days
        )
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ForecastHistoryResponse.from_view(view)


@router.put("/projects/{project_id}/delivery-date", response_model=CommitmentResponse)
async def set_project_date(
    project_id: str,
    request: DeliveryDateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
) -> CommitmentResponse:
    _ensure(principal, Capability.SET_PROJECT_DATES)
    scope = CommitmentScope(kind=CommitmentScopeKind.PROJECT, id=project_id, project_id=project_id)
    return await _set(service, principal, scope, request)


@router.put(
    "/projects/{project_id}/releases/{release_id}/delivery-date",
    response_model=CommitmentResponse,
)
async def set_release_date(
    project_id: str,
    release_id: str,
    request: DeliveryDateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
) -> CommitmentResponse:
    _ensure(principal, Capability.SET_PROJECT_DATES)
    scope = CommitmentScope(kind=CommitmentScopeKind.RELEASE, id=release_id, project_id=project_id)
    return await _set(service, principal, scope, request)


@router.put("/projects/{project_id}/pods/{pod_id}/delivery-date", response_model=CommitmentResponse)
async def set_pod_date(
    project_id: str,
    pod_id: str,
    request: DeliveryDateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
) -> CommitmentResponse:
    _ensure(principal, Capability.SET_POD_DATES)
    if not await _runs_pod(service, principal, pod_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the pod's own scrum master or a manager sets the pod's date.",
        )
    scope = CommitmentScope(kind=CommitmentScopeKind.POD, id=pod_id, project_id=project_id)
    return await _set(service, principal, scope, request)


@router.get("/pods/{pod_id}/delivery", response_model=PodDeliveryResponse)
async def pod_delivery(
    pod_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
) -> PodDeliveryResponse:
    policy = AuthorizationPolicy()
    if not (
        policy.can(principal, Capability.SET_POD_DATES)
        or policy.can(principal, Capability.READ_PROJECT_PROGRESS)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed.")
    try:
        projects = await service.pod_projects(principal.tenant_id, pod_id, as_of)
        items: list[PodProjectDeliveryResponse] = []
        for project in projects:
            view = await service.project_delivery(principal.tenant_id, project.id, as_of)
            slice_view = next(pod for pod in view.pods if pod.scope.id == pod_id)
            items.append(
                PodProjectDeliveryResponse(
                    project_id=project.id,
                    project_name=project.name,
                    project_target=view.project.target,
                    pod=ScopeDeliveryResponse.from_view(slice_view),
                )
            )
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PodDeliveryResponse(
        pod_id=pod_id,
        can_set_dates=policy.can(principal, Capability.SET_POD_DATES)
        and await _runs_pod(service, principal, pod_id),
        projects=items,
    )


@router.get("/projects/{project_id}/releases", response_model=list[ReleaseResponse])
async def list_releases(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
) -> list[ReleaseResponse]:
    _ensure(principal, Capability.READ_PROJECT_PROGRESS)
    try:
        releases = await service.releases(principal.tenant_id, project_id)
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [ReleaseResponse.from_domain(release) for release in releases]


@router.get(
    "/projects/{project_id}/release-candidates", response_model=list[ReleaseCandidateResponse]
)
async def release_candidates(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
) -> list[ReleaseCandidateResponse]:
    """The fix versions and labels the project's requirements carry, to define releases from."""
    _ensure(principal, Capability.SET_PROJECT_DATES)
    try:
        candidates = await service.release_candidates(principal.tenant_id, project_id)
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [
        ReleaseCandidateResponse(
            kind=item.kind, value=item.value, issues=item.issues, release_date=item.release_date
        )
        for item in candidates
    ]


@router.post(
    "/projects/{project_id}/releases",
    response_model=ReleaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_release(
    project_id: str,
    request: ReleaseRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
) -> ReleaseResponse:
    _ensure(principal, Capability.SET_PROJECT_DATES)
    return await _save_release(service, principal, project_id, None, request)


@router.put("/projects/{project_id}/releases/{release_id}", response_model=ReleaseResponse)
async def update_release(
    project_id: str,
    release_id: str,
    request: ReleaseRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
) -> ReleaseResponse:
    _ensure(principal, Capability.SET_PROJECT_DATES)
    return await _save_release(service, principal, project_id, release_id, request)


@router.delete(
    "/projects/{project_id}/releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_release(
    project_id: str,
    release_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ForecastService, Depends(get_forecast_service)],
) -> Response:
    _ensure(principal, Capability.SET_PROJECT_DATES)
    try:
        release = await service.release(principal.tenant_id, release_id)
    except GraphNotFound:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if release.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such release.")
    await service.remove_release(principal.tenant_id, release_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _set(
    service: ForecastService,
    principal: Principal,
    scope: CommitmentScope,
    request: DeliveryDateRequest,
) -> CommitmentResponse:
    try:
        commitment = await service.set_date(
            principal.tenant_id,
            scope,
            request.target_date,
            note=request.note,
            actor=principal.subject,
        )
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CommitmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return CommitmentResponse.from_domain(commitment)


async def _save_release(
    service: ForecastService,
    principal: Principal,
    project_id: str,
    release_id: str | None,
    request: ReleaseRequest,
) -> ReleaseResponse:
    try:
        release = await service.save_release(
            principal.tenant_id,
            project_id,
            release_id=release_id,
            name=request.name,
            match=ReleaseMatch(kind=request.match_kind, value=request.match_value),
            actor=principal.subject,
        )
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OpenProgramError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return ReleaseResponse.from_domain(release)


async def _runs_pod(service: ForecastService, principal: Principal, pod_id: str) -> bool:
    """A manager or admin sets any pod's date; a scrum master only their own pod's."""
    if principal.has_role(Role.ADMIN) or principal.has_role(Role.MGR):
        return True
    return await service.runs_pod(principal.tenant_id, pod_id, principal.subject, date.today())


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
