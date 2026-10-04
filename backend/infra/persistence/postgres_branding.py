from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.branding import LogoContentType, TenantLogo

_tracer = trace.get_tracer("openprogram.persistence.branding")


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresTenantLogoRepository:
    """Tenant logos in ``tenant_logos``, keyed by tenant so each has at most one."""

    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def get_logo(self, tenant_id: str) -> TenantLogo | None:
        with _tracer.start_as_current_span("postgres.branding.get_logo"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, content_type, data, sha256, updated_at, updated_by
                FROM tenant_logos
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            )
        return _logo_from_row(rows[0]) if rows else None

    async def save_logo(self, logo: TenantLogo) -> None:
        with _tracer.start_as_current_span("postgres.branding.save_logo"):
            await self._executor.execute(
                """
                INSERT INTO tenant_logos (
                    tenant_id, content_type, data, sha256, updated_at, updated_by
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    content_type = EXCLUDED.content_type,
                    data = EXCLUDED.data,
                    sha256 = EXCLUDED.sha256,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                """,
                (
                    logo.tenant_id,
                    logo.content_type.value,
                    logo.data,
                    logo.sha256,
                    logo.updated_at,
                    logo.updated_by,
                ),
            )

    async def delete_logo(self, tenant_id: str) -> bool:
        with _tracer.start_as_current_span("postgres.branding.delete_logo"):
            rows = await self._executor.fetch(
                """
                DELETE FROM tenant_logos
                WHERE tenant_id = %s
                RETURNING tenant_id
                """,
                (tenant_id,),
            )
        return bool(rows)


def _logo_from_row(row: Mapping[str, object]) -> TenantLogo:
    return TenantLogo(
        tenant_id=str(row["tenant_id"]),
        content_type=LogoContentType(str(row["content_type"])),
        data=_bytes(row["data"]),
        sha256=str(row["sha256"]),
        updated_at=_datetime(row["updated_at"]),
        updated_by=str(row["updated_by"]),
    )


def _bytes(value: object) -> bytes:
    # psycopg returns BYTEA as bytes; other drivers hand back a memoryview.
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value)
    raise TypeError("tenant_logos row has no image bytes")


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    raise TypeError("tenant_logos row has no updated_at timestamp")
