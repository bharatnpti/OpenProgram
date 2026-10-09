"""Define day reports, preview them, and send them on time or on demand.

A scheduled send claims its report's local day first and only then builds and
sends, so the report goes out at most once that day whatever the scheduler
does. "Send now" is the admin's explicit action: it always sends and is kept
as a manual run, which does not stop that day's scheduled one.

A note written for a report's local day opens that day's report, so the
person who knows the news or the ask can add it before the scheduled send.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from uuid import uuid4

from core.application.day_report_builder import DayReportBuilder
from core.domain.errors import GraphNotFound, OpenProgramError
from core.domain.graph import NodeKind
from core.domain.reports import (
    DayReport,
    DayReportDefinition,
    DayReportNote,
    DeliveryOutcome,
    ReportRun,
    RunTrigger,
    due_date,
    render_text,
    report_timezone,
    validated_definition,
    validated_note,
)
from core.ports.reports import DayReportRepository, ReportSender
from core.ports.repositories import GraphRepository

DEFAULT_RUN_HISTORY = 20


class ReportNotFound(OpenProgramError):
    """No report with that id exists for the tenant."""


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _new_id() -> str:
    return uuid4().hex


@dataclass(frozen=True, kw_only=True)
class ReportPreview:
    report: DayReport
    text: str


class DayReportService:
    def __init__(
        self,
        *,
        repository: DayReportRepository,
        sender: ReportSender,
        builder: DayReportBuilder,
        graph_repository: GraphRepository,
        clock: Callable[[], datetime] = _utc_now,
        new_id: Callable[[], str] = _new_id,
    ) -> None:
        self._repository = repository
        self._sender = sender
        self._builder = builder
        self._graph = graph_repository
        self._clock = clock
        self._new_id = new_id

    # ---- definitions ---------------------------------------------------

    async def definitions(self, tenant_id: str) -> list[DayReportDefinition]:
        return sorted(
            await self._repository.list_definitions(tenant_id),
            key=lambda definition: (definition.name.casefold(), definition.report_id),
        )

    async def definition(self, tenant_id: str, report_id: str) -> DayReportDefinition:
        definition = await self._repository.get_definition(tenant_id, report_id)
        if definition is None:
            raise ReportNotFound(f"No report {report_id!r}.")
        return definition

    async def save(self, definition: DayReportDefinition, *, actor: str) -> DayReportDefinition:
        """Create (no report id) or replace a report, after checking it and its project."""
        report_id = definition.report_id or self._new_id()
        if definition.report_id:
            await self.definition(definition.tenant_id, definition.report_id)
        checked = validated_definition(
            replace(definition, report_id=report_id, updated_at=self._clock(), updated_by=actor)
        )
        project = await self._graph.get_node(checked.tenant_id, checked.project_id)
        if project is None or project.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"No project {checked.project_id!r}.")
        if checked.release_id is not None:
            await self._builder.check_release(
                checked.tenant_id, checked.project_id, checked.release_id
            )
        await self._repository.save_definition(checked)
        return checked

    async def remove(self, tenant_id: str, report_id: str) -> None:
        await self._repository.delete_definition(tenant_id, report_id)

    async def runs(
        self, tenant_id: str, report_id: str, *, limit: int = DEFAULT_RUN_HISTORY
    ) -> list[ReportRun]:
        await self.definition(tenant_id, report_id)
        return await self._repository.list_runs(tenant_id, report_id, limit)

    # ---- the day's note -------------------------------------------------

    async def note(self, tenant_id: str, report_id: str) -> DayReportNote | None:
        """The note for the report's local today, if anyone wrote one."""
        definition = await self.definition(tenant_id, report_id)
        return await self._repository.get_note(tenant_id, report_id, self._local_today(definition))

    async def save_note(
        self, tenant_id: str, report_id: str, text: str, *, actor: str
    ) -> DayReportNote:
        """Write (or, with no text, remove) the note for the report's local today."""
        definition = await self.definition(tenant_id, report_id)
        note = DayReportNote(
            tenant_id=tenant_id,
            report_id=report_id,
            report_date=self._local_today(definition),
            text=validated_note(text),
            author=actor,
            updated_at=self._clock(),
        )
        await self._repository.save_note(note)
        return note

    # ---- building and sending -------------------------------------------

    async def preview(self, tenant_id: str, report_id: str) -> ReportPreview:
        definition = await self.definition(tenant_id, report_id)
        report = await self._build(definition, self._local_today(definition))
        return ReportPreview(report=report, text=render_text(report))

    async def _build(self, definition: DayReportDefinition, day: date) -> DayReport:
        note = await self._repository.get_note(definition.tenant_id, definition.report_id, day)
        return await self._builder.build(
            definition.tenant_id,
            definition.project_id,
            day,
            release_id=definition.release_id,
            report_id=definition.report_id,
            note=note,
        )

    async def send_now(self, tenant_id: str, report_id: str, *, actor: str) -> ReportRun:
        definition = await self.definition(tenant_id, report_id)
        run = ReportRun(
            tenant_id=tenant_id,
            run_id=self._new_id(),
            report_id=report_id,
            report_date=self._local_today(definition),
            trigger=RunTrigger.MANUAL,
            started_at=self._clock(),
            actor=actor,
        )
        await self._repository.record_run(run)
        return await self._send(definition, run)

    async def dispatch_due(self, tenant_id: str, now: datetime | None = None) -> list[ReportRun]:
        """Send every enabled report due now that has not gone out for its local day."""
        moment = now or self._clock()
        sent: list[ReportRun] = []
        for definition in await self._repository.list_definitions(tenant_id):
            if not definition.enabled or not definition.destinations:
                continue
            day = due_date(definition.schedule, moment)
            if day is None:
                continue
            run = ReportRun(
                tenant_id=tenant_id,
                run_id=self._new_id(),
                report_id=definition.report_id,
                report_date=day,
                trigger=RunTrigger.SCHEDULE,
                started_at=self._clock(),
            )
            if not await self._repository.claim_scheduled_run(run):
                continue
            sent.append(await self._send(definition, run))
        return sent

    async def _send(self, definition: DayReportDefinition, run: ReportRun) -> ReportRun:
        try:
            report = await self._build(definition, run.report_date)
        except GraphNotFound:
            finished = replace(
                run,
                finished_at=self._clock(),
                title=definition.name,
                outcomes=tuple(
                    DeliveryOutcome(
                        destination=destination,
                        ok=False,
                        detail="Not sent: the report's project or release no longer exists.",
                    )
                    for destination in definition.destinations
                ),
            )
            await self._repository.record_run(finished)
            return finished
        outcomes = [
            await self._sender.deliver(definition.tenant_id, destination, report)
            for destination in definition.destinations
        ]
        finished = replace(
            run,
            finished_at=self._clock(),
            title=report.title,
            text=render_text(report),
            outcomes=tuple(outcomes),
        )
        await self._repository.record_run(finished)
        return finished

    def _local_today(self, definition: DayReportDefinition) -> date:
        return self._clock().astimezone(report_timezone(definition.schedule)).date()
