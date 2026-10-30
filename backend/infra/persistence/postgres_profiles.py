from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.profiles import PodMemberProfile, ProfileDefinition, ProfileSource

_tracer = trace.get_tracer("openprogram.persistence.profiles")

_CATALOG_COLUMNS = "tenant_id, slug, label, code_work, title_words, sort, hidden"
_ROW_COLUMNS = "tenant_id, pod_id, member_id, profile, valid_from, source, is_lead, set_by, set_at"


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresProfileRepository:
    """``profile_catalog`` and ``pod_member_profiles`` (migration 0041)."""

    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def list_catalog(self, tenant_id: str) -> list[ProfileDefinition]:
        with _tracer.start_as_current_span("postgres.profiles.list_catalog"):
            rows = await self._executor.fetch(
                f"SELECT {_CATALOG_COLUMNS} FROM profile_catalog WHERE tenant_id = %s",
                (tenant_id,),
            )
        return [_entry(row) for row in rows]

    async def save_catalog_entry(
        self, tenant_id: str, entry: ProfileDefinition, *, updated_by: str
    ) -> None:
        with _tracer.start_as_current_span("postgres.profiles.save_catalog_entry"):
            await self._executor.execute(
                f"""
                INSERT INTO profile_catalog ({_CATALOG_COLUMNS}, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, now())
                ON CONFLICT (tenant_id, slug) DO UPDATE SET
                    label = EXCLUDED.label,
                    code_work = EXCLUDED.code_work,
                    title_words = EXCLUDED.title_words,
                    sort = EXCLUDED.sort,
                    hidden = EXCLUDED.hidden,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    tenant_id,
                    entry.slug,
                    entry.label,
                    entry.code_work,
                    json.dumps(list(entry.title_words)),
                    entry.sort,
                    entry.hidden,
                    updated_by,
                ),
            )

    async def list_member_profiles(
        self,
        tenant_id: str,
        *,
        pod_ids: Sequence[str] | None = None,
        member_id: str | None = None,
    ) -> list[PodMemberProfile]:
        clauses = ["tenant_id = %s"]
        params: list[object] = [tenant_id]
        if pod_ids is not None:
            if not pod_ids:
                return []
            clauses.append("pod_id = ANY(%s)")
            params.append(list(pod_ids))
        if member_id is not None:
            clauses.append("member_id = %s")
            params.append(member_id)
        with _tracer.start_as_current_span("postgres.profiles.list_member_profiles"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_ROW_COLUMNS} FROM pod_member_profiles
                WHERE {" AND ".join(clauses)}
                ORDER BY pod_id, member_id, valid_from
                """,
                tuple(params),
            )
        return [_row(row) for row in rows]

    async def save_member_profile(self, row: PodMemberProfile) -> None:
        with _tracer.start_as_current_span("postgres.profiles.save_member_profile"):
            await self._executor.execute(
                f"""
                INSERT INTO pod_member_profiles ({_ROW_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, pod_id, member_id, valid_from) DO UPDATE SET
                    profile = EXCLUDED.profile,
                    source = EXCLUDED.source,
                    is_lead = EXCLUDED.is_lead,
                    set_by = EXCLUDED.set_by,
                    set_at = EXCLUDED.set_at
                """,
                (
                    row.tenant_id,
                    row.pod_id,
                    row.member_id,
                    row.profile,
                    row.valid_from,
                    row.source.value,
                    row.is_lead,
                    row.set_by,
                    row.set_at,
                ),
            )


def _entry(row: Mapping[str, object]) -> ProfileDefinition:
    raw = row.get("title_words")
    words = json.loads(raw) if isinstance(raw, str) else raw
    sort = row.get("sort")
    return ProfileDefinition(
        slug=str(row["slug"]),
        label=str(row.get("label") or row["slug"]),
        code_work=bool(row.get("code_work")),
        title_words=tuple(str(word) for word in words) if isinstance(words, list) else (),
        sort=sort if isinstance(sort, int) else 100,
        hidden=bool(row.get("hidden")),
        customized=True,
    )


def _row(row: Mapping[str, object]) -> PodMemberProfile:
    valid_from = row.get("valid_from")
    set_at = row.get("set_at")
    try:
        source = ProfileSource(str(row.get("source")))
    except ValueError:
        source = ProfileSource.BACKFILL
    return PodMemberProfile(
        tenant_id=str(row["tenant_id"]),
        pod_id=str(row["pod_id"]),
        member_id=str(row["member_id"]),
        profile=str(row["profile"]),
        valid_from=valid_from if isinstance(valid_from, date) else date(1970, 1, 1),
        source=source,
        is_lead=bool(row.get("is_lead")),
        set_by=str(row["set_by"]) if row.get("set_by") else None,
        set_at=set_at if isinstance(set_at, datetime) else None,
    )
