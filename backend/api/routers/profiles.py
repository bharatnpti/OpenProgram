"""Profiles per pod membership.

Reads: the catalog (everyone: labels only), a pod's people with their profiles
(whoever reads the pod's panel), and the caller's own profiles.

Writes, and the reads behind them (a membership's suggestion, the review list):
an admin in every pod, a scrum master in the pods they run. A profile grants
no rights; it only routes who is asked, named and escalated to.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_current_principal, get_delivery_scope_service, get_profile_service
from api.profile_dtos import (
    AcceptProfilesRequest,
    AcceptProfilesResponse,
    MemberProfileResponse,
    MembershipProfileResponse,
    PodProfilesResponse,
    ProfileResponse,
    ProfileReviewItemResponse,
    ProfileReviewResponse,
    ProfileUpdateRequest,
    SetProfileRequest,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.delivery_scope import DeliveryScopeService
from core.application.profile_service import NOT_A_SETTER, AcceptChoice, ProfileService
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.profiles import ProfileConflict, ProfileDefinition, ProfileError, ProfileSource

router = APIRouter(tags=["profiles"])


@router.get("/profiles", response_model=list[ProfileResponse])
async def list_profiles(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
) -> list[ProfileResponse]:
    """The tenant's profile catalog: the generic defaults with its own changes."""
    _ensure(principal, Capability.READ_DIRECTORY)
    return [
        ProfileResponse.from_domain(entry) for entry in await service.catalog(principal.tenant_id)
    ]


@router.put("/config/profiles/{slug}", response_model=ProfileResponse)
async def save_profile(
    slug: str,
    request: ProfileUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
) -> ProfileResponse:
    """Rename, hide or add a catalog entry. Admins only."""
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        entry = await service.save_catalog_entry(
            principal.tenant_id,
            ProfileDefinition(
                slug=slug,
                label=request.label,
                code_work=request.code_work,
                hidden=request.hidden,
                title_words=tuple(request.title_words),
                sort=request.sort,
            ),
            by=principal.subject,
        )
    except ProfileConflict as exc:
        # Another of the tenant's profiles has the name, compared without case.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return ProfileResponse.from_domain(entry)


@router.get("/pods/{pod_id}/profiles", response_model=PodProfilesResponse)
async def pod_profiles(
    pod_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
    scope: Annotated[DeliveryScopeService, Depends(get_delivery_scope_service)],
) -> PodProfilesResponse:
    """The pod's people, each with their role and profile in it: the pod panel's readers."""
    try:
        await scope.ensure_pod_detail(principal, pod_id)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    try:
        view = await service.pod_profiles(principal.tenant_id, pod_id, as_of)
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PodProfilesResponse.from_view(view, can_edit=await service.can_set(principal, pod_id))


@router.get("/me/profiles", response_model=list[MemberProfileResponse])
async def my_profiles(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
) -> list[MemberProfileResponse]:
    """The caller's own profile in each pod they are in, to read."""
    _ensure(principal, Capability.READ_OWN_WORK)
    views = await service.member_profiles(principal.tenant_id, principal.subject, as_of)
    return [MemberProfileResponse.from_view(view) for view in views]


@router.get(
    "/config/pods/{pod_id}/members/{member_id}/profile",
    response_model=MembershipProfileResponse,
)
async def membership_profile(
    pod_id: str,
    member_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
    role: Annotated[str | None, Query(max_length=40)] = None,
) -> MembershipProfileResponse:
    """The membership's profile, its history, and a suggestion with its reasons.

    Also for someone not in the pod yet, so the add dialog can preselect one;
    ``role`` is the pod role they are about to get.
    """
    await _ensure_can_set(service, principal, pod_id)
    try:
        detail = await service.membership_detail(principal.tenant_id, pod_id, member_id, role=role)
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MembershipProfileResponse.from_detail(detail)


@router.put(
    "/config/pods/{pod_id}/members/{member_id}/profile",
    response_model=MemberProfileResponse,
)
async def set_membership_profile(
    pod_id: str,
    member_id: str,
    request: SetProfileRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
) -> MemberProfileResponse:
    """Change the member's profile in the pod from today; earlier days keep theirs."""
    await _ensure_can_set(service, principal, pod_id)
    try:
        view = await service.set_profile(
            principal.tenant_id,
            pod_id,
            member_id,
            request.profile,
            source=(
                ProfileSource.SUGGESTION_ACCEPTED
                if request.accepted_suggestion
                else ProfileSource.ADMIN
            ),
            by=principal.subject,
        )
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return MemberProfileResponse.from_view(view)


@router.get("/config/profiles/review", response_model=ProfileReviewResponse)
async def review_profiles(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
    pod_id: Annotated[str | None, Query()] = None,
) -> ProfileReviewResponse:
    """Profiles nobody checked yet (from the pod role or the backfill), with suggestions."""
    settable = await _settable(service, principal)
    if pod_id is not None:
        if settable is not None and pod_id not in settable:
            await _ensure_can_set(service, principal, pod_id)
        pods: tuple[str, ...] | None = (pod_id,)
    else:
        pods = settable
    items = await service.review(principal.tenant_id, pods)
    return ProfileReviewResponse(items=[ProfileReviewItemResponse.from_domain(i) for i in items])


@router.post("/config/profiles/review/accept", response_model=AcceptProfilesResponse)
async def accept_profiles(
    request: AcceptProfilesRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ProfileService, Depends(get_profile_service)],
) -> AcceptProfilesResponse:
    """Accept the suggestions shown, each as its own dated row; nothing is set otherwise."""
    _ensure_setter(principal)
    try:
        accepted = await service.accept(
            principal,
            [
                AcceptChoice(pod_id=item.pod_id, member_id=item.member_id, profile=item.profile)
                for item in request.items
            ],
        )
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return AcceptProfilesResponse(
        accepted=[MemberProfileResponse.from_view(view) for view in accepted]
    )


async def _ensure_can_set(service: ProfileService, principal: Principal, pod_id: str) -> None:
    try:
        await service.ensure_can_set(principal, pod_id)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


async def _settable(service: ProfileService, principal: Principal) -> tuple[str, ...] | None:
    _ensure_setter(principal)
    return await service.settable_pods(principal)


def _ensure_setter(principal: Principal) -> None:
    if not AuthorizationPolicy().can(principal, Capability.SET_POD_PROFILES):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_A_SETTER)


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
