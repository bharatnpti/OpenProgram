from __future__ import annotations

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
    CheckinPreferenceUpdateRequest,
    MyStatusResponse,
    StatusCorrectionRequest,
)
from config.settings import Settings
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.self_status_service import SelfStatusService
from core.domain.auth import Principal
from core.domain.blockers import BlockerReport
from core.domain.errors import AuthorizationDenied
from core.domain.status import CheckInPreference
from infra.registry import ServiceRegistry

router = APIRouter(tags=["checkins"])


@router.get("/me/status", response_model=MyStatusResponse)
async def get_my_status(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[SelfStatusService, Depends(get_self_status_service)],
    as_of: date | None = None,
) -> MyStatusResponse:
    _ensure_own_work(principal)
    effective_as_of = as_of or date.today()
    status = await service.my_status(principal.tenant_id, principal.subject, effective_as_of)
    if status is None:
        raise HTTPException(status_code=404, detail="status is not available")
    details = await service.my_blocker_details(
        principal.tenant_id, principal.subject, effective_as_of
    )
    return MyStatusResponse.from_domain(status, blocker_details=details)


@router.post("/me/status/confirm", response_model=MyStatusResponse)
async def confirm_my_status(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[SelfStatusService, Depends(get_self_status_service)],
    as_of: date | None = None,
) -> MyStatusResponse:
    _ensure_own_work(principal)
    effective_as_of = as_of or date.today()
    status = await service.confirm(principal.tenant_id, principal.subject, effective_as_of)
    if status is None:
        raise HTTPException(status_code=404, detail="status is not available")
    details = await service.my_blocker_details(
        principal.tenant_id, principal.subject, effective_as_of
    )
    return MyStatusResponse.from_domain(status, blocker_details=details)


@router.post("/me/status/correct", response_model=MyStatusResponse)
async def correct_my_status(
    request: StatusCorrectionRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[SelfStatusService, Depends(get_self_status_service)],
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
        raise HTTPException(status_code=404, detail="status is not available")
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
    preference = await _preference_for(registry, settings, principal)
    return CheckinPreferenceResponse.from_domain(preference)


@router.put("/me/checkin-preference", response_model=CheckinPreferenceResponse)
async def update_checkin_preference(
    request: CheckinPreferenceUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> CheckinPreferenceResponse:
    _ensure_own_work(principal)
    existing = await _preference_for(registry, settings, principal)
    fields = request.model_fields_set
    local_time = (
        request.local_time
        if "local_time" in fields and request.local_time is not None
        else existing.local_time
    )
    weekdays = (
        tuple(request.weekdays)
        if "weekdays" in fields and request.weekdays is not None
        else existing.weekdays
    )
    updated = CheckInPreference(
        tenant_id=principal.tenant_id,
        developer_id=principal.subject,
        local_time=local_time,
        timezone=request.timezone if "timezone" in fields else existing.timezone,
        weekdays=weekdays,
        reply_wait_seconds=(
            request.reply_wait_seconds
            if "reply_wait_seconds" in fields and request.reply_wait_seconds is not None
            else existing.reply_wait_seconds
        ),
        final_reply_wait_seconds=(
            request.final_reply_wait_seconds
            if "final_reply_wait_seconds" in fields and request.final_reply_wait_seconds is not None
            else existing.final_reply_wait_seconds
        ),
        write_back_consent=existing.write_back_consent,
    )
    await registry.status_repository().record_checkin_preference(updated)
    return CheckinPreferenceResponse.from_domain(updated)


async def _preference_for(
    registry: ServiceRegistry,
    settings: Settings,
    principal: Principal,
) -> CheckInPreference:
    preference = await registry.status_repository().checkin_preference_for(
        principal.tenant_id,
        principal.subject,
    )
    if preference is not None:
        return preference
    return CheckInPreference(
        tenant_id=principal.tenant_id,
        developer_id=principal.subject,
        timezone=settings.tenant_default_timezone,
        reply_wait_seconds=settings.checkin_reply_wait_seconds,
        final_reply_wait_seconds=settings.checkin_final_reply_wait_seconds,
    )


def _ensure_own_work(principal: Principal) -> None:
    try:
        AuthorizationPolicy().ensure(principal, Capability.READ_OWN_WORK)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
