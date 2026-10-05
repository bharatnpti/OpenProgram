from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from core.domain.reports import DayReportDefinition, DayReportNote, ReportRun, RunTrigger


@dataclass
class InMemoryDayReportRepository:
    _definitions: dict[tuple[str, str], DayReportDefinition] = field(default_factory=dict)
    _runs: dict[tuple[str, str], ReportRun] = field(default_factory=dict)
    _notes: dict[tuple[str, str, date], DayReportNote] = field(default_factory=dict)

    async def list_definitions(self, tenant_id: str) -> list[DayReportDefinition]:
        return [
            definition
            for (tenant, _report), definition in self._definitions.items()
            if tenant == tenant_id
        ]

    async def get_definition(self, tenant_id: str, report_id: str) -> DayReportDefinition | None:
        return self._definitions.get((tenant_id, report_id))

    async def save_definition(self, definition: DayReportDefinition) -> None:
        self._definitions[(definition.tenant_id, definition.report_id)] = definition

    async def delete_definition(self, tenant_id: str, report_id: str) -> bool:
        removed = self._definitions.pop((tenant_id, report_id), None) is not None
        for note_key in [key for key in self._notes if key[:2] == (tenant_id, report_id)]:
            del self._notes[note_key]
        for key in [key for key, run in self._runs.items() if run.report_id == report_id]:
            if key[0] == tenant_id:
                del self._runs[key]
        return removed

    async def claim_scheduled_run(self, run: ReportRun) -> bool:
        for existing in self._runs.values():
            if (
                existing.tenant_id == run.tenant_id
                and existing.report_id == run.report_id
                and existing.report_date == run.report_date
                and existing.trigger is RunTrigger.SCHEDULE
            ):
                return False
        self._runs[(run.tenant_id, run.run_id)] = run
        return True

    async def record_run(self, run: ReportRun) -> None:
        self._runs[(run.tenant_id, run.run_id)] = run

    async def list_runs(self, tenant_id: str, report_id: str, limit: int) -> list[ReportRun]:
        runs = [
            run
            for run in self._runs.values()
            if run.tenant_id == tenant_id and run.report_id == report_id
        ]
        return sorted(runs, key=lambda run: run.started_at, reverse=True)[:limit]

    async def get_note(
        self, tenant_id: str, report_id: str, report_date: date
    ) -> DayReportNote | None:
        return self._notes.get((tenant_id, report_id, report_date))

    async def save_note(self, note: DayReportNote) -> None:
        key = (note.tenant_id, note.report_id, note.report_date)
        if note.text:
            self._notes[key] = note
        else:
            self._notes.pop(key, None)
