"""DBOS never launches on a database nobody named, and no test reaches the stack's own.

A settings object that was never given a database falls back to the default URL, the
database of the stack a developer runs on this machine. A test that built such settings
and saved the check-in fill switch launched DBOS there, which registered the code under
test as the stack's application version and stalled the stack's scheduled runs.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest
from dbos import DBOS

from config.settings import DEFAULT_DATABASE_URL, Settings
from infra.adapters.catalog import (
    build_rollup_refresher,
    build_workflow_readiness_probe,
    build_workflow_scheduler,
    build_workflow_worker,
)
from infra.adapters.workflows import dbos as dbos_workflows
from infra.adapters.workflows.dbos import DbosDatabaseNotConfigured, DbosRuntimeConfig
from infra.registry import ServiceRegistry
from tests.fixtures import stack_database

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="
OWN_DATABASE_URL = "postgresql://x:x@127.0.0.1:1/x"

_BUILDERS = [
    build_workflow_scheduler,
    build_rollup_refresher,
    build_workflow_worker,
    build_workflow_readiness_probe,
]


@pytest.fixture(autouse=True)
def _no_database_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Whoever runs the suite may export a URL, as the Makefile does, or the opt-in.
    monkeypatch.delenv("OPENPROGRAM_DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENPROGRAM_DBOS_SYSTEM_DATABASE_URL", raising=False)
    monkeypatch.delenv(stack_database.OPT_IN, raising=False)


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        secret_key=SECRET_KEY,
        runtime_mode="memory",
        workflow_provider="dbos",
        **overrides,
    )


def _fake_dbos(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A DBOS that records what it is asked, so nothing can really launch."""
    calls: list[str] = []

    class FakeDbos:
        def __init__(self, config: dict[str, object]) -> None:
            calls.append("construct")

        @staticmethod
        def destroy(destroy_registry: bool = False) -> None:
            calls.append("destroy")

        @staticmethod
        def launch() -> None:
            calls.append("launch")

    monkeypatch.setattr(dbos_workflows, "DBOS", FakeDbos)
    monkeypatch.setattr(dbos_workflows, "_configured_runtime", None)
    return calls


# --- the settings: was a database named? -----------------------------------------------


def test_settings_nobody_gave_a_database_fall_back_to_the_default_and_are_not_explicit() -> None:
    settings = _settings()

    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.dbos_database_explicit is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": OWN_DATABASE_URL},
        {"database_url": DEFAULT_DATABASE_URL},  # spelled out: a person named it
        {"dbos_system_database_url": OWN_DATABASE_URL},
    ],
)
def test_a_database_named_in_the_constructor_is_explicit(overrides: dict[str, str]) -> None:
    assert _settings(**overrides).dbos_database_explicit is True


def test_a_database_named_in_the_environment_is_explicit_even_when_it_spells_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What the Makefile exports for every target.
    monkeypatch.setenv("OPENPROGRAM_DATABASE_URL", DEFAULT_DATABASE_URL)

    assert _settings().dbos_database_explicit is True


def test_a_database_named_in_a_dotenv_file_is_explicit(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"OPENPROGRAM_DATABASE_URL={OWN_DATABASE_URL}\n")

    settings = Settings(_env_file=dotenv, secret_key=SECRET_KEY, runtime_mode="memory")

    assert settings.dbos_database_explicit is True


def test_an_empty_dbos_url_is_no_name_either(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example ships the variable empty.
    monkeypatch.setenv("OPENPROGRAM_DBOS_SYSTEM_DATABASE_URL", "")

    assert _settings().dbos_database_explicit is False


def test_a_deployed_environment_is_not_second_guessed() -> None:
    deployed = _settings().model_copy(update={"environment": "production"})

    assert deployed.dbos_database_explicit is True


# --- the adapters: no launch on a database nobody named --------------------------------


@pytest.mark.parametrize("build", _BUILDERS)
def test_an_adapter_from_settings_nobody_gave_a_database_refuses_to_configure_dbos(
    build: Callable[[Settings], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_dbos(monkeypatch)
    adapter = build(_settings())

    config = adapter.runtime_config()
    assert config.database_explicit is False
    with pytest.raises(DbosDatabaseNotConfigured, match="OPENPROGRAM_DATABASE_URL"):
        dbos_workflows.configure_dbos_runtime(config)

    assert calls == []


@pytest.mark.parametrize("build", _BUILDERS)
def test_an_adapter_from_settings_with_a_database_configures_dbos_as_before(
    build: Callable[[Settings], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_dbos(monkeypatch)
    adapter = build(_settings(database_url=OWN_DATABASE_URL))

    config = adapter.runtime_config()
    assert config.database_explicit is True
    dbos_workflows.configure_dbos_runtime(config)

    assert calls == ["destroy", "construct"]


def test_a_config_written_by_hand_is_explicit_by_construction() -> None:
    config = DbosRuntimeConfig(app_name="openprogram-it", system_database_url=OWN_DATABASE_URL)

    assert config.database_explicit is True


async def test_a_scheduler_on_a_database_nobody_named_launches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_dbos(monkeypatch)
    scheduler = build_workflow_scheduler(_settings())

    with pytest.raises(DbosDatabaseNotConfigured):
        await scheduler.remove_schedule("openprogram-checkin-fill")
    with pytest.raises(DbosDatabaseNotConfigured):
        await scheduler.ensure_heartbeat_schedule()

    assert calls == []


async def test_a_worker_on_a_database_nobody_named_launches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_dbos(monkeypatch)

    with pytest.raises(DbosDatabaseNotConfigured):
        await build_workflow_worker(_settings()).run()

    assert calls == []


async def test_the_readiness_probe_refuses_before_it_opens_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_dbos(monkeypatch)
    connections: list[object] = []

    async def connect(*args: object, **kwargs: object) -> object:
        connections.append(args)
        raise AssertionError("the probe connected")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)

    with pytest.raises(DbosDatabaseNotConfigured):
        await build_workflow_readiness_probe(_settings()).check()

    assert connections == []
    assert calls == []


async def test_saving_the_fill_switch_on_default_settings_launches_no_dbos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The path that stalled the stack: the switch's save removes the fill's schedule."""
    calls = _fake_dbos(monkeypatch)
    registry = ServiceRegistry(_settings())
    try:
        saved = await registry.fill_day_settings_service().set("demo", False, actor="admin")
    finally:
        await registry.shutdown()

    assert saved.enabled is False
    assert calls == []


# --- the test suite: no test reaches the stack's own database --------------------------


@pytest.mark.parametrize(
    ("conninfo", "reaches"),
    [
        (DEFAULT_DATABASE_URL, True),
        ("postgresql://u:p@127.0.0.1:5432/openprogram", True),
        ("postgresql://u:p@localhost/openprogram", True),  # the port defaults to 5432
        ("postgresql://u:p@[::1]:5432/openprogram", True),
        ("host=/var/run/postgresql dbname=openprogram", True),
        ("postgresql://u:p@localhost:5432/openprogram_copy", False),  # a copy of it
        ("postgresql://u:p@localhost:49153/openprogram", False),  # a throwaway container
        ("postgresql://u:p@postgres:5432/openprogram", False),  # inside the compose network
        (OWN_DATABASE_URL, False),
        ("not a connection string", False),
    ],
)
def test_the_stack_database_is_told_from_a_tests_own(conninfo: str, reaches: bool) -> None:
    assert stack_database.reaches_stack_database(conninfo) is reaches


def test_connection_options_count_as_much_as_the_string() -> None:
    assert stack_database.reaches_stack_database("", host="localhost", dbname="openprogram")
    assert not stack_database.reaches_stack_database("", host="localhost", dbname="pooltest")


def test_a_refusal_is_not_an_exception_so_code_that_carries_on_cannot_swallow_it() -> None:
    assert not issubclass(pytest.fail.Exception, Exception)
    with pytest.raises(pytest.fail.Exception, match="database of its own"):
        stack_database.refuse_stack_database("connect to", DEFAULT_DATABASE_URL)


def test_the_opt_in_lets_a_test_run_against_the_stack_on_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(stack_database.OPT_IN, "1")

    stack_database.refuse_stack_database("connect to", DEFAULT_DATABASE_URL)


def test_launching_dbos_on_the_stack_database_is_refused_before_the_real_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[str] = []
    guarded = stack_database.guard_dbos_launch(lambda: launched.append("launch"))

    monkeypatch.setattr(
        dbos_workflows,
        "_configured_runtime",
        DbosRuntimeConfig(app_name="a", system_database_url=DEFAULT_DATABASE_URL),
    )
    with pytest.raises(pytest.fail.Exception):
        guarded()
    assert launched == []

    monkeypatch.setattr(
        dbos_workflows,
        "_configured_runtime",
        DbosRuntimeConfig(app_name="a", system_database_url=OWN_DATABASE_URL),
    )
    guarded()
    assert launched == ["launch"]


def test_a_connection_to_the_stack_database_is_refused_before_it_is_opened() -> None:
    opened: list[str] = []

    def connect(conninfo: str = "", **kwargs: object) -> str:
        opened.append(conninfo)
        return "connection"

    guarded = stack_database.guard_connect(connect)

    with pytest.raises(pytest.fail.Exception):
        guarded(DEFAULT_DATABASE_URL)
    with pytest.raises(pytest.fail.Exception):
        guarded("", host="localhost", dbname="openprogram", autocommit=True)
    assert opened == []
    assert guarded(OWN_DATABASE_URL, autocommit=True) == "connection"
    assert opened == [OWN_DATABASE_URL]


async def test_an_async_connection_to_the_stack_database_is_refused_before_it_is_opened() -> None:
    opened: list[str] = []

    async def connect(conninfo: str = "", **kwargs: object) -> str:
        opened.append(conninfo)
        return "connection"

    guarded = stack_database.guard_async_connect(connect)

    with pytest.raises(pytest.fail.Exception):
        await guarded(DEFAULT_DATABASE_URL, connect_timeout=5)
    assert opened == []
    assert await guarded(OWN_DATABASE_URL) == "connection"


def test_every_test_runs_with_the_guards_installed() -> None:
    for guarded in (
        DBOS.launch,
        psycopg.connect,
        psycopg.Connection.connect,
        psycopg.AsyncConnection.connect,
    ):
        assert getattr(guarded, "stack_database_guard", False)
