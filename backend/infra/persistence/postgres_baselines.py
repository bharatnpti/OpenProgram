"""planning_baselines: every baseline kept, one active per scope (a partial unique index)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import date, datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.baseline import (
    BaselineItem,
    BaselineKind,
    BaselineScopeKind,
    PlanningBaseline,
)
from core.domain.delivery import DeliveryStage

_tracer = trace.get_tracer("openprogram.persistence.baselines")
_COLUMNS = (
    "id, tenant_id, scope_kind, scope_id, project_id, kind, planned_on, payload, change_label, "
    "grace_working_days, set_by, set_at, note, superseded_at"
)


class AsyncSqlSession(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class AsyncSqlExecutor(AsyncSqlSession, Protocol):
    def transaction(self) -> AbstractAsyncContextManager[AsyncSqlSession]: ...


class PostgresPlanningBaselineRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def add(self, baseline: PlanningBaseline) -> PlanningBaseline:
        with _tracer.start_as_current_span("postgres.baselines.add"):
            async with self._executor.transaction() as session:
                # The active one first: the partial unique index allows one per scope.
                await session.execute(
                    """
                    UPDATE planning_baselines SET superseded_at = %s
                    WHERE tenant_id = %s AND scope_kind = %s AND scope_id = %s
                        AND superseded_at IS NULL
                    """,
                    (
                        baseline.set_at,
                        baseline.tenant_id,
                        baseline.scope_kind.value,
                        baseline.scope_id,
                    ),
                )
                await session.execute(
                    f"""
                    INSERT INTO planning_baselines ({_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, NULL)
                    """,
                    (
                        baseline.baseline_id,
                        baseline.tenant_id,
                        baseline.scope_kind.value,
                        baseline.scope_id,
                        baseline.project_id,
                        baseline.kind.value,
                        baseline.planned_on,
                        json.dumps(payload_to_json(baseline), sort_keys=True),
                        baseline.change_label,
                        baseline.grace_working_days,
                        baseline.set_by,
                        baseline.set_at,
                        baseline.note,
                    ),
                )
        return replace(baseline, superseded_at=None)

    async def list_for_scope(
        self, tenant_id: str, scope_kind: BaselineScopeKind, scope_id: str
    ) -> list[PlanningBaseline]:
        with _tracer.start_as_current_span("postgres.baselines.list_for_scope"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_COLUMNS} FROM planning_baselines
                WHERE tenant_id = %s AND scope_kind = %s AND scope_id = %s
                ORDER BY set_at, id
                """,
                (tenant_id, scope_kind.value, scope_id),
            )
        return [_baseline(row) for row in rows]

    async def list_for_project(self, tenant_id: str, project_id: str) -> list[PlanningBaseline]:
        with _tracer.start_as_current_span("postgres.baselines.list_for_project"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_COLUMNS} FROM planning_baselines
                WHERE tenant_id = %s AND project_id = %s
                ORDER BY set_at, id
                """,
                (tenant_id, project_id),
            )
        return [_baseline(row) for row in rows]


def payload_to_json(baseline: PlanningBaseline) -> dict[str, object]:
    return {
        "keys": {
            key: {
                "stage": item.stage.value if item.stage is not None else None,
                "points": item.points,
                "due": item.due.isoformat() if item.due is not None else None,
                "type": item.type,
            }
            for key, item in baseline.items.items()
        },
        "reconstructed": baseline.reconstructed,
        "snapshot_day": baseline.snapshot_day.isoformat()
        if baseline.snapshot_day is not None
        else None,
    }


def _baseline(row: Mapping[str, object]) -> PlanningBaseline:
    planned_on = row["planned_on"]
    set_at = row["set_at"]
    if not isinstance(planned_on, date) or not isinstance(set_at, datetime):
        raise TypeError("planning_baselines row has no planned_on or set_at")
    superseded_at = row.get("superseded_at")
    payload = row.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    raw = payload if isinstance(payload, Mapping) else {}
    keys = raw.get("keys")
    return PlanningBaseline(
        tenant_id=str(row["tenant_id"]),
        baseline_id=str(row["id"]),
        scope_kind=BaselineScopeKind(str(row["scope_kind"])),
        scope_id=str(row["scope_id"]),
        project_id=str(row["project_id"]),
        kind=BaselineKind(str(row["kind"])),
        planned_on=planned_on,
        items={
            str(key): _item(value)
            for key, value in (keys.items() if isinstance(keys, Mapping) else ())
        },
        reconstructed=bool(raw.get("reconstructed")),
        snapshot_day=_iso_date(raw.get("snapshot_day")),
        change_label=str(row["change_label"]),
        grace_working_days=int(row["grace_working_days"]),  # type: ignore[call-overload]
        set_by=str(row["set_by"]),
        set_at=set_at,
        note=str(row.get("note") or ""),
        superseded_at=superseded_at if isinstance(superseded_at, datetime) else None,
    )


def _item(value: object) -> BaselineItem:
    raw = value if isinstance(value, Mapping) else {}
    points = raw.get("points")
    kind = raw.get("type")
    return BaselineItem(
        stage=_stage(raw.get("stage")),
        points=float(points)
        if isinstance(points, int | float) and not isinstance(points, bool)
        else None,
        due=_iso_date(raw.get("due")),
        type=kind if isinstance(kind, str) and kind else None,
    )


def _stage(value: object) -> DeliveryStage | None:
    try:
        return DeliveryStage(str(value)) if value is not None else None
    except ValueError:
        return None


def _iso_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
