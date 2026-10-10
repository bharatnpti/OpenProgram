"""Requirements by delivery stage, the stage mapping behind them, and the forecast's minimum.

A project's requirements view needs read_project_progress, like the project's
progress. The stage mapping and the working days of history a forecast needs
are runtime config: reading or changing them needs manage_config. The tenant
always comes from the principal.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import (
    get_current_principal,
    get_delivery_service,
    get_forecast_service,
)
from api.dtos import (
    DeliveryStagesResponse,
    DeliveryStagesUpdateRequest,
    ForecastSettingsResponse,
    ForecastSettingsUpdateRequest,
    ObservedStatusResponse,
    RequirementsResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.delivery_service import (
    DEFAULT_TIMELINE_DAYS,
    MAX_TIMELINE_DAYS,
    DeliveryService,
)
from core.application.forecast_service import ForecastService
from core.domain.auth import Principal
from core.domain.delivery import StageMapping, StageMappingError, validated_mapping
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.forecast import ForecastSettingsError

router = APIRouter(tags=["delivery"])


@router.get("/projects/{project_id}/requirements", response_model=RequirementsResponse)
async def project_requirements(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DeliveryService, Depends(get_delivery_service)],
    forecasts: Annotated[ForecastService, Depends(get_forecast_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
    days: Annotated[
        int | None,
        Query(
            ge=1,
            le=MAX_TIMELINE_DAYS,
            description=(
                f"Days of timeline up to as_of. Left out: {DEFAULT_TIMELINE_DAYS}, or the "
                "forecast's window when the tenant's minimum needs more."
            ),
        ),
    ] = None,
    release_id: Annotated[str | None, Query(description="One release of the project.")] = None,
) -> RequirementsResponse:
    _ensure(principal, Capability.READ_PROJECT_PROGRESS)
    rule = await forecasts.forecast_settings(principal.tenant_id)
    # Left to the server, the timeline covers the forecast's window, so the flow of
    # stages can reach the forecast's minimum whenever the forecast can.
    timeline_days = days if days is not None else max(DEFAULT_TIMELINE_DAYS, rule.window_days)
    try:
        release = await forecasts.release(principal.tenant_id, release_id) if release_id else None
        view = await service.requirements(
            principal.tenant_id, project_id, as_of, days=timeline_days, release=release
        )
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return RequirementsResponse.from_view(
        view, timeline_days=timeline_days, forecast_needed_days=rule.min_sample_days
    )


@router.get("/config/delivery/stages", response_model=DeliveryStagesResponse)
async def get_delivery_stages(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DeliveryService, Depends(get_delivery_service)],
) -> DeliveryStagesResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return DeliveryStagesResponse.from_view(await service.delivery_settings(principal.tenant_id))


@router.put("/config/delivery/stages", response_model=DeliveryStagesResponse)
async def save_delivery_stages(
    request: DeliveryStagesUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DeliveryService, Depends(get_delivery_service)],
) -> DeliveryStagesResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    mapping = _mapping(request)
    view = await service.save_stage_mapping(principal.tenant_id, mapping, actor=principal.subject)
    return DeliveryStagesResponse.from_view(view)


@router.get("/config/delivery/forecast", response_model=ForecastSettingsResponse)
async def get_forecast_settings(
    principal: Annotated[Principal, Depends(get_current_principal)],
    forecasts: Annotated[ForecastService, Depends(get_forecast_service)],
) -> ForecastSettingsResponse:
    """How many working days of history a forecast needs: the tenant's, else the default."""
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ForecastSettingsResponse.from_view(
        await forecasts.forecast_settings(principal.tenant_id)
    )


@router.put("/config/delivery/forecast", response_model=ForecastSettingsResponse)
async def save_forecast_settings(
    request: ForecastSettingsUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    forecasts: Annotated[ForecastService, Depends(get_forecast_service)],
) -> ForecastSettingsResponse:
    """Set the working days of history a forecast needs; null goes back to the default.

    Every scope's forecast, the history read and the day report follow at once.
    A number outside the bounds is refused with 422 and says why.
    """
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        view = await forecasts.save_forecast_settings(
            principal.tenant_id, request.min_history_days, actor=principal.subject
        )
    except ForecastSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return ForecastSettingsResponse.from_view(view)


@router.get("/config/delivery/statuses", response_model=list[ObservedStatusResponse])
async def observed_statuses(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DeliveryService, Depends(get_delivery_service)],
) -> list[ObservedStatusResponse]:
    """Every status the synced issues carry, placed by the saved mapping."""
    _ensure(principal, Capability.MANAGE_CONFIG)
    return [
        ObservedStatusResponse.from_view(item)
        for item in await service.observed_statuses(principal.tenant_id)
    ]


@router.post("/config/delivery/statuses/preview", response_model=list[ObservedStatusResponse])
async def preview_observed_statuses(
    request: DeliveryStagesUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DeliveryService, Depends(get_delivery_service)],
) -> list[ObservedStatusResponse]:
    """The same statuses placed by an unsaved mapping. Nothing is stored."""
    _ensure(principal, Capability.MANAGE_CONFIG)
    mapping = _mapping(request)
    return [
        ObservedStatusResponse.from_view(item)
        for item in await service.observed_statuses(principal.tenant_id, mapping)
    ]


def _mapping(request: DeliveryStagesUpdateRequest) -> StageMapping:
    try:
        return validated_mapping(
            request.stages,
            excluded_statuses=request.excluded_statuses,
            requirement_types=request.requirement_types,
        )
    except StageMappingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
