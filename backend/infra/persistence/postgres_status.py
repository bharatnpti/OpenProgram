from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.graph import EntityRef, JsonScalar, NodeKind
from core.domain.integrations import SyncCursor
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import CheckIn, CheckInSignals, DeveloperStatus, Mood, StatusSource

_tracer = trace.get_tracer("pulseops.persistence.status")


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresStatusRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def record_checkin(self, checkin: CheckIn) -> None:
        with _tracer.start_as_current_span("postgres.status.record_checkin"):
            await self._executor.execute(
                """
                INSERT INTO checkins (
                    tenant_id, developer_id, correlation_id, asked_at,
                    replied_at, raw_reply, signals
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, correlation_id)
                DO UPDATE SET
                    developer_id = EXCLUDED.developer_id,
                    asked_at = EXCLUDED.asked_at,
                    replied_at = EXCLUDED.replied_at,
                    raw_reply = EXCLUDED.raw_reply,
                    signals = EXCLUDED.signals
                """,
                (
                    checkin.tenant_id,
                    checkin.developer_id,
                    checkin.correlation_id,
                    checkin.asked_at,
                    checkin.replied_at,
                    checkin.raw_reply,
                    _signals_to_json(checkin.signals),
                ),
            )

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> CheckIn | None:
        with _tracer.start_as_current_span("postgres.status.checkin_by_correlation"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, developer_id, correlation_id, asked_at,
                       replied_at, raw_reply, signals
                FROM checkins
                WHERE tenant_id = %s AND correlation_id = %s
                LIMIT 1
                """,
                (tenant_id, correlation_id),
            )
        return _checkin_from_row(rows[0]) if rows else None

    async def record_developer_status(self, status: DeveloperStatus) -> None:
        with _tracer.start_as_current_span("postgres.status.record_developer_status"):
            await self._executor.execute(
                """
                INSERT INTO developer_statuses (
                    tenant_id, developer_id, as_of, source, blockers, summary
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, developer_id, as_of)
                DO UPDATE SET
                    source = EXCLUDED.source,
                    blockers = EXCLUDED.blockers,
                    summary = EXCLUDED.summary,
                    updated_at = now()
                """,
                (
                    status.tenant_id,
                    status.developer_id,
                    status.as_of,
                    status.source.value,
                    _string_tuple_to_json(status.blockers),
                    status.summary,
                ),
            )

    async def latest_developer_status(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> DeveloperStatus | None:
        with _tracer.start_as_current_span("postgres.status.latest_developer_status"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, developer_id, as_of, source, blockers, summary
                FROM developer_statuses
                WHERE tenant_id = %s AND developer_id = %s AND as_of <= %s
                ORDER BY as_of DESC
                LIMIT 1
                """,
                (tenant_id, developer_id, as_of),
            )
        return _developer_status_from_row(rows[0]) if rows else None

    async def developers_without_checkin(self, tenant_id: str, as_of: date) -> list[str]:
        with _tracer.start_as_current_span("postgres.status.developers_without_checkin"):
            rows = await self._executor.fetch(
                """
                WITH known AS (
                    SELECT id AS developer_id
                    FROM graph_nodes
                    WHERE tenant_id = %s AND kind = 'developer'
                  UNION
                    SELECT developer_id
                    FROM developer_statuses
                    WHERE tenant_id = %s
                  UNION
                    SELECT developer_id
                    FROM checkins
                    WHERE tenant_id = %s
                )
                SELECT known.developer_id
                FROM known
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM checkins replied
                    WHERE replied.tenant_id = %s
                      AND replied.developer_id = known.developer_id
                      AND replied.replied_at >= %s::date
                      AND replied.replied_at < (%s::date + INTERVAL '1 day')
                )
                ORDER BY known.developer_id
                """,
                (tenant_id, tenant_id, tenant_id, tenant_id, as_of, as_of),
            )
        return [str(row["developer_id"]) for row in rows]


class PostgresRollupRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def record_node_status(self, status: NodeStatus) -> None:
        with _tracer.start_as_current_span("postgres.rollup.record_node_status"):
            await self._executor.execute(
                """
                INSERT INTO node_statuses (
                    tenant_id, entity_kind, entity_id, as_of, rag, source, factors
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, entity_kind, entity_id, as_of)
                DO UPDATE SET
                    rag = EXCLUDED.rag,
                    source = EXCLUDED.source,
                    factors = EXCLUDED.factors,
                    updated_at = now()
                """,
                (
                    status.entity_ref.tenant_id,
                    status.entity_ref.kind.value,
                    status.entity_ref.id,
                    status.as_of,
                    status.rag.value,
                    status.source.value,
                    _factors_to_json(status.factors),
                ),
            )

    async def latest_node_status(
        self, tenant_id: str, entity_ref: EntityRef, as_of: date
    ) -> NodeStatus | None:
        with _tracer.start_as_current_span("postgres.rollup.latest_node_status"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, entity_kind, entity_id, as_of, rag, source, factors
                FROM node_statuses
                WHERE tenant_id = %s
                  AND entity_kind = %s
                  AND entity_id = %s
                  AND as_of <= %s
                ORDER BY as_of DESC
                LIMIT 1
                """,
                (tenant_id, entity_ref.kind.value, entity_ref.id, as_of),
            )
        return _node_status_from_row(rows[0]) if rows else None

    async def list_node_statuses(self, tenant_id: str, as_of: date) -> list[NodeStatus]:
        with _tracer.start_as_current_span("postgres.rollup.list_node_statuses"):
            rows = await self._executor.fetch(
                """
                SELECT DISTINCT ON (entity_kind, entity_id)
                    tenant_id, entity_kind, entity_id, as_of, rag, source, factors
                FROM node_statuses
                WHERE tenant_id = %s AND as_of <= %s
                ORDER BY entity_kind, entity_id, as_of DESC
                """,
                (tenant_id, as_of),
            )
        return [_node_status_from_row(row) for row in rows]


class PostgresSyncCursorRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def get_cursor(self, tenant_id: str, connector: str, scope: str) -> SyncCursor:
        with _tracer.start_as_current_span("postgres.cursor.get_cursor"):
            rows = await self._executor.fetch(
                """
                SELECT cursor_value, cursor_updated_at, metadata
                FROM connector_sync_cursors
                WHERE tenant_id = %s AND connector = %s AND scope = %s
                """,
                (tenant_id, connector, scope),
            )
        return _sync_cursor_from_row(rows[0]) if rows else SyncCursor()

    async def record_cursor(
        self, tenant_id: str, connector: str, scope: str, cursor: SyncCursor
    ) -> None:
        with _tracer.start_as_current_span("postgres.cursor.record_cursor"):
            await self._executor.execute(
                """
                INSERT INTO connector_sync_cursors (
                    tenant_id, connector, scope, cursor_value, cursor_updated_at, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, connector, scope)
                DO UPDATE SET
                    cursor_value = EXCLUDED.cursor_value,
                    cursor_updated_at = EXCLUDED.cursor_updated_at,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """,
                (
                    tenant_id,
                    connector,
                    scope,
                    cursor.value,
                    cursor.updated_at,
                    _json_scalar_mapping(cursor.metadata),
                ),
            )


def _checkin_from_row(row: Mapping[str, object]) -> CheckIn:
    raw_reply = row.get("raw_reply")
    return CheckIn(
        tenant_id=str(row["tenant_id"]),
        developer_id=str(row["developer_id"]),
        correlation_id=str(row["correlation_id"]),
        asked_at=_datetime_field(row["asked_at"], "asked_at"),
        replied_at=_optional_datetime_field(row.get("replied_at"), "replied_at"),
        raw_reply=raw_reply if isinstance(raw_reply, str) else None,
        signals=_signals_from_json(row.get("signals")),
    )


def _developer_status_from_row(row: Mapping[str, object]) -> DeveloperStatus:
    return DeveloperStatus(
        tenant_id=str(row["tenant_id"]),
        developer_id=str(row["developer_id"]),
        as_of=_date_field(row["as_of"], "as_of"),
        source=StatusSource(str(row["source"])),
        blockers=_string_tuple_from_json(row.get("blockers")),
        summary=str(row["summary"]),
    )


def _node_status_from_row(row: Mapping[str, object]) -> NodeStatus:
    return NodeStatus(
        entity_ref=EntityRef(
            tenant_id=str(row["tenant_id"]),
            kind=NodeKind(str(row["entity_kind"])),
            id=str(row["entity_id"]),
        ),
        rag=Rag(str(row["rag"])),
        source=StatusSource(str(row["source"])),
        factors=_factors_from_json(row.get("factors")),
        as_of=_date_field(row["as_of"], "as_of"),
    )


def _sync_cursor_from_row(row: Mapping[str, object]) -> SyncCursor:
    cursor_value = row.get("cursor_value")
    return SyncCursor(
        value=cursor_value if isinstance(cursor_value, str) else None,
        updated_at=_optional_datetime_field(row.get("cursor_updated_at"), "cursor_updated_at"),
        metadata=_json_scalar_mapping(row.get("metadata")),
    )


def _signals_to_json(signals: CheckInSignals | None) -> dict[str, object] | None:
    if signals is None:
        return None
    return {
        "progress_note": signals.progress_note,
        "blockers": list(signals.blockers),
        "eta_change_days": signals.eta_change_days,
        "mood": signals.mood.value if signals.mood else None,
    }


def _signals_from_json(value: object) -> CheckInSignals | None:
    if not isinstance(value, Mapping):
        return None
    progress_note = value.get("progress_note")
    if not isinstance(progress_note, str):
        return None
    eta_change_days = value.get("eta_change_days")
    return CheckInSignals(
        progress_note=progress_note,
        blockers=_string_tuple_from_json(value.get("blockers")),
        eta_change_days=eta_change_days
        if isinstance(eta_change_days, int) and not isinstance(eta_change_days, bool)
        else None,
        mood=_mood_from_json(value.get("mood")),
    )


def _string_tuple_to_json(values: tuple[str, ...]) -> dict[str, object]:
    return {"items": list(values)}


def _string_tuple_from_json(value: object) -> tuple[str, ...]:
    items = _items_from_json(value)
    return tuple(item for item in items if isinstance(item, str))


def _factors_to_json(factors: tuple[RollupFactor, ...]) -> dict[str, object]:
    return {"items": [_factor_to_json(factor) for factor in factors]}


def _factor_to_json(factor: RollupFactor) -> dict[str, object]:
    return {
        "description": factor.description,
        "contributes": factor.contributes.value,
        "source_ref": {
            "tenant_id": factor.source_ref.tenant_id,
            "kind": factor.source_ref.kind.value,
            "id": factor.source_ref.id,
        },
    }


def _factors_from_json(value: object) -> tuple[RollupFactor, ...]:
    factors: list[RollupFactor] = []
    for item in _items_from_json(value):
        factor = _factor_from_json(item)
        if factor is not None:
            factors.append(factor)
    return tuple(factors)


def _factor_from_json(value: object) -> RollupFactor | None:
    if not isinstance(value, Mapping):
        return None
    description = value.get("description")
    contributes = _rag_from_json(value.get("contributes"))
    source_ref = _entity_ref_from_json(value.get("source_ref"))
    if not isinstance(description, str) or contributes is None or source_ref is None:
        return None
    return RollupFactor(
        description=description,
        contributes=contributes,
        source_ref=source_ref,
    )


def _entity_ref_from_json(value: object) -> EntityRef | None:
    if not isinstance(value, Mapping):
        return None
    tenant_id = value.get("tenant_id")
    kind = value.get("kind")
    entity_id = value.get("id")
    if (
        not isinstance(tenant_id, str)
        or not isinstance(kind, str)
        or not isinstance(entity_id, str)
    ):
        return None
    try:
        node_kind = NodeKind(kind)
    except ValueError:
        return None
    return EntityRef(tenant_id=tenant_id, kind=node_kind, id=entity_id)


def _items_from_json(value: object) -> tuple[object, ...]:
    raw_items = value.get("items") if isinstance(value, Mapping) else value
    if isinstance(raw_items, list | tuple):
        return tuple(raw_items)
    return ()


def _mood_from_json(value: object) -> Mood | None:
    if not isinstance(value, str):
        return None
    try:
        return Mood(value)
    except ValueError:
        return None


def _rag_from_json(value: object) -> Rag | None:
    if not isinstance(value, str):
        return None
    try:
        return Rag(value)
    except ValueError:
        return None


def _json_scalar_mapping(value: object) -> dict[str, JsonScalar]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, JsonScalar] = {}
    for key, item in value.items():
        if isinstance(key, str) and (item is None or isinstance(item, str | int | float | bool)):
            result[key] = item
    return result


def _datetime_field(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    raise TypeError(f"{field_name} must be a datetime instance")


def _optional_datetime_field(value: object, field_name: str) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    raise TypeError(f"{field_name} must be a datetime instance or None")


def _date_field(value: object, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raise TypeError(f"{field_name} must be a date instance")
