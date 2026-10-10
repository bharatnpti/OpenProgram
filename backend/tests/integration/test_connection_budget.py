"""A burst of sync workflows stays inside one process's connection budget.

Runs DBOS in this process, the way the worker does, against a throwaway copy
of a seeded database that serves as both the application and the DBOS system
database (the compose layout). It starts 80 Git syncs through the runtime
fan-out's own path while a thread samples pg_stat_activity, and checks the
peak against the budget in docs/ops/database-connections.md.

Never point it at a database anyone uses: it writes sync results and DBOS rows.
Run it on a fresh copy of a seeded one (the local demo's, say) and drop the copy
afterwards:

    docker exec openprogram-postgres-1 createdb -U openprogram -T <seeded db> pooltest
    export OPENPROGRAM_POOL_BURST_DATABASE_URL=postgresql://openprogram:openprogram@localhost:5432/pooltest
    OPENPROGRAM_RUN_INTEGRATION=1 PYTHONPATH=backend \
      uv run pytest backend/tests/integration/test_connection_budget.py --no-cov
    docker exec openprogram-postgres-1 dropdb -U openprogram --force pooltest
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from dbos import DBOS
from psycopg.conninfo import conninfo_to_dict

from config.settings import get_settings
from core.domain.workflows import SyncDispatchInput
from infra.adapters.workflows import dbos as dbos_workflows
from infra.persistence.psycopg_executor import close_shared_executors

BURST_DATABASE_URL = os.getenv("OPENPROGRAM_POOL_BURST_DATABASE_URL", "")
BURST_SIZE = 80

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPENPROGRAM_RUN_INTEGRATION") != "1" or not BURST_DATABASE_URL,
        reason="set OPENPROGRAM_RUN_INTEGRATION=1 and OPENPROGRAM_POOL_BURST_DATABASE_URL",
    ),
]


class _ActivitySampler(threading.Thread):
    """Counts this database's connections every 20 ms, from a connection of its own."""

    def __init__(self, database_url: str) -> None:
        super().__init__(daemon=True)
        self._database_url = database_url
        self._database = conninfo_to_dict(database_url)["dbname"]
        self.stopping = threading.Event()
        self.peak = 0

    def run(self) -> None:
        with psycopg.connect(self._database_url, autocommit=True) as connection:
            while not self.stopping.is_set():
                row = connection.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (self._database,),
                ).fetchone()
                self.peak = max(self.peak, row[0] if row else 0)
                time.sleep(0.02)


@pytest.fixture
def burst_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name, value in {
        "RUNTIME_MODE": "container",
        "DATABASE_URL": BURST_DATABASE_URL,
        "DBOS_SYSTEM_DATABASE_URL": BURST_DATABASE_URL,
        # An application name of its own, so the copy's schedules never fire.
        "DBOS_APP_NAME": "openprogram-pool-burst",
        "WORKFLOW_PROVIDER": "dbos",
        "TENANT_ID": "demo",
        "VCS_PROVIDER": "fake",
        "ISSUE_TRACKER_PROVIDER": "fake",
        "CHAT_PROVIDER": "fake",
        "LLM_PROVIDER": "fake",
        "DIRECTORY_PROVIDER": "fake",
        "CALENDAR_PROVIDER": "fake",
        "REDIS_URL": os.getenv("OPENPROGRAM_POOL_BURST_REDIS_URL", "redis://127.0.0.1:1/0"),
        "SECRET_KEY": "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
    }.items():
        monkeypatch.setenv(f"OPENPROGRAM_{name}", value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_a_burst_of_git_syncs_stays_inside_the_connection_budget(
    burst_environment: None,
) -> None:
    settings = get_settings()
    # One process: its application pool, DBOS's pool and listener, and the
    # executor's one-off reachability check.
    budget = settings.postgres_pool_max_size + settings.dbos_system_pool_size + 1 + 1
    sampler = _ActivitySampler(BURST_DATABASE_URL)
    sampler.start()
    dbos_workflows.configure_dbos_runtime(
        dbos_workflows.DbosRuntimeConfig(
            app_name=settings.dbos_app_name,
            system_database_url=settings.resolved_dbos_system_database_url,
        )
    )
    DBOS.launch()
    run = uuid4().hex[:8]
    try:
        dispatch = SyncDispatchInput(
            tenant_id="demo",
            connector="vcs",
            scope="repo:openprogram",
            payload={"repo_name": "openprogram"},
        )
        workflow_ids = [
            await dbos_workflows._start_sync_child_workflow(
                dispatch, workflow_id=f"pool-burst-{run}-{index}"
            )
            for index in range(BURST_SIZE)
        ]
        failures: list[str] = []
        for workflow_id in workflow_ids:
            handle = await DBOS.retrieve_workflow_async(workflow_id)
            try:
                await handle.get_result()
            except Exception as error:  # noqa: BLE001 - every failure is reported
                failures.append(f"{workflow_id}: {type(error).__name__}: {error}")
    finally:
        sampler.stopping.set()
        sampler.join(timeout=5)
        dbos_workflows.destroy_dbos_runtime()
        await close_shared_executors()

    assert failures == []
    assert 0 < sampler.peak <= budget, f"peak {sampler.peak} connections, budget {budget}"
