from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    Release,
    ReleaseMatch,
    ReleaseMatchKind,
)

_tracer = trace.get_tracer("openprogram.persistence.forecast")
_CHANGE_COLUMNS = (
    "tenant_id, scope_kind, scope_id, project_id, target_date, changed_at, changed_by, note"
)
_RELEASE_COLUMNS = (
    "tenant_id, release_id, project_id, name, match_kind, match_value, updated_at, updated_by"
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
