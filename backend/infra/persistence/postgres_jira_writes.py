"""The tenant's Jira write switches and their change log, in tables that already exist.

No table of their own (no migration): the switches are the ``jira_writes``
object in the tenant's ``readiness_settings.settings`` JSON, the per-tenant
config row that already held the one switch for creating issues in Jira. Release
readiness's own save keeps that object as it is, and a row holding only it reads
as "readiness never saved". Each change is a row in ``readiness_actions`` with
action ``jira_writes_changed``: who (actor), when (at), and the value before and
after. The master switch stays in ``writeback_config``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from opentelemetry import trace

from core.domain.jira_writes import (
    JiraWritesChange,
    SettingSource,
    StoredJiraWrites,
    setting_value_from_json,
    setting_value_json,
    stored_from_json,
    stored_json,
)

_tracer = trace.get_tracer("openprogram.persistence.jira_writes")

#: The ``readiness_actions.action`` of a Jira writes change.
CHANGE_ACTION = "jira_writes_changed"


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresJiraWritesRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def get_jira_writes(self, tenant_id: str) -> StoredJiraWrites | None:
        with _tracer.start_as_current_span("postgres.jira_writes.get"):
            rows = await self._executor.fetch(
                "SELECT settings -> 'jira_writes' AS jira_writes FROM readiness_settings "
                "WHERE tenant_id = %s",
                (tenant_id,),
            )
        return stored_from_json(_json(rows[0].get("jira_writes"))) if rows else None

    async def save_jira_writes(
        self, tenant_id: str, stored: StoredJiraWrites, *, at: datetime, actor: str
    ) -> None:
        # Only the ``jira_writes`` key changes; the row's readiness keys and its
        # updated_at/updated_by (readiness's "last saved") stay as they are.
        with _tracer.start_as_current_span("postgres.jira_writes.save"):
            await self._executor.execute(
                """
                INSERT INTO readiness_settings (tenant_id, settings, updated_at, updated_by)
                VALUES (%s, jsonb_build_object('jira_writes', %s::jsonb), %s, %s)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    settings = jsonb_set(
                        readiness_settings.settings,
                        '{jira_writes}',
                        EXCLUDED.settings -> 'jira_writes',
                        true
                    )
                """,
                (tenant_id, json.dumps(stored_json(stored)), at, actor),
            )

    async def append_jira_writes_change(self, change: JiraWritesChange) -> None:
        with _tracer.start_as_current_span("postgres.jira_writes.append_change"):
            await self._executor.execute(
                """
                INSERT INTO readiness_actions (
                    tenant_id, action_id, at, actor, action, before, after
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    change.tenant_id,
                    f"jw_{uuid4().hex}",
                    change.at,
                    change.actor,
                    CHANGE_ACTION,
                    json.dumps(
                        {
                            "setting": change.setting,
                            "value": setting_value_json(change.before),
                            "source": change.before_source.value,
                        }
                    ),
                    json.dumps(
                        {"setting": change.setting, "value": setting_value_json(change.after)}
                    ),
                ),
            )

    async def list_jira_writes_changes(self, tenant_id: str, limit: int) -> list[JiraWritesChange]:
        with _tracer.start_as_current_span("postgres.jira_writes.list_changes"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, at, actor, before, after
                FROM readiness_actions
                WHERE tenant_id = %s AND action = %s
                ORDER BY at DESC, action_id DESC
                LIMIT %s
                """,
                (tenant_id, CHANGE_ACTION, limit),
            )
        return [change for row in rows if (change := _change(row)) is not None]


def _change(row: Mapping[str, object]) -> JiraWritesChange | None:
    before = _json(row.get("before"))
    after = _json(row.get("after"))
    at = row.get("at")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return None
    if not isinstance(at, datetime):
        return None
    try:
        source = SettingSource(str(before.get("source", SettingSource.DEFAULT.value)))
    except ValueError:
        source = SettingSource.DEFAULT
    return JiraWritesChange(
        tenant_id=str(row["tenant_id"]),
        at=at,
        actor=str(row["actor"]),
        setting=str(after.get("setting") or before.get("setting") or ""),
        before=setting_value_from_json(before.get("value")),
        after=setting_value_from_json(after.get("value")),
        before_source=source,
    )


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value
