"""Day reports: who gets which project's report, when, and what was sent.

Everyone reads the reports (read_day_reports): which there are, today's
report built live (a preview: nothing is sent or stored), and past sends with
how each destination fared and who sent it. A reader who may not set a report
up sees where it goes as counts and people's names, never the addresses or ids.

Sending now (send_day_reports) and setting reports up (set_up_day_reports) are
for the scrum master, the manager and the admin; a scrum master only for a
project one of their pods works on, by the rule a pod's date uses
(ForecastService.runs_pod). The note today's report opens with stays with
set_project_dates. Each report says what the caller may do with it. The
tenant always comes from the principal.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.dependencies import (
    get_current_principal,
    get_day_report_service,
    get_forecast_service,
    get_registry,
)
from api.dtos import (
    DayReportNoteRequest,
    DayReportRequest,
    DayReportResponse,
    DayReportSetupResponse,
    ReportDestinationOptionResponse,
    ReportPreviewResponse,
    ReportRunResponse,
    ReportSetupPersonResponse,
    ReportSetupProjectResponse,
    ReportSetupReleaseResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.day_report_service import DayReportService, ReportNotFound
from core.application.forecast_service import ForecastService
from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.graph import NodeKind
from core.domain.reports import (
    DayReportDefinition,
    DayReportNote,
    DestinationKind,
    ReportDefinitionError,
    ReportDestination,
    ReportRun,
    ReportSchedule,
)
from infra.registry import ServiceRegistry

router = APIRouter(prefix="/day-reports", tags=["reports"])

_DESTINATION_LABELS = {
    DestinationKind.CHAT_CHANNEL: "Chat channel",
    DestinationKind.PERSON: "Person (direct message)",
    DestinationKind.EMAIL: "Email address or mailing list",
    DestinationKind.TEAMS: "Teams channel",
}
_NOT_YOURS = (
    "Only a scrum master of a pod working on the project, a manager or an admin "
    "sends and sets up its reports."
)


class _Reach:
    """What the caller may do with each project's reports; a scrum master's differs by project."""

    def __init__(self, principal: Principal, forecast: ForecastService) -> None:
        self._principal = principal
        self._forecast = forecast
        self._policy = AuthorizationPolicy()
        self._runs: dict[str, bool] = {}

    def can(self, capability: Capability) -> bool:
        return self._policy.can(self._principal, capability)

    async def may(self, capability: Capability, project_id: str) -> bool:
        """Holds ``capability`` for this project: a manager or admin any, a scrum master theirs."""
        if not self.can(capability):
            return False
        if self._principal.has_role(Role.ADMIN) or self._principal.has_role(Role.MGR):
            return True
        if project_id not in self._runs:
            self._runs[project_id] = await self._forecast.runs_project_pod(
                self._principal.tenant_id, project_id, self._principal.subject
            )
        return self._runs[project_id]

    async def ensure(self, capability: Capability, project_id: str) -> None:
        if not await self.may(capability, project_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_YOURS)


class _Views:
    """Builds each report's response as the caller may see it, reading each name once."""

    def __init__(
        self,
        principal: Principal,
        service: DayReportService,
        forecast: ForecastService,
        registry: ServiceRegistry,
        reach: _Reach,
    ) -> None:
        self._principal = principal
        self._service = service
        self._forecast = forecast
        self._registry = registry
        self.reach = reach
        self._names: dict[str, str] | None = None
        self._projects: dict[str, str | None] = {}
        self._releases: dict[str, str | None] = {}

    async def names(self) -> dict[str, str]:
        if self._names is None:
            self._names = await _member_names(self._registry, self._principal.tenant_id)
        return self._names

    async def report(
        self, definition: DayReportDefinition, *, sent_before: bool = True
    ) -> DayReportResponse:
        """The report with its last send (none for a new one) and today's note."""
        tenant_id = self._principal.tenant_id
        last_run: ReportRun | None = None
        if sent_before:
            runs = await self._service.runs(tenant_id, definition.report_id, limit=1)
            last_run = runs[0] if runs else None
        note: DayReportNote | None = await self._service.note(tenant_id, definition.report_id)
        return DayReportResponse.from_domain(
            definition,
            names=await self.names(),
            can_send=await self.reach.may(Capability.SEND_DAY_REPORTS, definition.project_id),
            can_edit=await self.reach.may(Capability.SET_UP_DAY_REPORTS, definition.project_id),
            can_write_note=self.reach.can(Capability.SET_PROJECT_DATES),
            last_run=last_run,
            note=note,
            project_name=await self._project_name(definition.project_id),
            release_name=await self._release_name(definition.release_id),
        )

    async def runs(self, project_id: str, runs: Iterable[ReportRun]) -> list[ReportRunResponse]:
        exact = await self.reach.may(Capability.SET_UP_DAY_REPORTS, project_id)
        names = await self.names()
        return [ReportRunResponse.from_domain(run, names, exact=exact) for run in runs]

    async def _project_name(self, project_id: str) -> str | None:
        if project_id not in self._projects:
            node = await self._registry.graph_repository().get_node(
                self._principal.tenant_id, project_id
            )
            self._projects[project_id] = (
                node.name if node is not None and node.kind is NodeKind.PROJECT else None
            )
        return self._projects[project_id]

    async def _release_name(self, release_id: str | None) -> str | None:
        if release_id is None:
            return None
        if release_id not in self._releases:
            try:
                release = await self._forecast.release(self._principal.tenant_id, release_id)
            except GraphNotFound:
                self._releases[release_id] = None
            else:
                self._releases[release_id] = release.name
        return self._releases[release_id]


def _views(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    forecast: Annotated[ForecastService, Depends(get_forecast_service)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> _Views:
    return _Views(principal, service, forecast, registry, _Reach(principal, forecast))


@router.get("", response_model=list[DayReportResponse])
async def list_reports(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    views: Annotated[_Views, Depends(_views)],
    project_id: Annotated[str | None, Query(max_length=200)] = None,
) -> list[DayReportResponse]:
    """Every report, or one project's: its last send, today's note and what the caller may do."""
    _ensure(principal, Capability.READ_DAY_REPORTS)
    return [
        await views.report(definition)
        for definition in await service.definitions(principal.tenant_id)
        if project_id is None or definition.project_id == project_id
    ]


@router.get("/setup", response_model=DayReportSetupResponse)
async def setup_options(
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    forecast: Annotated[ForecastService, Depends(get_forecast_service)],
    views: Annotated[_Views, Depends(_views)],
) -> DayReportSetupResponse:
    """The projects the caller may set reports up for, the people, and the destinations."""
    _ensure(principal, Capability.SET_UP_DAY_REPORTS)
    tenant_id = principal.tenant_id
    projects: list[ReportSetupProjectResponse] = []
    for node in await registry.graph_repository().list_nodes(tenant_id, NodeKind.PROJECT):
        if not await views.reach.may(Capability.SET_UP_DAY_REPORTS, node.id):
            continue
        releases = await forecast.releases(tenant_id, node.id)
        projects.append(
            ReportSetupProjectResponse(
                id=node.id,
                name=node.name or node.id,
                releases=[
                    ReportSetupReleaseResponse(release_id=item.release_id, name=item.name)
                    for item in releases
                ],
            )
        )
    names = await views.names()
    return DayReportSetupResponse(
        projects=sorted(projects, key=lambda item: item.name.casefold()),
        people=sorted(
            (
                ReportSetupPersonResponse(id=member_id, name=name)
                for member_id, name in names.items()
            ),
            key=lambda item: item.name.casefold(),
        ),
        destinations=[
            ReportDestinationOptionResponse(
                kind=DestinationKind(kind),
                label=_DESTINATION_LABELS[DestinationKind(kind)],
                available=available,
                note=note,
            )
            for kind, available, note in await registry.report_destination_options(tenant_id)
        ],
    )


@router.post("", response_model=DayReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    request: DayReportRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    views: Annotated[_Views, Depends(_views)],
) -> DayReportResponse:
    _ensure(principal, Capability.SET_UP_DAY_REPORTS)
    await views.reach.ensure(Capability.SET_UP_DAY_REPORTS, request.project_id)
    saved = await _save(service, principal, request, report_id="")
    return await views.report(saved, sent_before=False)


@router.get("/{report_id}", response_model=DayReportResponse)
async def get_report(
    report_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    views: Annotated[_Views, Depends(_views)],
) -> DayReportResponse:
    _ensure(principal, Capability.READ_DAY_REPORTS)
    return await views.report(await _definition(service, principal, report_id))


@router.put("/{report_id}", response_model=DayReportResponse)
async def update_report(
    report_id: str,
    request: DayReportRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    views: Annotated[_Views, Depends(_views)],
) -> DayReportResponse:
    _ensure(principal, Capability.SET_UP_DAY_REPORTS)
    current = await _definition(service, principal, report_id)
    # Moving a report to another project needs the reach of both.
    await views.reach.ensure(Capability.SET_UP_DAY_REPORTS, current.project_id)
    await views.reach.ensure(Capability.SET_UP_DAY_REPORTS, request.project_id)
    saved = await _save(service, principal, request, report_id=report_id)
    return await views.report(saved)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    views: Annotated[_Views, Depends(_views)],
) -> Response:
    _ensure(principal, Capability.SET_UP_DAY_REPORTS)
    try:
        current = await service.definition(principal.tenant_id, report_id)
    except ReportNotFound:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    await views.reach.ensure(Capability.SET_UP_DAY_REPORTS, current.project_id)
    await service.remove(principal.tenant_id, report_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{report_id}/preview", response_model=ReportPreviewResponse)
async def preview_report(
    report_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
) -> ReportPreviewResponse:
    """Today's report, built now: what it would say if it were sent. Nothing is sent or stored."""
    _ensure(principal, Capability.READ_DAY_REPORTS)
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
    views: Annotated[_Views, Depends(_views)],
) -> ReportRunResponse:
    _ensure(principal, Capability.SEND_DAY_REPORTS)
    definition = await _definition(service, principal, report_id)
    await views.reach.ensure(Capability.SEND_DAY_REPORTS, definition.project_id)
    try:
        run = await service.send_now(principal.tenant_id, report_id, actor=principal.subject)
    except ReportNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return (await views.runs(definition.project_id, [run]))[0]


@router.get("/{report_id}/runs", response_model=list[ReportRunResponse])
async def report_runs(
    report_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    views: Annotated[_Views, Depends(_views)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ReportRunResponse]:
    """Past sends, newest first, with how each destination fared and who sent it."""
    _ensure(principal, Capability.READ_DAY_REPORTS)
    definition = await _definition(service, principal, report_id)
    runs = await service.runs(principal.tenant_id, report_id, limit=limit)
    return await views.runs(definition.project_id, runs)


@router.put("/{report_id}/note", response_model=DayReportResponse)
async def write_note(
    report_id: str,
    request: DayReportNoteRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DayReportService, Depends(get_day_report_service)],
    views: Annotated[_Views, Depends(_views)],
) -> DayReportResponse:
    """Write the note today's report opens with; an empty text removes it."""
    _ensure(principal, Capability.SET_PROJECT_DATES)
    definition = await _definition(service, principal, report_id)
    try:
        await service.save_note(
            principal.tenant_id, report_id, request.text, actor=principal.subject
        )
    except ReportNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ReportDefinitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return await views.report(definition)


async def _definition(
    service: DayReportService, principal: Principal, report_id: str
) -> DayReportDefinition:
    try:
        return await service.definition(principal.tenant_id, report_id)
    except ReportNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


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


async def _member_names(registry: ServiceRegistry, tenant_id: str) -> dict[str, str]:
    """Members' display names by node id, read from the graph; one without a name is left out.

    An id that is no member's gets no name, so the console shows the id or the
    kind of place, never a guess.
    """
    return {
        node.id: node.name
        for node in await registry.graph_repository().list_nodes(tenant_id, NodeKind.DEVELOPER)
        if node.name
    }


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
