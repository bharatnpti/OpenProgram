"""The tenant's "fill the day from Jira" switch, in a table that already exists.

No table of its own (no migration): the switch is the ``checkin_fill`` object in
the tenant's ``readiness_settings.settings`` JSON, the per-tenant config row that
also holds the Jira write switches. Release readiness's own save keeps it as it
is, and a row holding only it reads as "readiness never saved".
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.observed_day import FillDaySetting

_tracer = trace.get_tracer("openprogram.persistence.fill_day")

#: The key of ``readiness_settings.settings`` that holds the switch.
FILL_DAY_KEY = "checkin_fill"


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresFillDayRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def get_fill_day(self, tenant_id: str) -> FillDaySetting | None:
        with _tracer.start_as_current_span("postgres.fill_day.get"):
            rows = await self._executor.fetch(
                "SELECT settings -> 'checkin_fill' AS checkin_fill FROM readiness_settings "
                "WHERE tenant_id = %s",
                (tenant_id,),
            )
        stored = _json(rows[0].get(FILL_DAY_KEY)) if rows else {}
        enabled = stored.get("enabled")
        if not isinstance(enabled, bool):
            return None
        updated_by = stored.get("updated_by")
        return FillDaySetting(
            tenant_id=tenant_id,
            enabled=enabled,
            updated_at=_datetime(stored.get("updated_at")),
            updated_by=updated_by if isinstance(updated_by, str) and updated_by else None,
        )

    async def save_fill_day(self, setting: FillDaySetting) -> None:
        # Only the ``checkin_fill`` key changes; the row's other keys and its
        # updated_at/updated_by (readiness's "last saved") stay as they are.
        value = json.dumps(
            {
                "enabled": setting.enabled,
                "updated_at": setting.updated_at.isoformat() if setting.updated_at else None,
                "updated_by": setting.updated_by,
            }
        )
        with _tracer.start_as_current_span("postgres.fill_day.save"):
            await self._executor.execute(
                """
                INSERT INTO readiness_settings (tenant_id, settings, updated_at, updated_by)
                VALUES (%s, jsonb_build_object('checkin_fill', %s::jsonb), now(), %s)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    settings = jsonb_set(
                        readiness_settings.settings,
                        '{checkin_fill}',
                        EXCLUDED.settings -> 'checkin_fill',
                        true
                    )
                """,
                (setting.tenant_id, value, setting.updated_by or ""),
            )


def _json(value: object) -> dict[str, object]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
