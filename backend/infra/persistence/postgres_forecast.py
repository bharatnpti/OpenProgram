from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from opentelemetry import trace

from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    ForecastDayRecord,
    ForecastDaysNotKept,
    Release,
    ReleaseMatch,
    ReleaseMatchKind,
    Verdict,
)
from infra.persistence.forecast_day_rows import distinct_forecast_days

_tracer = trace.get_tracer("openprogram.persistence.forecast")
_CHANGE_COLUMNS = (
    "tenant_id, scope_kind, scope_id, project_id, target_date, changed_at, changed_by, note"
)
_RELEASE_COLUMNS = (
    "tenant_id, release_id, project_id, name, match_kind, match_value, updated_at, updated_by"
)
_FORECAST_DAY_COLUMNS = (
    "tenant_id, scope_kind, scope_id, project_id, day, p50, p85, verdict, target_date, "
    "team_latest, team_latest_key, remaining, unit, sample_days, completed_in_sample, "
    "min_sample_days, attribution, reconstructed, computed_at"
)


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresCommitmentRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def append(self, change: DateChange) -> None:
        with _tracer.start_as_current_span("postgres.forecast.append_change"):
            await self._executor.execute(
                f"""
                INSERT INTO delivery_date_changes ({_CHANGE_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    change.tenant_id,
                    change.scope.kind.value,
                    change.scope.id,
                    change.scope.project_id,
                    change.target_date,
                    change.changed_at,
                    change.changed_by,
                    change.note,
                ),
            )

    async def changes(self, tenant_id: str, scope: CommitmentScope) -> list[DateChange]:
        with _tracer.start_as_current_span("postgres.forecast.changes"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_CHANGE_COLUMNS} FROM delivery_date_changes
                WHERE tenant_id = %s AND scope_kind = %s AND scope_id = %s AND project_id = %s
                ORDER BY changed_at, id
                """,
                (tenant_id, scope.kind.value, scope.id, scope.project_id),
            )
        return [_change(row) for row in rows]

    async def changes_for_project(self, tenant_id: str, project_id: str) -> list[DateChange]:
        with _tracer.start_as_current_span("postgres.forecast.changes_for_project"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_CHANGE_COLUMNS} FROM delivery_date_changes
                WHERE tenant_id = %s AND project_id = %s
                ORDER BY changed_at, id
                """,
                (tenant_id, project_id),
            )
        return [_change(row) for row in rows]


class PostgresReleaseRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def list_for_project(self, tenant_id: str, project_id: str) -> list[Release]:
        with _tracer.start_as_current_span("postgres.forecast.list_releases"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_RELEASE_COLUMNS} FROM releases
                WHERE tenant_id = %s AND project_id = %s ORDER BY name
                """,
                (tenant_id, project_id),
            )
        return [_release(row) for row in rows]

    async def list_all(self, tenant_id: str) -> list[Release]:
        with _tracer.start_as_current_span("postgres.forecast.list_all_releases"):
            rows = await self._executor.fetch(
                f"SELECT {_RELEASE_COLUMNS} FROM releases WHERE tenant_id = %s ORDER BY name",
                (tenant_id,),
            )
        return [_release(row) for row in rows]

    async def get(self, tenant_id: str, release_id: str) -> Release | None:
        with _tracer.start_as_current_span("postgres.forecast.get_release"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_RELEASE_COLUMNS} FROM releases
                WHERE tenant_id = %s AND release_id = %s
                """,
                (tenant_id, release_id),
            )
        return _release(rows[0]) if rows else None

    async def save(self, release: Release) -> None:
        with _tracer.start_as_current_span("postgres.forecast.save_release"):
            await self._executor.execute(
                f"""
                INSERT INTO releases ({_RELEASE_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, release_id) DO UPDATE SET
                    project_id = EXCLUDED.project_id,
                    name = EXCLUDED.name,
                    match_kind = EXCLUDED.match_kind,
                    match_value = EXCLUDED.match_value,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                """,
                (
                    release.tenant_id,
                    release.release_id,
                    release.project_id,
                    release.name,
                    release.match.kind.value,
                    release.match.value,
                    release.updated_at,
                    release.updated_by,
                ),
            )

    async def delete(self, tenant_id: str, release_id: str) -> bool:
        with _tracer.start_as_current_span("postgres.forecast.delete_release"):
            rows = await self._executor.fetch(
                """
                DELETE FROM releases WHERE tenant_id = %s AND release_id = %s
                RETURNING release_id
                """,
                (tenant_id, release_id),
            )
        return bool(rows)


class PostgresForecastDayRepository:
    """forecast_days: a reconstructed row never replaces one the hourly run kept."""

    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def save(self, records: Sequence[ForecastDayRecord]) -> None:
        """Keep each record. A key offered twice keeps its last record, and says so: the
        statement would otherwise be refused whole and no row of the day saved. If the
        store refuses a batch anyway, ForecastDaysNotKept counts the rows it did not take."""
        rows = distinct_forecast_days(records)
        if not rows:
            return
        with _tracer.start_as_current_span("postgres.forecast.save_forecast_days"):
            # One statement per batch of days, so a reconstruction of 90 days is a few round trips.
            for start in range(0, len(rows), 100):
                batch = rows[start : start + 100]
                values = ", ".join(
                    [
                        "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        "%s::jsonb, %s, %s)"
                    ]
                    * len(batch)
                )
                try:
                    await self._executor.execute(
                        f"""
                        INSERT INTO forecast_days ({_FORECAST_DAY_COLUMNS})
                        VALUES {values}
                        ON CONFLICT (tenant_id, scope_kind, scope_id, day) DO UPDATE SET
                            project_id = EXCLUDED.project_id,
                            p50 = EXCLUDED.p50,
                            p85 = EXCLUDED.p85,
                            verdict = EXCLUDED.verdict,
                            target_date = EXCLUDED.target_date,
                            team_latest = EXCLUDED.team_latest,
                            team_latest_key = EXCLUDED.team_latest_key,
                            remaining = EXCLUDED.remaining,
                            unit = EXCLUDED.unit,
                            sample_days = EXCLUDED.sample_days,
                            completed_in_sample = EXCLUDED.completed_in_sample,
                            min_sample_days = EXCLUDED.min_sample_days,
                            attribution = EXCLUDED.attribution,
                            reconstructed = EXCLUDED.reconstructed,
                            computed_at = EXCLUDED.computed_at
                        WHERE forecast_days.reconstructed OR NOT EXCLUDED.reconstructed
                        """,
                        tuple(value for record in batch for value in _forecast_day_params(record)),
                    )
                except Exception as exc:
                    # This batch and the ones after it were not taken; the ones before were.
                    raise ForecastDaysNotKept(rows=len(rows) - start) from exc

    async def fill_attribution(self, records: Sequence[ForecastDayRecord]) -> None:
        with _tracer.start_as_current_span("postgres.forecast.fill_forecast_day_attribution"):
            for record in records:
                if record.attribution is None:
                    continue
                await self._executor.execute(
                    """
                    UPDATE forecast_days SET attribution = %s::jsonb
                    WHERE tenant_id = %s AND scope_kind = %s AND scope_id = %s
                        AND project_id = %s AND day = %s AND attribution IS NULL
                    """,
                    (
                        json.dumps(dict(record.attribution), sort_keys=True),
                        record.tenant_id,
                        record.scope.kind.value,
                        record.scope.id,
                        record.scope.project_id,
                        record.day,
                    ),
                )

    async def list_between(
        self, tenant_id: str, scope: CommitmentScope, start: date, end: date
    ) -> list[ForecastDayRecord]:
        with _tracer.start_as_current_span("postgres.forecast.list_forecast_days"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_FORECAST_DAY_COLUMNS} FROM forecast_days
                WHERE tenant_id = %s AND scope_kind = %s AND scope_id = %s
                    AND project_id = %s AND day BETWEEN %s AND %s
                ORDER BY day
                """,
                (tenant_id, scope.kind.value, scope.id, scope.project_id, start, end),
            )
        return [_forecast_day(row) for row in rows]


def _forecast_day_params(record: ForecastDayRecord) -> tuple[object, ...]:
    return (
        record.tenant_id,
        record.scope.kind.value,
        record.scope.id,
        record.scope.project_id,
        record.day,
        record.p50,
        record.p85,
        record.verdict.value,
        record.target_date,
        record.team_latest,
        record.team_latest_key,
        record.remaining,
        record.unit,
        record.sample_days,
        record.completed_in_sample,
        record.min_sample_days,
        json.dumps(dict(record.attribution), sort_keys=True)
        if record.attribution is not None
        else None,
        record.reconstructed,
        record.computed_at,
    )


def _forecast_day(row: Mapping[str, object]) -> ForecastDayRecord:
    day = row["day"]
    computed_at = row["computed_at"]
    if not isinstance(day, date) or not isinstance(computed_at, datetime):
        raise TypeError("forecast_days row has no day or computed_at")
    attribution = row.get("attribution")
    if isinstance(attribution, str):
        attribution = json.loads(attribution)
    return ForecastDayRecord(
        tenant_id=str(row["tenant_id"]),
        scope=CommitmentScope(
            kind=CommitmentScopeKind(str(row["scope_kind"])),
            id=str(row["scope_id"]),
            project_id=str(row["project_id"]),
        ),
        day=day,
        p50=_optional_date(row.get("p50")),
        p85=_optional_date(row.get("p85")),
        verdict=Verdict(str(row["verdict"])),
        target_date=_optional_date(row.get("target_date")),
        team_latest=_optional_date(row.get("team_latest")),
        team_latest_key=str(row["team_latest_key"]) if row.get("team_latest_key") else None,
        remaining=_number(row.get("remaining")),
        unit=str(row["unit"]),
        sample_days=int(row["sample_days"]),  # type: ignore[call-overload]
        completed_in_sample=_number(row.get("completed_in_sample")),
        min_sample_days=int(row["min_sample_days"]),  # type: ignore[call-overload]
        attribution=attribution if isinstance(attribution, Mapping) else None,
        reconstructed=bool(row.get("reconstructed")),
        computed_at=computed_at,
    )


def _optional_date(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _number(value: object) -> float:
    if isinstance(value, Decimal | int | float) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _change(row: Mapping[str, object]) -> DateChange:
    changed_at = row["changed_at"]
    target = row.get("target_date")
    if not isinstance(changed_at, datetime):
        raise TypeError("delivery_date_changes row has no changed_at")
    return DateChange(
        tenant_id=str(row["tenant_id"]),
        scope=CommitmentScope(
            kind=CommitmentScopeKind(str(row["scope_kind"])),
            id=str(row["scope_id"]),
            project_id=str(row["project_id"]),
        ),
        target_date=target if isinstance(target, date) else None,
        changed_at=changed_at,
        changed_by=str(row["changed_by"]),
        note=str(row.get("note") or ""),
    )


def _release(row: Mapping[str, object]) -> Release:
    updated_at = row["updated_at"]
    if not isinstance(updated_at, datetime):
        raise TypeError("releases row has no updated_at")
    return Release(
        tenant_id=str(row["tenant_id"]),
        release_id=str(row["release_id"]),
        project_id=str(row["project_id"]),
        name=str(row["name"]),
        match=ReleaseMatch(
            kind=ReleaseMatchKind(str(row["match_kind"])), value=str(row["match_value"])
        ),
        updated_at=updated_at,
        updated_by=str(row["updated_by"]),
    )
