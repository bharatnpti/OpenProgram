from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from opentelemetry import trace

from core.domain.directory import DirectoryUser
from core.ports.directory import DirectoryUserRepository

_tracer = trace.get_tracer("pulseops.persistence.directory")


class PostgresDirectoryUserRepository(DirectoryUserRepository):
    def __init__(self, executor: object) -> None:
        self._executor = executor

    async def upsert_users(self, users: Sequence[DirectoryUser]) -> None:
        if not users:
            return
        with _tracer.start_as_current_span("postgres.directory.upsert_users"):
            values: list[object] = []
            placeholders: list[str] = []
            for user in users:
                placeholders.append(
                    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                )
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
            await self._executor.execute(
                f"""
                INSERT INTO directory_users (
                    tenant_id, external_id, display_name, email, handle, avatar_url,
                    title, is_active, source, metadata, synced_at
                )
                VALUES {', '.join(placeholders)}
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
        like = _search_pattern(query)
        with _tracer.start_as_current_span("postgres.directory.search"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, external_id, display_name, email, handle, avatar_url,
                       title, is_active, source, metadata, synced_at
                FROM directory_users
                WHERE tenant_id = %s
                  AND is_active = TRUE
                  AND (
                    %s = ''
                    OR display_name ILIKE %s
                    OR COALESCE(email, '') ILIKE %s
                    OR COALESCE(handle, '') ILIKE %s
                    OR external_id ILIKE %s
                  )
                ORDER BY display_name, external_id
                LIMIT %s OFFSET %s
                """,
                (tenant_id, query.strip(), like, like, like, like, limit, offset),
            )
        return [_user_from_row(row) for row in rows]

    async def count(self, tenant_id: str, query: str = "") -> int:
        like = _search_pattern(query)
        with _tracer.start_as_current_span("postgres.directory.count"):
            rows = await self._executor.fetch(
                """
                SELECT COUNT(*)::int AS count
                FROM directory_users
                WHERE tenant_id = %s
                  AND is_active = TRUE
                  AND (
                    %s = ''
                    OR display_name ILIKE %s
                    OR COALESCE(email, '') ILIKE %s
                    OR COALESCE(handle, '') ILIKE %s
                    OR external_id ILIKE %s
                  )
                """,
                (tenant_id, query.strip(), like, like, like, like),
            )
        return int(rows[0]["count"]) if rows else 0

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
        email=row.get("email") if isinstance(row.get("email"), str) else None,
        handle=row.get("handle") if isinstance(row.get("handle"), str) else None,
        avatar_url=row.get("avatar_url") if isinstance(row.get("avatar_url"), str) else None,
        title=row.get("title") if isinstance(row.get("title"), str) else None,
        is_active=bool(row.get("is_active", True)),
        source=str(row.get("source") or "slack"),
        synced_at=synced_at,
        metadata={
            key: value
            for key, value in metadata.items()
            if isinstance(key, str) and (value is None or isinstance(value, str | int | float | bool))
        },
    )
