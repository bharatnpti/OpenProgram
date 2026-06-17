from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet

from config.settings import get_settings
from core.domain.graph import EntityRef, NodeKind
from core.domain.workflows import CheckinScheduleConfig, HeartbeatInput
from core.ports.secrets import SecretRef
from infra.adapters.chat.rate_limit import RedisRateLimiter
from infra.adapters.redis_client import RedisClientProvider
from infra.adapters.secrets.encrypted import FernetSecretStore, PostgresEncryptedSecretRecordStore
from infra.adapters.workflows.temporal import HeartbeatWorkflow, record_heartbeat_activity
from infra.persistence.postgres_graph import (
    PostgresGraphRepository,
    PostgresTimeSeriesRepository,
    PostgresVectorStore,
)
from infra.persistence.postgres_status import PostgresRollupRepository, PostgresStatusRepository
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor
from infra.persistence.seed_data import seed_demo_graph

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("PULSEOPS_RUN_INTEGRATION") != "1",
        reason="set PULSEOPS_RUN_INTEGRATION=1 or run make integration",
    ),
]

ROOT = Path(__file__).resolve().parents[3]
SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="


@pytest.fixture(scope="session")
def compose_stack() -> object:
    try:
        import docker
        from testcontainers.compose import DockerCompose
    except Exception as exc:  # pragma: no cover - import depends on optional docker env
        pytest.skip(f"docker test dependencies unavailable: {exc}")

    try:
        docker.from_env().ping()
    except Exception as exc:
        pytest.skip(f"docker daemon unavailable: {exc}")

    compose_env = {
        "COMPOSE_PROJECT_NAME": f"pulseops-it-{uuid4().hex[:12]}",
        "PULSEOPS_POSTGRES_PORT_BINDING": "5432",
        "PULSEOPS_REDIS_PORT_BINDING": "6379",
        "PULSEOPS_TEMPORAL_PORT_BINDING": "7233",
    }
    previous_env = {key: os.environ.get(key) for key in compose_env}
    os.environ.update(compose_env)
    try:
        with DockerCompose(
            ROOT,
            compose_file_name="docker-compose.yml",
            build=True,
            wait=True,
            services=["postgres", "redis", "temporal"],
        ) as compose:
            yield compose
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def test_postgres_extensions_seed_vector_and_secret(
    compose_stack: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = _service_url(compose_stack, "postgres", 5432, "pulseops")
    monkeypatch.setenv("PULSEOPS_DATABASE_URL", database_url)
    monkeypatch.setenv("PULSEOPS_SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("PULSEOPS_RUNTIME_MODE", "container")
    get_settings.cache_clear()
    command.upgrade(Config(str(ROOT / "backend/infra/persistence/alembic.ini")), "head")

    executor = PsycopgAsyncExecutor(database_url)
    extension_rows = await executor.fetch(
        "SELECT extname FROM pg_extension WHERE extname IN ('age', 'timescaledb', 'vector')"
    )
    assert {str(row["extname"]) for row in extension_rows} == {"age", "timescaledb", "vector"}

    graph_repository = PostgresGraphRepository(executor)
    time_series_repository = PostgresTimeSeriesRepository(executor)
    status_repository = PostgresStatusRepository(executor)
    rollup_repository = PostgresRollupRepository(executor)
    vector_store = PostgresVectorStore(executor)
    try:
        await seed_demo_graph(
            graph_repository,
            time_series_repository,
            "demo",
            status_repository=status_repository,
            rollup_repository=rollup_repository,
        )
        tree = await graph_repository.get_program_tree("demo", "program-platform", date.today())
        assert tree.root.id == "program-platform"

        ref = EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-api")
        seeded_facts = await time_series_repository.list_facts("demo", ref)
        recent_seeded_facts = await time_series_repository.list_facts(
            "demo",
            ref,
            datetime(2026, 6, 1, tzinfo=UTC),
        )
        future_seeded_facts = await time_series_repository.list_facts(
            "demo",
            ref,
            datetime(2026, 6, 16, tzinfo=UTC),
        )
        await seed_demo_graph(
            graph_repository,
            time_series_repository,
            "demo",
            status_repository=status_repository,
            rollup_repository=rollup_repository,
        )
        assert await time_series_repository.list_facts("demo", ref) == seeded_facts
        assert recent_seeded_facts == seeded_facts
        assert future_seeded_facts == []

        confirmed = await status_repository.latest_developer_status(
            "demo",
            "dev-asha",
            date(2026, 6, 15),
        )
        rollups = await rollup_repository.list_node_statuses("demo", date(2026, 6, 15))
        assert confirmed is not None
        assert confirmed.source.value == "confirmed"
        assert rollups

        vector = [0.0] * 1536
        vector[0] = 1.0
        await vector_store.upsert_embedding("demo", ref, vector)
        matches = await vector_store.search("demo", vector, limit=1)
        assert matches[0].entity_ref == ref
        assert matches[0].score == pytest.approx(1.0)

        secret_store = FernetSecretStore(
            Fernet(SECRET_KEY.encode("utf-8")),
            PostgresEncryptedSecretRecordStore(executor),
        )
        secret_ref = SecretRef(tenant_id="demo", connector="slack", key="bot_token")
        await secret_store.put(secret_ref, "xoxb-secret")
        assert await secret_store.get(secret_ref) == "xoxb-secret"
    finally:
        await executor.close()


async def test_redis_rate_limiter_uses_container(compose_stack: object) -> None:
    redis_url = _redis_url(compose_stack)
    redis_provider = RedisClientProvider(redis_url=redis_url, max_connections=2)
    try:
        limiter = RedisRateLimiter(
            client=redis_provider.client(),
            window_seconds=1,
            max_events=100,
        )
        await limiter.acquire("integration:rate-limit")
    finally:
        await redis_provider.close()


async def test_dbos_worker_executes_heartbeat(compose_stack: object) -> None:
    from dbos import DBOS, SetWorkflowID

    from infra.adapters.workflows.dbos import (
        DbosRuntimeConfig,
        configure_dbos_runtime,
        dbos_heartbeat_workflow,
        destroy_dbos_runtime,
    )

    database_url = _service_url(compose_stack, "postgres", 5432, "pulseops")
    configure_dbos_runtime(
        DbosRuntimeConfig(
            app_name="pulseops-it",
            system_database_url=database_url,
        )
    )
    DBOS.launch()
    try:
        with SetWorkflowID(f"dbos-heartbeat-it-{uuid4()}"):
            handle = await DBOS.start_workflow_async(
                dbos_heartbeat_workflow,
                HeartbeatInput(tenant_id="demo", heartbeat_id=f"it-{uuid4()}"),
            )
        result = await handle.get_result()
    finally:
        destroy_dbos_runtime()

    assert result.status == "ok"


async def test_dbos_worker_executes_read_sync_workflow(
    compose_stack: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dbos import DBOS, SetWorkflowID

    from infra.adapters.workflows.dbos import (
        DbosRuntimeConfig,
        configure_dbos_runtime,
        dbos_jira_sync_workflow,
        destroy_dbos_runtime,
    )
    from infra.workflows.jira_sync import JiraSyncInput

    database_url = _service_url(compose_stack, "postgres", 5432, "pulseops")
    monkeypatch.setenv("PULSEOPS_SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("PULSEOPS_RUNTIME_MODE", "memory")
    monkeypatch.setenv("PULSEOPS_CHAT_PROVIDER", "fake")
    monkeypatch.setenv("PULSEOPS_ISSUE_TRACKER_PROVIDER", "fake")
    monkeypatch.setenv("PULSEOPS_VCS_PROVIDER", "fake")
    monkeypatch.setenv("PULSEOPS_CALENDAR_PROVIDER", "fake")
    monkeypatch.setenv("PULSEOPS_LLM_PROVIDER", "fake")
    get_settings.cache_clear()
    configure_dbos_runtime(
        DbosRuntimeConfig(
            app_name="pulseops-it",
            system_database_url=database_url,
        )
    )
    DBOS.launch()
    try:
        with SetWorkflowID(f"dbos-jira-sync-it-{uuid4()}"):
            handle = await DBOS.start_workflow_async(
                dbos_jira_sync_workflow,
                JiraSyncInput(
                    tenant_id="demo",
                    project_key="PO",
                    observed_at="2026-01-10T11:00:00+00:00",
                ),
            )
        result = await handle.get_result()
    finally:
        destroy_dbos_runtime()
        get_settings.cache_clear()

    assert result.connector == "issue"
    assert result.scope == "project:PO"
    assert result.items_synced == 2
    assert result.cursor_updated_at == "2026-01-10T10:00:00+00:00"


async def test_dbos_scheduler_applies_heartbeat_schedule(compose_stack: object) -> None:
    from dbos import DBOS

    from infra.adapters.workflows.dbos import (
        DbosRuntimeConfig,
        DbosWorkflowScheduler,
        configure_dbos_runtime,
        destroy_dbos_runtime,
    )

    database_url = _service_url(compose_stack, "postgres", 5432, "pulseops")
    schedule_id = f"dbos-heartbeat-schedule-it-{uuid4()}"
    scheduler = DbosWorkflowScheduler(
        app_name="pulseops-it",
        system_database_url=database_url,
        schedule_id=schedule_id,
        tenant_id="demo",
        heartbeat_cron="0 * * * * *",
    )
    try:
        result = await scheduler.ensure_heartbeat_schedule()
        configure_dbos_runtime(
            DbosRuntimeConfig(
                app_name="pulseops-it",
                system_database_url=database_url,
            )
        )
        DBOS.launch()
        handle = DBOS.trigger_schedule(schedule_id)
        heartbeat = await asyncio.to_thread(handle.get_result)
    finally:
        destroy_dbos_runtime()

    assert result.schedule_id == schedule_id
    assert result.status == "configured"
    assert heartbeat.status == "ok"
    assert heartbeat.tenant_id == "demo"
    assert heartbeat.heartbeat_id.startswith(f"{schedule_id}-")


async def test_dbos_scheduler_applies_checkin_fanout_schedule(
    compose_stack: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dbos import DBOS

    from infra.adapters.workflows.dbos import (
        DbosRuntimeConfig,
        DbosWorkflowScheduler,
        configure_dbos_runtime,
        destroy_dbos_runtime,
    )

    database_url = _service_url(compose_stack, "postgres", 5432, "pulseops")
    monkeypatch.setenv("PULSEOPS_SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("PULSEOPS_RUNTIME_MODE", "memory")
    monkeypatch.setenv("PULSEOPS_WORKFLOW_PROVIDER", "fake")
    get_settings.cache_clear()
    schedule_id = f"dbos-checkin-fanout-schedule-it-{uuid4()}"
    scheduler = DbosWorkflowScheduler(
        app_name="pulseops-it",
        system_database_url=database_url,
        schedule_id=f"unused-heartbeat-{uuid4()}",
        tenant_id="demo",
        heartbeat_cron="0 * * * * *",
    )
    try:
        result = await scheduler.ensure_checkin_fanout_schedule(
            CheckinScheduleConfig(
                schedule_id=schedule_id,
                tenant_id="demo",
                cron="0 * * * * *",
            )
        )
        configure_dbos_runtime(
            DbosRuntimeConfig(
                app_name="pulseops-it",
                system_database_url=database_url,
            )
        )
        DBOS.launch()
        handle = DBOS.trigger_schedule(schedule_id)
        fanout = await asyncio.to_thread(handle.get_result)
    finally:
        destroy_dbos_runtime()
        get_settings.cache_clear()

    assert result.schedule_id == schedule_id
    assert result.status == "configured"
    assert fanout.tenant_id == "demo"
    assert fanout.dispatched == 0
    assert fanout.workflow_ids == []


async def test_temporal_worker_executes_heartbeat(compose_stack: object) -> None:
    from temporalio.worker import Worker

    temporal_target = _temporal_target(compose_stack)
    client = await _connect_temporal(temporal_target)
    task_queue = f"pulseops-it-{uuid4()}"
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[HeartbeatWorkflow],
        activities=[record_heartbeat_activity],
    )
    async with worker:
        result = await client.execute_workflow(
            HeartbeatWorkflow.run,
            HeartbeatInput(tenant_id="demo", heartbeat_id=f"it-{uuid4()}"),
            id=f"heartbeat-it-{uuid4()}",
            task_queue=task_queue,
        )
    assert result.status == "ok"


def _service_url(compose: object, service: str, port: int, database: str) -> str:
    host = compose.get_service_host(service, port)
    published_port = compose.get_service_port(service, port)
    return f"postgresql://pulseops:pulseops@{host}:{published_port}/{database}"


def _redis_url(compose: object) -> str:
    host = compose.get_service_host("redis", 6379)
    port = compose.get_service_port("redis", 6379)
    return f"redis://{host}:{port}/0"


def _temporal_target(compose: object) -> str:
    host = compose.get_service_host("temporal", 7233)
    port = compose.get_service_port("temporal", 7233)
    return f"{host}:{port}"


async def _connect_temporal(target: str) -> object:
    from temporalio.client import Client

    for _ in range(30):
        try:
            return await Client.connect(target)
        except Exception:
            await asyncio.sleep(1.0)
    return await Client.connect(target)
