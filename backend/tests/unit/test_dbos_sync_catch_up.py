"""After a sleep or downtime, Jira and Git syncs catch up one run at a time, not all at once."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from config.settings import Settings
from core.domain.workflows import SyncDispatchInput
from infra.adapters.catalog import (
    build_rollup_refresher,
    build_workflow_readiness_probe,
    build_workflow_scheduler,
    build_workflow_worker,
)
from infra.adapters.workflows import dbos as dbos_workflows
from infra.workflows.runtime_sync import RuntimeSyncWorkflowResult
from infra.workflows.schedule import sync_schedule_configs

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="

_DBOS_ADAPTERS = (
    dbos_workflows.DbosWorkflowScheduler,
    dbos_workflows.DbosRollupRefresher,
    dbos_workflows.DbosWorkflowWorker,
    dbos_workflows.DbosWorkflowReadinessProbe,
)


def _at(hhmm: str) -> datetime:
    hour, minute = hhmm.split(":")
    return datetime(2026, 10, 10, int(hour), int(minute), tzinfo=UTC)


@pytest.mark.parametrize(
    ("tick", "now", "superseded"),
    [
        # The 9 h sleep on 2026-10-10: woken at 03:34, every quarter hour since
        # 18:15 the evening before fired at once. Only 03:30 is still current.
        ("03:30", "03:34", False),
        ("03:15", "03:34", True),
        ("18:15", "03:34", True),
        # On time, a tick is current until its successor falls due.
        ("03:30", "03:30", False),
        ("03:30", "03:44", False),
        ("03:30", "03:45", True),
    ],
)
def test_a_quarter_hourly_tick_is_superseded_once_the_next_one_is_due(
    tick: str, now: str, superseded: bool
) -> None:
    scheduled_at = _at(tick)
    if tick == "18:15":
        scheduled_at = scheduled_at.replace(day=9)

    assert dbos_workflows.sync_tick_superseded(scheduled_at, "*/15 * * * *", now=_at(now)) is (
        superseded
    )


def test_a_seconds_cron_is_read_the_way_dbos_fires_it() -> None:
    # Six fields put the seconds first, as DBOS's scheduler reads them.
    tick = datetime(2026, 10, 10, 3, 0, 0, tzinfo=UTC)

    assert not dbos_workflows.sync_tick_superseded(
        tick, "*/2 * * * * *", now=tick.replace(second=1)
    )
    assert dbos_workflows.sync_tick_superseded(tick, "*/2 * * * * *", now=tick.replace(second=2))


def _runtime_context(connector: str) -> dict[str, Any]:
    settings = Settings.model_validate({"secret_key": SECRET_KEY, "tenant_id": "demo"})
    config = next(
        config
        for config in sync_schedule_configs(settings)
        if config.payload.get("connector") == connector
    )
    return {
        "schedule_id": config.schedule_id,
        "tenant_id": config.tenant_id,
        "connector": config.connector,
        "scope": config.scope,
        "payload": dict(config.payload),
        "cron": config.cron,
    }


@pytest.mark.parametrize("connector", ["issue", "vcs"])
async def test_a_superseded_scheduled_sync_dispatches_nothing(
    monkeypatch: pytest.MonkeyPatch, connector: str
) -> None:
    checked: list[tuple[str, str]] = []
    dispatched: list[SyncDispatchInput] = []

    async def superseded(scheduled_at: str, cron: str) -> bool:
        checked.append((scheduled_at, cron))
        return True

    async def run(input: SyncDispatchInput) -> object:
        dispatched.append(input)
        return None

    monkeypatch.setattr(dbos_workflows, "dbos_check_sync_tick_superseded_step", superseded)
    monkeypatch.setattr(dbos_workflows, "_run_sync_dispatch", run)
    context = _runtime_context(connector)

    result = await dbos_workflows._run_scheduled_sync(_at("03:15"), context)

    assert result == RuntimeSyncWorkflowResult(
        tenant_id="demo", connector=connector, dispatched=0, workflow_ids=[]
    )
    assert checked == [(_at("03:15").isoformat(), context["cron"])]
    assert dispatched == []


async def test_the_current_scheduled_sync_runs_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched: list[SyncDispatchInput] = []

    async def current(scheduled_at: str, cron: str) -> bool:
        return False

    async def run(input: SyncDispatchInput) -> str:
        dispatched.append(input)
        return "ran"

    monkeypatch.setattr(dbos_workflows, "dbos_check_sync_tick_superseded_step", current)
    monkeypatch.setattr(dbos_workflows, "_run_sync_dispatch", run)

    result = await dbos_workflows._run_scheduled_sync(_at("03:30"), _runtime_context("vcs"))

    assert result == "ran"
    assert [dispatch.payload["connector"] for dispatch in dispatched] == ["vcs"]
    assert dispatched[0].payload["observed_at"] == _at("03:30").isoformat()


async def test_derived_schedules_are_never_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the Jira and Git syncs read from a cursor; a risk pass, rollup or
    # report keeps every tick it had.
    async def fail(scheduled_at: str, cron: str) -> bool:
        raise AssertionError("a derived schedule must not be checked")

    async def run(input: SyncDispatchInput) -> str:
        return input.connector

    monkeypatch.setattr(dbos_workflows, "dbos_check_sync_tick_superseded_step", fail)
    monkeypatch.setattr(dbos_workflows, "_run_sync_dispatch", run)
    context = {
        "schedule_id": "openprogram-runtime-risk-assessment",
        "tenant_id": "demo",
        "connector": "risk",
        "scope": "assessment",
        "payload": {},
        "cron": "*/30 * * * *",
    }

    assert await dbos_workflows._run_scheduled_sync(_at("18:00"), context) == "risk"


class _Handle:
    pass


@pytest.mark.parametrize(
    ("connector", "payload", "workflow"),
    [
        ("issue", {"project_key": "CHK"}, "dbos_jira_sync_workflow"),
        ("vcs", {"repo_name": "acme/platform-libs"}, "dbos_git_sync_workflow"),
    ],
)
async def test_jira_and_git_syncs_wait_their_turn_on_the_sync_queue(
    monkeypatch: pytest.MonkeyPatch, connector: str, payload: dict[str, Any], workflow: str
) -> None:
    enqueued: list[object] = []
    started: list[object] = []

    class Queue:
        async def enqueue_async(self, function: object, input: object) -> _Handle:
            enqueued.append(function)
            return _Handle()

    async def queue() -> Queue:
        return Queue()

    async def start(function: object, input: object) -> _Handle:
        started.append(function)
        return _Handle()

    monkeypatch.setattr(dbos_workflows, "_registered_sync_queue", queue)
    monkeypatch.setattr(dbos_workflows.DBOS, "start_workflow_async", staticmethod(start))

    workflow_id = await dbos_workflows._start_sync_child_workflow(
        SyncDispatchInput(tenant_id="demo", connector=connector, scope="s", payload=payload),
        workflow_id="child-1",
    )

    assert workflow_id == "child-1"
    assert enqueued == [getattr(dbos_workflows, workflow)]
    assert started == []


async def test_other_sync_kinds_still_start_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[object] = []

    async def queue() -> object:
        raise AssertionError("only Jira and Git syncs use the sync queue")

    async def start(function: object, input: object) -> _Handle:
        started.append(function)
        return _Handle()

    monkeypatch.setattr(dbos_workflows, "_registered_sync_queue", queue)
    monkeypatch.setattr(dbos_workflows.DBOS, "start_workflow_async", staticmethod(start))

    for connector in ("runtime", "directory", "risk", "day_report"):
        await dbos_workflows._start_sync_child_workflow(
            SyncDispatchInput(tenant_id="demo", connector=connector, scope="s", payload={}),
            workflow_id=f"child-{connector}",
        )

    assert started == [
        dbos_workflows.dbos_runtime_config_sync_workflow,
        dbos_workflows.dbos_directory_sync_workflow,
        dbos_workflows.dbos_risk_assessment_workflow,
        dbos_workflows.dbos_day_report_dispatch_workflow,
    ]


def _settings_must_not_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Launching a runtime from its config must work where full settings do not validate.

    An integration test, or a one-off script, holds an application name and a
    database URL and no secret key: reading the global settings there raised
    "secret_key must be a 44-character Fernet key".
    """

    def unavailable() -> Settings:
        raise AssertionError("the DBOS runtime read the global settings")

    monkeypatch.setattr("config.settings.get_settings", unavailable)


async def test_the_sync_queue_is_registered_once_per_runtime_with_the_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: list[tuple[str, dict[str, object]]] = []
    runtime = dbos_workflows.DbosRuntimeConfig(
        app_name="openprogram",
        system_database_url="postgresql://unused",
        sync_queue_concurrency=3,
    )

    async def register(name: str, **options: object) -> str:
        registered.append((name, options))
        return f"queue-{len(registered)}"

    monkeypatch.setattr(dbos_workflows.DBOS, "register_queue_async", staticmethod(register))
    monkeypatch.setattr(dbos_workflows, "_configured_runtime", runtime)
    monkeypatch.setattr(dbos_workflows, "_sync_queue", None)
    _settings_must_not_be_read(monkeypatch)

    first = await dbos_workflows._registered_sync_queue()
    again = await dbos_workflows._registered_sync_queue()

    assert first == again == "queue-1"
    assert registered == [
        (
            "openprogram_sync",
            {"global_concurrency": 3, "on_conflict": "always_update"},
        )
    ]


async def test_a_changed_sync_limit_registers_the_queue_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits: list[object] = []

    async def register(name: str, **options: object) -> str:
        limits.append(options["global_concurrency"])
        return f"queue-{len(limits)}"

    monkeypatch.setattr(dbos_workflows.DBOS, "register_queue_async", staticmethod(register))
    monkeypatch.setattr(dbos_workflows, "_sync_queue", None)
    _settings_must_not_be_read(monkeypatch)

    for limit in (3, 3, 5):
        monkeypatch.setattr(
            dbos_workflows,
            "_configured_runtime",
            dbos_workflows.DbosRuntimeConfig(
                app_name="openprogram",
                system_database_url="postgresql://unused",
                sync_queue_concurrency=limit,
            ),
        )
        await dbos_workflows._registered_sync_queue()

    assert limits == [3, 5]


def _fake_dbos(configs: list[dict[str, object]]) -> type:
    class FakeDbos:
        def __init__(self, config: dict[str, object]) -> None:
            configs.append(config)

        @staticmethod
        def destroy(destroy_registry: bool = False) -> None:
            return None

    return FakeDbos


def test_the_dbos_system_pool_is_bounded_by_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    configs: list[dict[str, object]] = []
    monkeypatch.setattr(dbos_workflows, "DBOS", _fake_dbos(configs))
    monkeypatch.setattr(dbos_workflows, "_configured_runtime", None)
    _settings_must_not_be_read(monkeypatch)

    dbos_workflows.configure_dbos_runtime(
        dbos_workflows.DbosRuntimeConfig(
            app_name="openprogram",
            system_database_url="postgresql://unused",
            system_pool_size=7,
        )
    )

    assert configs == [
        {
            "name": "openprogram",
            "system_database_url": "postgresql://unused",
            "sys_db_pool_size": 7,
        }
    ]


def test_a_runtime_built_from_a_name_and_a_url_alone_needs_no_settings(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    configs: list[dict[str, object]] = []
    monkeypatch.setattr(dbos_workflows, "DBOS", _fake_dbos(configs))
    monkeypatch.setattr(dbos_workflows, "_configured_runtime", None)
    _settings_must_not_be_read(monkeypatch)

    dbos_workflows.configure_dbos_runtime(
        dbos_workflows.DbosRuntimeConfig(
            app_name="openprogram-it", system_database_url="postgresql://unused"
        )
    )

    assert configs[0]["sys_db_pool_size"] == settings.dbos_system_pool_size


def test_the_runtime_config_defaults_are_the_settings_defaults(settings: Settings) -> None:
    config = dbos_workflows.DbosRuntimeConfig(app_name="a", system_database_url="u")

    assert config.system_pool_size == settings.dbos_system_pool_size
    assert config.sync_queue_concurrency == settings.sync_queue_concurrency


def test_every_dbos_adapter_carries_the_settings_values_into_its_runtime_config() -> None:
    settings = Settings(
        _env_file=None,
        secret_key=SECRET_KEY,
        runtime_mode="memory",
        workflow_provider="dbos",
        dbos_system_pool_size=6,
        sync_queue_concurrency=2,
    )

    adapters = [
        build_workflow_scheduler(settings),
        build_rollup_refresher(settings),
        build_workflow_worker(settings),
        build_workflow_readiness_probe(settings),
    ]

    expected = dbos_workflows.DbosRuntimeConfig(
        app_name=settings.dbos_app_name,
        system_database_url=settings.resolved_dbos_system_database_url,
        system_pool_size=6,
        sync_queue_concurrency=2,
    )
    assert len(adapters) == 4
    for adapter in adapters:
        assert isinstance(adapter, _DBOS_ADAPTERS)
        assert adapter.runtime_config() == expected
