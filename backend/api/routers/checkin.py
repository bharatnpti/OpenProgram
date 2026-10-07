from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import (
    get_current_principal,
    get_registry,
    get_self_status_service,
    get_settings_from_request,
)
from api.dtos import (
    CheckinPreferenceResponse,
    MyStatusResponse,
    SelfCheckinPreferenceUpdateRequest,
    StatusCorrectionRequest,
)
from config.settings import Settings
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.self_status_service import SelfStatusService
from core.domain.auth import Principal
from core.domain.blockers import BlockerReport
from core.domain.errors import AuthorizationDenied
from core.domain.graph import NodeKind
from core.domain.status import CheckInPreference, effective_checkin_preference
from infra.registry import ServiceRegistry

router = APIRouter(tags=["checkins"])


@router.get("/me/status", response_model=MyStatusResponse)
async def get_my_status(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[SelfStatusService, Depends(get_self_status_service)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    as_of: date | None = None,
) -> MyStatusResponse:
    _ensure_own_work(principal)
    effective_as_of = as_of or date.today()
    status = await service.my_status(principal.tenant_id, principal.subject, effective_as_of)
    if status is None:
        raise await _status_not_available(registry, principal)
    details = await service.my_blocker_details(
        principal.tenant_id, principal.subject, effective_as_of
    )
    return MyStatusResponse.from_domain(status, blocker_details=details)


@router.post("/me/status/confirm", response_model=MyStatusResponse)
async def confirm_my_status(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[SelfStatusService, Depends(get_self_status_service)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    as_of: date | None = None,
) -> MyStatusResponse:
    _ensure_own_work(principal)
    effective_as_of = as_of or date.today()
    status = await service.confirm(principal.tenant_id, principal.subject, effective_as_of)
    if status is None:
        raise await _status_not_available(registry, principal)
    details = await service.my_blocker_details(
        principal.tenant_id, principal.subject, effective_as_of
    )
    return MyStatusResponse.from_domain(status, blocker_details=details)


@router.post("/me/status/correct", response_model=MyStatusResponse)
async def correct_my_status(
    request: StatusCorrectionRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[SelfStatusService, Depends(get_self_status_service)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    as_of: date | None = None,
) -> MyStatusResponse:
    _ensure_own_work(principal)
    effective_as_of = as_of or date.today()
    blocker_reports = (
        tuple(
            BlockerReport(
                description=item.description,
                issue_key=item.work_item_id,
                pod_id=item.pod_id,
                resolved=item.resolved,
                blocker_id=item.blocker_id,
            )
            for item in request.blocker_items
        )
        if request.blocker_items is not None
        else None
    )
    status = await service.correct(
        principal.tenant_id,
        principal.subject,
        effective_as_of,
        summary=request.summary,
        blockers=tuple(request.blockers),
        eta_change_days=request.eta_change_days,
        blocker_reports=blocker_reports,
    )
    if status is None:
        raise await _status_not_available(registry, principal)
    details = await service.my_blocker_details(
        principal.tenant_id, principal.subject, effective_as_of
    )
    return MyStatusResponse.from_domain(status, blocker_details=details)


@router.get("/me/checkin-preference", response_model=CheckinPreferenceResponse)
async def get_checkin_preference(
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> CheckinPreferenceResponse:
    _ensure_own_work(principal)
    await _ensure_member(registry, principal)
    preference = await _stored_preference(registry, principal)
    return _preference_response(principal, preference, settings)


@router.put("/me/checkin-preference", response_model=CheckinPreferenceResponse)
async def update_checkin_preference(
    request: SelfCheckinPreferenceUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> CheckinPreferenceResponse:
    _ensure_own_work(principal)
    await _ensure_member(registry, principal)
    existing = await _stored_preference(registry, principal) or CheckInPreference(
        tenant_id=principal.tenant_id,
        developer_id=principal.subject,
    )
    fields = request.model_fields_set
    # Only days and time zone are the person's to change. Everything else --
    # check-in time, reply windows, write-back consent -- is carried over as
    # stored; the request model refuses those fields outright. A field sent as
    # null goes back to the team default. The team defaults are never copied
    # in, so a later change to them still reaches this person.
    updated = replace(
        existing,
        timezone=request.timezone if "timezone" in fields else existing.timezone,
        weekdays=(
            (tuple(request.weekdays) if request.weekdays is not None else None)
            if "weekdays" in fields
            else existing.weekdays
        ),
    )
    await registry.status_repository().record_checkin_preference(updated)
    return _preference_response(principal, updated, settings)


async def _stored_preference(
    registry: ServiceRegistry,
    principal: Principal,
) -> CheckInPreference | None:
    return await registry.status_repository().checkin_preference_for(
        principal.tenant_id,
        principal.subject,
    )


def _preference_response(
    principal: Principal,
    preference: CheckInPreference | None,
    settings: Settings,
) -> CheckinPreferenceResponse:
    return CheckinPreferenceResponse.from_domain(
        effective_checkin_preference(
            principal.tenant_id,
            principal.subject,
            preference,
            settings.checkin_defaults(),
        )
    )


async def _ensure_member(registry: ServiceRegistry, principal: Principal) -> None:
    """Only a configured member is asked to check in, so only one has a preference.

    Check-ins go to member (developer) nodes, so a preference saved for anyone
    else is never read: answering with tenant defaults told them they had a
    schedule, and a save wrote a row nothing uses.
    """
    if not await _is_member(registry, principal):
        raise HTTPException(
            status_code=404,
            detail="check-in preference is not available: no member record for this person",
        )


#: The 404 details of the own-status routes. Both are 404, so a client that
#: reads a 404 as "nothing to show" keeps working; the detail tells apart
#: someone who is no member from a member with no status on record yet.
STATUS_NOT_A_MEMBER_DETAIL = "status is not available: no member record for this person"
STATUS_NONE_YET_DETAIL = "status is not available: no status on record yet for this member"


async def _status_not_available(registry: ServiceRegistry, principal: Principal) -> HTTPException:
    """The 404 for an own-status route with no status: no member record, or none yet."""
    if not await _is_member(registry, principal):
        return HTTPException(status_code=404, detail=STATUS_NOT_A_MEMBER_DETAIL)
    return HTTPException(status_code=404, detail=STATUS_NONE_YET_DETAIL)


async def _is_member(registry: ServiceRegistry, principal: Principal) -> bool:
    """Whether the caller has a member (developer) record: who check-ins and statuses are for."""
    node = await registry.graph_repository().get_node(principal.tenant_id, principal.subject)
    return node is not None and node.kind is NodeKind.DEVELOPER


def _ensure_own_work(principal: Principal) -> None:
    try:
        AuthorizationPolicy().ensure(principal, Capability.READ_OWN_WORK)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
