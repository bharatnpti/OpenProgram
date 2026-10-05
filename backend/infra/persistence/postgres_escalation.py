from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.escalation_matrix import (
    ContactSource,
    EscalationLevel,
    EscalationMatrix,
    NeedType,
)

_tracer = trace.get_tracer("openprogram.persistence.escalation")

_COLUMNS = "tenant_id, project_id, decision_owner_id, levels, updated_at, updated_by"


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresEscalationMatrixRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def get(self, tenant_id: str, project_id: str) -> EscalationMatrix | None:
        with _tracer.start_as_current_span("postgres.escalation.get"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_COLUMNS} FROM escalation_matrices
                WHERE tenant_id = %s AND project_id = %s
                """,
                (tenant_id, project_id),
            )
        return _matrix(rows[0]) if rows else None

    async def list(self, tenant_id: str) -> list[EscalationMatrix]:
        with _tracer.start_as_current_span("postgres.escalation.list"):
            rows = await self._executor.fetch(
                f"SELECT {_COLUMNS} FROM escalation_matrices WHERE tenant_id = %s",
                (tenant_id,),
            )
        return [_matrix(row) for row in rows]

    async def save(self, matrix: EscalationMatrix) -> None:
        with _tracer.start_as_current_span("postgres.escalation.save"):
            await self._executor.execute(
                f"""
                INSERT INTO escalation_matrices ({_COLUMNS})
                VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (tenant_id, project_id) DO UPDATE SET
                    decision_owner_id = EXCLUDED.decision_owner_id,
                    levels = EXCLUDED.levels,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                """,
                (
                    matrix.tenant_id,
                    matrix.project_id,
                    matrix.decision_owner_id,
                    json.dumps(
                        [
                            {
                                "label": level.label,
                                "source": level.source.value,
                                "member_id": level.member_id,
                                "after_days": {
                                    need.value: days for need, days in level.after_days.items()
                                },
                            }
                            for level in matrix.levels
                        ]
                    ),
                    matrix.updated_at,
                    matrix.updated_by or "",
                ),
            )

    async def delete(self, tenant_id: str, project_id: str) -> bool:
        with _tracer.start_as_current_span("postgres.escalation.delete"):
            rows = await self._executor.fetch(
                """
                DELETE FROM escalation_matrices WHERE tenant_id = %s AND project_id = %s
                RETURNING project_id
                """,
                (tenant_id, project_id),
            )
        return bool(rows)


def _matrix(row: Mapping[str, object]) -> EscalationMatrix:
    updated_at = row.get("updated_at")
    raw = row.get("levels")
    levels = json.loads(raw) if isinstance(raw, str) else raw
    return EscalationMatrix(
        tenant_id=str(row["tenant_id"]),
        project_id=str(row["project_id"]),
        decision_owner_id=str(row["decision_owner_id"]) if row.get("decision_owner_id") else None,
        levels=tuple(
            level
            for item in (levels if isinstance(levels, list) else [])
            if (level := _level(item))
        ),
        updated_at=updated_at if isinstance(updated_at, datetime) else None,
        updated_by=str(row["updated_by"]) if row.get("updated_by") else None,
    )


def _level(item: object) -> EscalationLevel | None:
    if not isinstance(item, Mapping):
        return None
    try:
        source = ContactSource(str(item.get("source")))
    except ValueError:
        return None
    days = item.get("after_days")
    after_days: dict[NeedType, int] = {}
    for key, value in days.items() if isinstance(days, Mapping) else ():
        try:
            after_days[NeedType(str(key))] = int(value)
        except (TypeError, ValueError):
            continue
    member = item.get("member_id")
    return EscalationLevel(
        label=str(item.get("label") or ""),
        source=source,
        member_id=str(member) if member else None,
        after_days=after_days,
    )
