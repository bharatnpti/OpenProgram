"""The Jira write switches round-trip through Postgres beside release readiness's own settings.

They live in ``readiness_settings.settings -> 'jira_writes'`` and their change log
in ``readiness_actions``, with no table of their own. Never point it at a database
anyone uses: run it on a copy migrated to 0040, under a tenant of its own:

    export OPENPROGRAM_READINESS_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/<copy>
    OPENPROGRAM_RUN_INTEGRATION=1 PYTHONPATH=backend \
      uv run pytest backend/tests/integration/test_jira_writes_repository.py --no-cov
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from core.domain.jira_writes import (
    CreateProjects,
    JiraWriteKind,
    JiraWritesChange,
    SettingSource,
    StoredJiraWrites,
)
from core.domain.release_readiness import ReadinessSettings
from infra.persistence.postgres_jira_writes import PostgresJiraWritesRepository
from infra.persistence.postgres_readiness import PostgresReleaseReadinessRepository
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor

DATABASE_URL = os.getenv("OPENPROGRAM_READINESS_DATABASE_URL", "")
AT = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPENPROGRAM_RUN_INTEGRATION") != "1" or not DATABASE_URL,
        reason="set OPENPROGRAM_RUN_INTEGRATION=1 and OPENPROGRAM_READINESS_DATABASE_URL",
    ),
]


async def test_the_switches_and_readiness_settings_keep_each_other() -> None:
    executor = PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=2)
    writes = PostgresJiraWritesRepository(executor)
    readiness = PostgresReleaseReadinessRepository(executor)
    tenant = f"jw-test-{uuid4().hex[:8]}"
    try:
        assert await writes.get_jira_writes(tenant) is None

        # Saved first, the switches alone are not a save of readiness's settings.
        stored = StoredJiraWrites(
            kinds={JiraWriteKind.CHECKIN_UPDATES: False},
            create_projects=CreateProjects(own_project=True, projects=("SEC",)),
        )
        await writes.save_jira_writes(tenant, stored, at=AT, actor="U1001")
        assert await writes.get_jira_writes(tenant) == stored
        assert await readiness.get_settings(tenant) is None

        # Readiness's save keeps them; theirs keeps readiness's keys and its "saved by".
        settings = ReadinessSettings(
            tenant_id=tenant, enabled=True, create_in_jira=False, updated_at=AT, updated_by="U1003"
        )
        await readiness.save_settings(settings)
        assert await writes.get_jira_writes(tenant) == stored
        more = replace(stored, kinds={**stored.kinds, JiraWriteKind.READINESS_CREATE: True})
        await writes.save_jira_writes(tenant, more, at=AT + timedelta(hours=1), actor="U1001")
        assert await writes.get_jira_writes(tenant) == more
        kept = await readiness.get_settings(tenant)
        assert kept is not None
        assert (kept.enabled, kept.updated_by, kept.updated_at) == (True, "U1003", AT)

        first = JiraWritesChange(
            tenant_id=tenant,
            at=AT,
            actor="U1001",
            setting="master",
            before=False,
            after=True,
            before_source=SettingSource.DEFAULT,
        )
        second = JiraWritesChange(
            tenant_id=tenant,
            at=AT + timedelta(minutes=5),
            actor="U1001",
            setting="create_projects",
            before=CreateProjects(),
            after=CreateProjects(own_project=True, projects=("SEC",)),
            before_source=SettingSource.DEFAULT,
        )
        await writes.append_jira_writes_change(first)
        await writes.append_jira_writes_change(second)
        assert await writes.list_jira_writes_changes(tenant, 10) == [second, first]
        assert await writes.list_jira_writes_changes(tenant, 1) == [second]
        # A finding's history never lists them.
        assert await readiness.list_actions(tenant, "rf_any") == []
    finally:
        for table in ("readiness_actions", "readiness_settings"):
            await executor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant,))
        await executor.close()
