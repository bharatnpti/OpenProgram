from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.directory import DirectoryUser
from core.ports.directory import DirectoryUserRepository

_tracer = trace.get_tracer("openprogram.persistence.directory")
_UPSERT_BATCH_SIZE = 1000


class AsyncSqlSession(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...


class AsyncSqlExecutor(AsyncSqlSession, Protocol):
    async def fetch(self, query: str, params: Sequence[object] = ()) -> list[dict[str, object]]: ...

    def transaction(self) -> AbstractAsyncContextManager[AsyncSqlSession]: ...


class PostgresDirectoryUserRepository(DirectoryUserRepository):
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def upsert_users(self, users: Sequence[DirectoryUser]) -> None:
        if not users:
            return
        with _tracer.start_as_current_span("postgres.directory.upsert_users"):
            async with self._executor.transaction() as transaction:
                for index in range(0, len(users), _UPSERT_BATCH_SIZE):
                    await self._upsert_batch(
                        transaction,
                        users[index : index + _UPSERT_BATCH_SIZE],
                    )

    async def _upsert_batch(
        self,
        transaction: AsyncSqlSession,
        users: Sequence[DirectoryUser],
    ) -> None:
        values: list[object] = []
        placeholders: list[str] = []
        for user in users:
            placeholders.append("(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
            values.extend(
                [
                    user.tenant_id,
                    user.external_id,
                    user.display_name,
                    user.email,
                    user.handle,
                    user.avatar_url,
                    user.title,
                    user.is_active,
                    user.source,
                    dict(user.metadata),
                    user.synced_at,
                ]
            )
        await transaction.execute(
            f"""
            INSERT INTO directory_users (
                tenant_id, external_id, display_name, email, handle, avatar_url,
                title, is_active, source, metadata, synced_at
            )
            VALUES {", ".join(placeholders)}
            ON CONFLICT (tenant_id, external_id)
            DO UPDATE SET
                display_name = EXCLUDED.display_name,
                email = EXCLUDED.email,
                handle = EXCLUDED.handle,
                avatar_url = EXCLUDED.avatar_url,
                title = EXCLUDED.title,
                is_active = EXCLUDED.is_active,
                source = EXCLUDED.source,
                metadata = EXCLUDED.metadata,
                synced_at = EXCLUDED.synced_at
            """,
            tuple(values),
        )

    async def search(
        self,
        tenant_id: str,
        query: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> list[DirectoryUser]:
        cleaned_query = query.strip()
        with _tracer.start_as_current_span("postgres.directory.search"):
            if not cleaned_query:
                rows = await self._executor.fetch(
                    """
                    SELECT tenant_id, external_id, display_name, email, handle, avatar_url,
                           title, is_active, source, metadata, synced_at
                    FROM directory_users
                    WHERE tenant_id = %s
                      AND is_active = TRUE
                    ORDER BY display_name, external_id
                    LIMIT %s OFFSET %s
                    """,
                    (tenant_id, limit, offset),
                )
                return [_user_from_row(row) for row in rows]

            like = _search_pattern(cleaned_query)
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, external_id, display_name, email, handle, avatar_url,
                       title, is_active, source, metadata, synced_at
                FROM directory_users
                WHERE tenant_id = %s
                  AND is_active = TRUE
                  AND (
                    display_name ILIKE %s
                    OR email ILIKE %s
                    OR handle ILIKE %s
                    OR external_id ILIKE %s
                  )
                ORDER BY display_name, external_id
                LIMIT %s OFFSET %s
                """,
                (tenant_id, like, like, like, like, limit, offset),
            )
        return [_user_from_row(row) for row in rows]

    async def count(self, tenant_id: str, query: str = "") -> int:
        cleaned_query = query.strip()
        with _tracer.start_as_current_span("postgres.directory.count"):
            if not cleaned_query:
                rows = await self._executor.fetch(
                    """
                    SELECT COUNT(*)::int AS count
                    FROM directory_users
                    WHERE tenant_id = %s
                      AND is_active = TRUE
                    """,
                    (tenant_id,),
                )
                return _int_value(rows[0].get("count")) if rows else 0

            like = _search_pattern(cleaned_query)
            rows = await self._executor.fetch(
                """
                SELECT COUNT(*)::int AS count
                FROM directory_users
                WHERE tenant_id = %s
                  AND is_active = TRUE
                  AND (
                    display_name ILIKE %s
                    OR email ILIKE %s
                    OR handle ILIKE %s
                    OR external_id ILIKE %s
                  )
                """,
                (tenant_id, like, like, like, like),
            )
        return _int_value(rows[0].get("count")) if rows else 0

    async def get(self, tenant_id: str, external_id: str) -> DirectoryUser | None:
        with _tracer.start_as_current_span("postgres.directory.get"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, external_id, display_name, email, handle, avatar_url,
                       title, is_active, source, metadata, synced_at
                FROM directory_users
                WHERE tenant_id = %s AND external_id = %s
                LIMIT 1
                """,
                (tenant_id, external_id),
            )
        return _user_from_row(rows[0]) if rows else None

    async def deactivate_missing(self, tenant_id: str, seen_external_ids: Sequence[str]) -> int:
        with _tracer.start_as_current_span("postgres.directory.deactivate_missing"):
            result = await self._executor.execute(
                """
                UPDATE directory_users
                SET is_active = FALSE,
                    synced_at = %s
                WHERE tenant_id = %s
                  AND is_active = TRUE
                  AND NOT (external_id = ANY(%s))
                """,
                (datetime.now(tz=UTC), tenant_id, list(seen_external_ids)),
            )
        return int(getattr(result, "rowcount", 0) or 0)


def _search_pattern(query: str) -> str:
    cleaned = query.strip()
    return f"%{cleaned}%"


def _user_from_row(row: dict[str, object]) -> DirectoryUser:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    synced_at = row.get("synced_at")
    if not isinstance(synced_at, datetime):
        synced_at = datetime.now(tz=UTC)
    return DirectoryUser(
        tenant_id=str(row["tenant_id"]),
        external_id=str(row["external_id"]),
        display_name=str(row["display_name"]),
        email=_optional_string(row.get("email")),
        handle=_optional_string(row.get("handle")),
        avatar_url=_optional_string(row.get("avatar_url")),
        title=_optional_string(row.get("title")),
        is_active=bool(row.get("is_active", True)),
        source=str(row.get("source") or "slack"),
        synced_at=synced_at,
        metadata={
            key: value
            for key, value in metadata.items()
            if isinstance(key, str)
            and (value is None or isinstance(value, str | int | float | bool))
        },
    )


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return 0


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None
