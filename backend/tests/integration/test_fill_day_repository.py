"""The fill switch round-trips through Postgres beside release readiness's settings
and the Jira write switches, all in ``readiness_settings.settings``.

Never point it at a database anyone uses: run it on a copy migrated to 0040,
under a tenant of its own:

    export OPENPROGRAM_READINESS_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/<copy>
    OPENPROGRAM_RUN_INTEGRATION=1 PYTHONPATH=backend \
      uv run pytest backend/tests/integration/test_fill_day_repository.py --no-cov
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from core.domain.jira_writes import JiraWriteKind, StoredJiraWrites
from core.domain.observed_day import FillDaySetting
from core.domain.release_readiness import ReadinessSettings
from infra.persistence.postgres_fill_day import PostgresFillDayRepository
from infra.persistence.postgres_jira_writes import PostgresJiraWritesRepository
from infra.persistence.postgres_readiness import PostgresReleaseReadinessRepository
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor

DATABASE_URL = os.getenv("OPENPROGRAM_READINESS_DATABASE_URL", "")
AT = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPENPROGRAM_RUN_INTEGRATION") != "1" or not DATABASE_URL,
        reason="set OPENPROGRAM_RUN_INTEGRATION=1 and OPENPROGRAM_READINESS_DATABASE_URL",
    ),
]


async def test_the_fill_switch_and_the_row_s_other_settings_keep_each_other() -> None:
    executor = PsycopgAsyncExecutor(DATABASE_URL, min_size=1, max_size=2)
    fill = PostgresFillDayRepository(executor)
    writes = PostgresJiraWritesRepository(executor)
    readiness = PostgresReleaseReadinessRepository(executor)
    tenant = f"fd-test-{uuid4().hex[:8]}"
    try:
        assert await fill.get_fill_day(tenant) is None

        # Saved first, the switch alone is not a save of readiness's settings.
        off = FillDaySetting(tenant_id=tenant, enabled=False, updated_at=AT, updated_by="U1001")
        await fill.save_fill_day(off)
        assert await fill.get_fill_day(tenant) == off
        assert await readiness.get_settings(tenant) is None

        # Jira writes and readiness saves keep it; it keeps theirs.
        stored = StoredJiraWrites(kinds={JiraWriteKind.CHECKIN_UPDATES: False})
        await writes.save_jira_writes(tenant, stored, at=AT, actor="U1001")
        await readiness.save_settings(
            ReadinessSettings(tenant_id=tenant, enabled=True, updated_at=AT, updated_by="U1003")
        )
        assert await fill.get_fill_day(tenant) == off
        assert await writes.get_jira_writes(tenant) == stored
        on = FillDaySetting(tenant_id=tenant, enabled=True, updated_at=AT, updated_by="U1001")
        await fill.save_fill_day(on)
        assert await fill.get_fill_day(tenant) == on
        assert await writes.get_jira_writes(tenant) == stored
        kept = await readiness.get_settings(tenant)
        assert kept is not None and (kept.enabled, kept.updated_by) == (True, "U1003")
    finally:
        await executor.execute("DELETE FROM readiness_settings WHERE tenant_id = %s", (tenant,))
        await executor.close()
