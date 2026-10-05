from __future__ import annotations

from datetime import date
from typing import Protocol

from core.domain.reports import (
    DayReport,
    DayReportDefinition,
    DayReportNote,
    DeliveryOutcome,
    ReportDestination,
    ReportRun,
)


class DayReportRepository(Protocol):
    async def list_definitions(self, tenant_id: str) -> list[DayReportDefinition]: ...

    async def get_definition(
        self, tenant_id: str, report_id: str
    ) -> DayReportDefinition | None: ...

    async def save_definition(self, definition: DayReportDefinition) -> None: ...

    async def delete_definition(self, tenant_id: str, report_id: str) -> bool:
        """Remove the report and its runs. False when there was none."""
        ...

    async def claim_scheduled_run(self, run: ReportRun) -> bool:
        """Store ``run`` unless a scheduled run of the report exists for its day.

        The atomic step that makes a scheduled report go out at most once per
        local day, however often the schedule fires or is replayed.
        """
        ...

    async def record_run(self, run: ReportRun) -> None:
        """Store the run, replacing an earlier record of the same run id."""
        ...

    async def list_runs(self, tenant_id: str, report_id: str, limit: int) -> list[ReportRun]:
        """The report's runs, newest first."""
        ...

    async def get_note(
        self, tenant_id: str, report_id: str, report_date: date
    ) -> DayReportNote | None: ...

    async def save_note(self, note: DayReportNote) -> None:
        """Store the day's note; an empty text removes it."""
        ...


class ReportSender(Protocol):
    async def deliver(
        self, tenant_id: str, destination: ReportDestination, report: DayReport
    ) -> DeliveryOutcome:
        """Send one report to one destination. Never raises for a failed send."""
        ...
