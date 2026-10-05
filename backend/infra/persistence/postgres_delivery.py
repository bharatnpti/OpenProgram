from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.delivery import (
    STAGE_ORDER,
    DeliverySettings,
    DeliveryStage,
    RequirementsSnapshot,
    StageMapping,
)

_tracer = trace.get_tracer("openprogram.persistence.delivery")


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresDeliverySettingsRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def get(self, tenant_id: str) -> DeliverySettings | None:
        with _tracer.start_as_current_span("postgres.delivery.get_settings"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, mapping, updated_at, updated_by
                FROM delivery_settings
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            )
        if not rows:
            return None
        row = rows[0]
        updated_at = row.get("updated_at")
        return DeliverySettings(
            tenant_id=str(row["tenant_id"]),
            mapping=mapping_from_json(_json(row.get("mapping"))),
            updated_at=updated_at if isinstance(updated_at, datetime) else None,
            updated_by=str(row.get("updated_by") or "") or None,
        )

    async def save(self, settings: DeliverySettings) -> None:
        with _tracer.start_as_current_span("postgres.delivery.save_settings"):
            await self._executor.execute(
                """
                INSERT INTO delivery_settings (tenant_id, mapping, updated_at, updated_by)
                VALUES (%s, %s::jsonb, %s, %s)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    mapping = EXCLUDED.mapping,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                """,
                (
                    settings.tenant_id,
                    json.dumps(mapping_to_json(settings.mapping)),
                    settings.updated_at,
                    settings.updated_by or "",
                ),
            )


class PostgresRequirementsSnapshotRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def save(self, snapshot: RequirementsSnapshot) -> None:
        with _tracer.start_as_current_span("postgres.delivery.save_snapshot"):
            await self._executor.execute(
                """
                INSERT INTO requirement_snapshots (tenant_id, project_id, day, payload, computed_at)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (tenant_id, project_id, day) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    computed_at = EXCLUDED.computed_at
                """,
                (
                    snapshot.tenant_id,
                    snapshot.project_id,
                    snapshot.day,
                    json.dumps(snapshot_to_json(snapshot), sort_keys=True),
                    snapshot.computed_at,
                ),
            )

    async def get(self, tenant_id: str, project_id: str, day: date) -> RequirementsSnapshot | None:
        with _tracer.start_as_current_span("postgres.delivery.get_snapshot"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, project_id, day, payload, computed_at
                FROM requirement_snapshots
                WHERE tenant_id = %s AND project_id = %s AND day = %s
                """,
                (tenant_id, project_id, day),
            )
        return _snapshot_from_row(rows[0]) if rows else None

    async def list_between(
        self, tenant_id: str, project_id: str, start: date, end: date
    ) -> list[RequirementsSnapshot]:
        with _tracer.start_as_current_span("postgres.delivery.list_snapshots"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, project_id, day, payload, computed_at
                FROM requirement_snapshots
                WHERE tenant_id = %s AND project_id = %s AND day BETWEEN %s AND %s
                ORDER BY day
                """,
                (tenant_id, project_id, start, end),
            )
        return [_snapshot_from_row(row) for row in rows]

    async def latest_before(
        self, tenant_id: str, project_id: str, day: date
    ) -> RequirementsSnapshot | None:
        with _tracer.start_as_current_span("postgres.delivery.latest_snapshot_before"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, project_id, day, payload, computed_at
                FROM requirement_snapshots
                WHERE tenant_id = %s AND project_id = %s AND day < %s
                ORDER BY day DESC
                LIMIT 1
                """,
                (tenant_id, project_id, day),
            )
        return _snapshot_from_row(rows[0]) if rows else None


def mapping_to_json(mapping: StageMapping) -> dict[str, object]:
    return {
        "statuses": {stage.value: list(mapping.statuses.get(stage, ())) for stage in STAGE_ORDER},
        "excluded_statuses": list(mapping.excluded_statuses),
        "requirement_types": list(mapping.requirement_types),
    }


def mapping_from_json(value: Mapping[str, object]) -> StageMapping:
    statuses = value.get("statuses")
    raw = statuses if isinstance(statuses, Mapping) else {}
    return StageMapping(
        statuses={stage: _strings(raw.get(stage.value)) for stage in STAGE_ORDER},
        excluded_statuses=_strings(value.get("excluded_statuses")),
        requirement_types=_strings(value.get("requirement_types")),
    )


def snapshot_to_json(snapshot: RequirementsSnapshot) -> dict[str, object]:
    return {
        "stage_counts": {stage.value: count for stage, count in snapshot.stage_counts.items()},
        "stage_points": {stage.value: points for stage, points in snapshot.stage_points.items()},
        "has_points": snapshot.has_points,
        "excluded": snapshot.excluded,
        "unmapped_statuses": list(snapshot.unmapped_statuses),
        "items": {key: stage.value for key, stage in snapshot.items.items()},
        "titles": dict(snapshot.titles),
    }


def _snapshot_from_row(row: Mapping[str, object]) -> RequirementsSnapshot:
    payload = _json(row.get("payload"))
    day = row["day"]
    computed_at = row["computed_at"]
    if not isinstance(day, date) or not isinstance(computed_at, datetime):
        raise TypeError("requirement_snapshots row has no day or computed_at")
    return RequirementsSnapshot(
        tenant_id=str(row["tenant_id"]),
        project_id=str(row["project_id"]),
        day=day,
        stage_counts={
            stage: int(count)
            for stage, count in _stage_mapping(payload.get("stage_counts")).items()
            if isinstance(count, int | float)
        },
        stage_points={
            stage: float(points)
            for stage, points in _stage_mapping(payload.get("stage_points")).items()
            if isinstance(points, int | float)
        },
        has_points=bool(payload.get("has_points")),
        excluded=int(payload.get("excluded") or 0),  # type: ignore[call-overload]
        unmapped_statuses=_strings(payload.get("unmapped_statuses")),
        items={
            str(key): stage
            for key, value in _mapping(payload.get("items")).items()
            if (stage := _stage(value)) is not None
        },
        titles={
            str(key): value
            for key, value in _mapping(payload.get("titles")).items()
            if isinstance(value, str)
        },
        computed_at=computed_at,
    )


def _stage_mapping(value: object) -> dict[DeliveryStage, object]:
    return {
        stage: item for key, item in _mapping(value).items() if (stage := _stage(key)) is not None
    }


def _stage(value: object) -> DeliveryStage | None:
    try:
        return DeliveryStage(str(value))
    except ValueError:
        return None


def _json(value: object) -> Mapping[str, object]:
    if isinstance(value, str):
        value = json.loads(value)
    return _mapping(value)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(item for item in value if isinstance(item, str))
    return ()
