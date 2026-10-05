"""Day reports: who gets which project's report, when, and what was sent.

Setting reports up is runtime config, so every /config route needs
manage_config. A preview builds the report without sending or storing it;
"send now" sends it to every destination at once and keeps the run.

A project's product owner or manager may see the project's reports and write
the note today's report opens with (set_project_dates), without managing
config. The tenant always comes from the principal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.dependencies import get_current_principal, get_day_report_service, get_registry
from api.dtos import (
    DayReportNoteRequest,
    DayReportNoteResponse,
    DayReportRequest,
    DayReportResponse,
    ProjectDayReportResponse,
    ReportDestinationOptionResponse,
    ReportPreviewResponse,
    ReportRunResponse,
    ReportScheduleDto,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.day_report_service import DayReportService, ReportNotFound
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.graph import NodeKind
from core.domain.reports import (
    DayReportDefinition,
    DayReportNote,
    DestinationKind,
    ReportDefinitionError,
    ReportDestination,
    ReportSchedule,
)
from infra.registry import ServiceRegistry

router = APIRouter(prefix="/config/reports", tags=["reports"])
project_router = APIRouter(tags=["reports"])

_DESTINATION_LABELS = {
    DestinationKind.CHAT_CHANNEL: "Chat channel",
    DestinationKind.PERSON: "Person (direct message)",
    DestinationKind.EMAIL: "Email address or mailing list",
    DestinationKind.TEAMS: "Teams channel",
}


@router.get("", response_model=list[DayReportResponse])
async def list_reports(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
) -> list[DayReportResponse]:
    _ensure(principal)
    responses: list[DayReportResponse] = []
    for definition in await service.definitions(principal.tenant_id):
        runs = await service.runs(principal.tenant_id, definition.report_id, limit=1)
        responses.append(DayReportResponse.from_domain(definition, runs[0] if runs else None))
    return responses


@router.get("/destinations", response_model=list[ReportDestinationOptionResponse])
async def destination_options(
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> list[ReportDestinationOptionResponse]:
    _ensure(principal)
    return [
        ReportDestinationOptionResponse(
            kind=DestinationKind(kind),
            label=_DESTINATION_LABELS[DestinationKind(kind)],
            available=available,
            note=note,
        )
        for kind, available, note in await registry.report_destination_options(principal.tenant_id)
    ]


@router.post("", response_model=DayReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    request: DayReportRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
) -> DayReportResponse:
    _ensure(principal)
    return DayReportResponse.from_domain(await _save(service, principal, request, report_id=""))


@router.get("/{report_id}", response_model=DayReportResponse)
async def get_report(
    report_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
) -> DayReportResponse:
    _ensure(principal)
    try:
        definition = await service.definition(principal.tenant_id, report_id)
        runs = await service.runs(principal.tenant_id, report_id, limit=1)
    except ReportNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return DayReportResponse.from_domain(definition, runs[0] if runs else None)


@router.put("/{report_id}", response_model=DayReportResponse)
async def update_report(
    report_id: str,
    request: DayReportRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
) -> DayReportResponse:
    _ensure(principal)
    return DayReportResponse.from_domain(
        await _save(service, principal, request, report_id=report_id)
    )


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
) -> Response:
    _ensure(principal)
    await service.remove(principal.tenant_id, report_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{report_id}/preview", response_model=ReportPreviewResponse)
async def preview_report(
    report_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
) -> ReportPreviewResponse:
    _ensure(principal)
    try:
        preview = await service.preview(principal.tenant_id, report_id)
    except (ReportNotFound, GraphNotFound) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ReportPreviewResponse.from_preview(preview.report, preview.text)


@router.post("/{report_id}/send", response_model=ReportRunResponse)
async def send_report_now(
    report_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
) -> ReportRunResponse:
    _ensure(principal)
    try:
        run = await service.send_now(principal.tenant_id, report_id, actor=principal.subject)
    except ReportNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ReportRunResponse.from_domain(run)


@router.get("/{report_id}/runs", response_model=list[ReportRunResponse])
async def report_runs(
    report_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ReportRunResponse]:
    _ensure(principal)
    try:
        runs = await service.runs(principal.tenant_id, report_id, limit=limit)
    except ReportNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [ReportRunResponse.from_domain(run) for run in runs]


async def _save(
    service: DayReportService,
    principal: Principal,
    request: DayReportRequest,
    *,
    report_id: str,
) -> DayReportDefinition:
    definition = DayReportDefinition(
        tenant_id=principal.tenant_id,
        report_id=report_id,
        name=request.name,
        project_id=request.project_id,
        enabled=request.enabled,
        schedule=ReportSchedule(
            local_time=request.schedule.local_time,
            timezone=request.schedule.timezone,
            weekdays=tuple(request.schedule.weekdays),
        ),
        destinations=tuple(
            ReportDestination(kind=item.kind, target=item.target) for item in request.destinations
        ),
        updated_at=datetime.now(tz=UTC),
        updated_by=principal.subject,
        release_id=request.release_id,
    )
    try:
        return await service.save(definition, actor=principal.subject)
    except ReportNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (GraphNotFound, ReportDefinitionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@project_router.get(
    "/projects/{project_id}/day-reports", response_model=list[ProjectDayReportResponse]
)
async def project_reports(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> list[ProjectDayReportResponse]:
    """The project's reports and the note each opens with today."""
    _ensure(principal, Capability.SET_PROJECT_DATES)
    names = await _member_names(registry, principal.tenant_id)
    responses: list[ProjectDayReportResponse] = []
    for definition in await service.definitions(principal.tenant_id):
        if definition.project_id != project_id:
            continue
        note = await service.note(principal.tenant_id, definition.report_id)
        responses.append(_project_report(definition, note, names))
    return responses


@project_router.put("/day-reports/{report_id}/note", response_model=ProjectDayReportResponse)
async def write_note(
    report_id: str,
    request: DayReportNoteRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> ProjectDayReportResponse:
    """Write the note today's report opens with; an empty text removes it."""
    _ensure(principal, Capability.SET_PROJECT_DATES)
    try:
        definition = await service.definition(principal.tenant_id, report_id)
        note = await service.save_note(
            principal.tenant_id, report_id, request.text, actor=principal.subject
        )
    except ReportNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ReportDefinitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    names = await _member_names(registry, principal.tenant_id)
    return _project_report(definition, note if note.text else None, names)


def _project_report(
    definition: DayReportDefinition, note: DayReportNote | None, names: dict[str, str]
) -> ProjectDayReportResponse:
    return ProjectDayReportResponse(
        report_id=definition.report_id,
        name=definition.name,
        enabled=definition.enabled,
        release_id=definition.release_id,
        schedule=ReportScheduleDto(
            local_time=definition.schedule.local_time,
            timezone=definition.schedule.timezone,
            weekdays=list(definition.schedule.weekdays),
        ),
        destination_count=len(definition.destinations),
        note=(
            DayReportNoteResponse.from_domain(note, names.get(note.author))
            if note is not None
            else None
        ),
    )


async def _member_names(registry: ServiceRegistry, tenant_id: str) -> dict[str, str]:
    return {
        node.id: node.name
        for node in await registry.graph_repository().list_nodes(tenant_id, NodeKind.DEVELOPER)
    }


def _ensure(principal: Principal, capability: Capability = Capability.MANAGE_CONFIG) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
