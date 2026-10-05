from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Protocol

from opentelemetry import trace

from core.domain.reports import (
    DayReportDefinition,
    DayReportNote,
    DeliveryOutcome,
    DestinationKind,
    ReportDestination,
    ReportRun,
    ReportSchedule,
    RunTrigger,
)

_tracer = trace.get_tracer("openprogram.persistence.reports")

_DEFINITION_COLUMNS = """
    tenant_id, report_id, name, project_id, enabled, local_time, timezone, weekdays,
    destinations, updated_at, updated_by, release_id
"""
_RUN_COLUMNS = """
    tenant_id, run_id, report_id, report_date, trigger, started_at, finished_at, title, body,
    outcomes, actor
"""


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresDayReportRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def list_definitions(self, tenant_id: str) -> list[DayReportDefinition]:
        with _tracer.start_as_current_span("postgres.reports.list_definitions"):
            rows = await self._executor.fetch(
                f"SELECT {_DEFINITION_COLUMNS} FROM day_reports WHERE tenant_id = %s",
                (tenant_id,),
            )
        return [_definition(row) for row in rows]

    async def get_definition(self, tenant_id: str, report_id: str) -> DayReportDefinition | None:
        with _tracer.start_as_current_span("postgres.reports.get_definition"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_DEFINITION_COLUMNS} FROM day_reports
                WHERE tenant_id = %s AND report_id = %s
                """,
                (tenant_id, report_id),
            )
        return _definition(rows[0]) if rows else None

    async def save_definition(self, definition: DayReportDefinition) -> None:
        schedule = definition.schedule
        with _tracer.start_as_current_span("postgres.reports.save_definition"):
            await self._executor.execute(
                """
                INSERT INTO day_reports (
                    tenant_id, report_id, name, project_id, enabled, local_time, timezone,
                    weekdays, destinations, updated_at, updated_by, release_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (tenant_id, report_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    project_id = EXCLUDED.project_id,
                    enabled = EXCLUDED.enabled,
                    local_time = EXCLUDED.local_time,
                    timezone = EXCLUDED.timezone,
                    weekdays = EXCLUDED.weekdays,
                    destinations = EXCLUDED.destinations,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by,
                    release_id = EXCLUDED.release_id
                """,
                (
                    definition.tenant_id,
                    definition.report_id,
                    definition.name,
                    definition.project_id,
                    definition.enabled,
                    schedule.local_time,
                    schedule.timezone,
                    list(schedule.weekdays),
                    json.dumps(
                        [
                            {"kind": item.kind.value, "target": item.target}
                            for item in definition.destinations
                        ]
                    ),
                    definition.updated_at,
                    definition.updated_by,
                    definition.release_id,
                ),
            )

    async def delete_definition(self, tenant_id: str, report_id: str) -> bool:
        with _tracer.start_as_current_span("postgres.reports.delete_definition"):
            rows = await self._executor.fetch(
                """
                DELETE FROM day_reports WHERE tenant_id = %s AND report_id = %s
                RETURNING report_id
                """,
                (tenant_id, report_id),
            )
        return bool(rows)

    async def claim_scheduled_run(self, run: ReportRun) -> bool:
        with _tracer.start_as_current_span("postgres.reports.claim_scheduled_run"):
            rows = await self._executor.fetch(
                """
                INSERT INTO day_report_runs (
                    tenant_id, run_id, report_id, report_date, trigger, started_at
                )
                VALUES (%s, %s, %s, %s, 'schedule', %s)
                ON CONFLICT (tenant_id, report_id, report_date)
                    WHERE trigger = 'schedule' DO NOTHING
                RETURNING run_id
                """,
                (run.tenant_id, run.run_id, run.report_id, run.report_date, run.started_at),
            )
        return bool(rows)

    async def record_run(self, run: ReportRun) -> None:
        with _tracer.start_as_current_span("postgres.reports.record_run"):
            await self._executor.execute(
                """
                INSERT INTO day_report_runs (
                    tenant_id, run_id, report_id, report_date, trigger, started_at,
                    finished_at, title, body, outcomes, actor
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (tenant_id, run_id) DO UPDATE SET
                    finished_at = EXCLUDED.finished_at,
                    title = EXCLUDED.title,
                    body = EXCLUDED.body,
                    outcomes = EXCLUDED.outcomes
                """,
                (
                    run.tenant_id,
                    run.run_id,
                    run.report_id,
                    run.report_date,
                    run.trigger.value,
                    run.started_at,
                    run.finished_at,
                    run.title,
                    run.text,
                    json.dumps(
                        [
                            {
                                "kind": outcome.destination.kind.value,
                                "target": outcome.destination.target,
                                "ok": outcome.ok,
                                "detail": outcome.detail,
                            }
                            for outcome in run.outcomes
                        ]
                    ),
                    run.actor,
                ),
            )

    async def list_runs(self, tenant_id: str, report_id: str, limit: int) -> list[ReportRun]:
        with _tracer.start_as_current_span("postgres.reports.list_runs"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_RUN_COLUMNS} FROM day_report_runs
                WHERE tenant_id = %s AND report_id = %s
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (tenant_id, report_id, limit),
            )
        return [_run(row) for row in rows]

    async def get_note(
        self, tenant_id: str, report_id: str, report_date: date
    ) -> DayReportNote | None:
        with _tracer.start_as_current_span("postgres.reports.get_note"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, report_id, report_date, text, author, updated_at
                FROM day_report_notes
                WHERE tenant_id = %s AND report_id = %s AND report_date = %s
                """,
                (tenant_id, report_id, report_date),
            )
        if not rows:
            return None
        row = rows[0]
        day = row["report_date"]
        updated_at = row["updated_at"]
        if not isinstance(day, date) or not isinstance(updated_at, datetime):
            raise TypeError("day_report_notes row has no report_date or updated_at")
        return DayReportNote(
            tenant_id=str(row["tenant_id"]),
            report_id=str(row["report_id"]),
            report_date=day,
            text=str(row["text"]),
            author=str(row["author"]),
            updated_at=updated_at,
        )

    async def save_note(self, note: DayReportNote) -> None:
        with _tracer.start_as_current_span("postgres.reports.save_note"):
            if not note.text:
                await self._executor.execute(
                    """
                    DELETE FROM day_report_notes
                    WHERE tenant_id = %s AND report_id = %s AND report_date = %s
                    """,
                    (note.tenant_id, note.report_id, note.report_date),
                )
                return
            await self._executor.execute(
                """
                INSERT INTO day_report_notes (
                    tenant_id, report_id, report_date, text, author, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, report_id, report_date) DO UPDATE SET
                    text = EXCLUDED.text,
                    author = EXCLUDED.author,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    note.tenant_id,
                    note.report_id,
                    note.report_date,
                    note.text,
                    note.author,
                    note.updated_at,
                ),
            )


def _definition(row: Mapping[str, object]) -> DayReportDefinition:
    local_time = row["local_time"]
    updated_at = row["updated_at"]
    if not isinstance(local_time, time) or not isinstance(updated_at, datetime):
        raise TypeError("day_reports row has no local_time or updated_at")
    return DayReportDefinition(
        tenant_id=str(row["tenant_id"]),
        report_id=str(row["report_id"]),
        name=str(row["name"]),
        project_id=str(row["project_id"]),
        enabled=bool(row["enabled"]),
        schedule=ReportSchedule(
            local_time=local_time,
            timezone=str(row["timezone"]),
            weekdays=tuple(int(day) for day in _list(row.get("weekdays"))),  # type: ignore[call-overload]
        ),
        destinations=tuple(
            destination
            for item in _list(_json(row.get("destinations")))
            if (destination := _destination(item)) is not None
        ),
        updated_at=updated_at,
        updated_by=str(row["updated_by"]),
        release_id=str(row["release_id"]) if row.get("release_id") else None,
    )


def _run(row: Mapping[str, object]) -> ReportRun:
    report_date = row["report_date"]
    started_at = row["started_at"]
    finished_at = row.get("finished_at")
    if not isinstance(report_date, date) or not isinstance(started_at, datetime):
        raise TypeError("day_report_runs row has no report_date or started_at")
    outcomes: list[DeliveryOutcome] = []
    for item in _list(_json(row.get("outcomes"))):
        destination = _destination(item)
        if destination is None or not isinstance(item, Mapping):
            continue
        outcomes.append(
            DeliveryOutcome(
                destination=destination,
                ok=bool(item.get("ok")),
                detail=str(item.get("detail") or ""),
            )
        )
    actor = row.get("actor")
    return ReportRun(
        tenant_id=str(row["tenant_id"]),
        run_id=str(row["run_id"]),
        report_id=str(row["report_id"]),
        report_date=report_date,
        trigger=RunTrigger(str(row["trigger"])),
        started_at=started_at,
        finished_at=finished_at if isinstance(finished_at, datetime) else None,
        title=str(row.get("title") or ""),
        text=str(row.get("body") or ""),
        outcomes=tuple(outcomes),
        actor=actor if isinstance(actor, str) else None,
    )


def _destination(item: object) -> ReportDestination | None:
    if not isinstance(item, Mapping):
        return None
    try:
        kind = DestinationKind(str(item.get("kind")))
    except ValueError:
        return None
    return ReportDestination(kind=kind, target=str(item.get("target") or ""))


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []
