"""The application's Postgres pool: bounded, one per process, fail fast, recover later."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

import pytest
from psycopg import OperationalError

from config.settings import Settings
from infra.persistence import psycopg_executor
from infra.persistence.psycopg_executor import (
    PoolConfig,
    PsycopgAsyncExecutor,
    close_shared_executors,
    shared_executor,
)
from infra.registry import ServiceRegistry

DATABASE_URL = "postgresql://openprogram@db.invalid:5432/openprogram"
SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="


@dataclass
class _FakePool:
    hangs: bool = False
    opened: bool = False
    closed: bool = False
    options: dict[str, object] = field(default_factory=dict)

    async def open(self, wait: bool = False) -> None:
        if self.hangs:
            await asyncio.Event().wait()
        self.opened = True

    async def close(self) -> None:
        self.closed = True


class _Connection:
    async def close(self) -> None:
        return None


@dataclass
class _Database:
    """Stands in for the server: refuses connections until it is up."""

    up: bool = False

    def __post_init__(self) -> None:
        self.attempts: list[str] = []

    async def connect(self, conninfo: str) -> _Connection:
        self.attempts.append(conninfo)
        if not self.up:
            raise OperationalError("connection failed: Connection refused")
        return _Connection()


@pytest.fixture
def pools(monkeypatch: pytest.MonkeyPatch) -> list[_FakePool]:
    created: list[_FakePool] = []

    def new_pool(**options: object) -> _FakePool:
        created.append(_FakePool(options=options))
        return created[-1]

    monkeypatch.setattr(psycopg_executor, "AsyncConnectionPool", new_pool)
    return created


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> _Database:
    database = _Database()
    monkeypatch.setattr(psycopg_executor.AsyncConnection, "connect", database.connect)
    return database


async def test_a_database_that_refuses_fails_at_once_and_is_tried_again(
    pools: list[_FakePool], database: _Database
) -> None:
    executor = PsycopgAsyncExecutor(DATABASE_URL)

    for _ in range(2):
        with pytest.raises(OperationalError):
            await executor.fetch("SELECT 1")
    # The pool never waited for a database that refused...
    assert not pools[0].opened
    database.up = True
    await executor.open()

    # ...and the executor recovers once it is back, without a restart.
    assert len(database.attempts) == 3
    assert pools[0].opened


async def test_an_open_cut_short_by_a_deadline_starts_a_fresh_pool_next_time(
    pools: list[_FakePool], database: _Database
) -> None:
    database.up = True
    executor = PsycopgAsyncExecutor(DATABASE_URL)
    pools[0].hangs = True

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await executor.open()
    await executor.open()

    assert pools[0].closed
    assert len(pools) == 2
    assert pools[1].opened


async def test_the_reachability_check_bounds_the_connect_unless_the_url_does(
    pools: list[_FakePool], database: _Database
) -> None:
    database.up = True

    await PsycopgAsyncExecutor(DATABASE_URL).open()
    await PsycopgAsyncExecutor(f"{DATABASE_URL}?connect_timeout=11").open()

    assert "connect_timeout=5" in database.attempts[0]
    assert database.attempts[1].endswith("connect_timeout=11")


@pytest.fixture
def no_shared_executors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(psycopg_executor, "_shared_executors", {})


def _container_settings(**overrides: object) -> Settings:
    return Settings.model_validate(
        {
            "secret_key": SECRET_KEY,
            "runtime_mode": "container",
            "database_url": DATABASE_URL,
            **overrides,
        }
    )


async def test_the_pool_is_capped_and_a_caller_waits_for_a_connection(
    pools: list[_FakePool], database: _Database
) -> None:
    database.up = True

    await PsycopgAsyncExecutor(DATABASE_URL, min_size=2, max_size=7, timeout_seconds=4.5).open()

    # max_size is a hard cap in psycopg_pool; timeout is how long a caller
    # waits for a connection to come back before PoolTimeout.
    assert pools[0].options["min_size"] == 2
    assert pools[0].options["max_size"] == 7
    assert pools[0].options["timeout"] == 4.5


async def test_a_closed_executor_opens_a_fresh_pool_on_its_next_use(
    pools: list[_FakePool], database: _Database
) -> None:
    database.up = True
    executor = PsycopgAsyncExecutor(DATABASE_URL)

    await executor.open()
    await executor.close()
    await executor.open()

    assert pools[0].closed
    assert len(pools) == 2
    assert pools[1].opened


def test_a_process_has_one_executor_per_database_and_pool_config(
    no_shared_executors: None,
) -> None:
    config = PoolConfig(min_size=1, max_size=10, timeout_seconds=30.0)

    first = shared_executor(DATABASE_URL, config)

    assert shared_executor(DATABASE_URL, config) is first
    assert shared_executor(DATABASE_URL, PoolConfig(max_size=3)) is not first
    assert first.pool_config == config


async def test_every_registry_in_a_process_borrows_the_one_pool(
    pools: list[_FakePool], database: _Database, no_shared_executors: None
) -> None:
    database.up = True
    settings = _container_settings(postgres_pool_max_size=8, postgres_pool_timeout_seconds=12.0)
    api = ServiceRegistry(settings)
    # What each workflow step builds for itself, once per run.
    steps = [ServiceRegistry(settings) for _ in range(3)]

    executor = api._executor()
    await executor.open()
    for step in steps:
        assert step._executor() is executor
        await step.close()

    # A step's close leaves the pool to everyone else...
    assert len(pools) == 1
    assert pools[0].opened and not pools[0].closed
    assert pools[0].options["max_size"] == 8
    assert pools[0].options["timeout"] == 12.0
    # ...and the process's own shutdown closes it.
    await api.shutdown()
    assert pools[0].closed


async def test_close_shared_executors_closes_each_open_pool_once(
    pools: list[_FakePool], database: _Database, no_shared_executors: None
) -> None:
    database.up = True
    await shared_executor(DATABASE_URL, PoolConfig()).open()
    shared_executor(f"{DATABASE_URL}2", PoolConfig())  # never used, never opened

    await close_shared_executors()
    await close_shared_executors()

    assert pools[0].closed
    assert not any(pool.opened for pool in pools[1:])


async def test_a_pool_left_by_a_closed_event_loop_is_replaced_on_the_current_one(
    pools: list[_FakePool], database: _Database
) -> None:
    database.up = True
    executor = PsycopgAsyncExecutor(DATABASE_URL)
    thread = threading.Thread(target=lambda: asyncio.run(executor.open()))
    thread.start()
    await asyncio.to_thread(thread.join, 5)

    await executor.open()

    assert pools[0].opened
    assert len(pools) == 2
    assert pools[1].opened


async def test_a_pool_in_use_on_another_running_event_loop_is_refused(
    pools: list[_FakePool], database: _Database
) -> None:
    database.up = True
    executor = PsycopgAsyncExecutor(DATABASE_URL)
    opened = threading.Event()
    release = threading.Event()

    async def hold_the_pool() -> None:
        await executor.open()
        opened.set()
        # Keeps this loop running, and so the pool bound to it, until released.
        await asyncio.to_thread(release.wait, 5)

    thread = threading.Thread(target=lambda: asyncio.run(hold_the_pool()))
    thread.start()
    try:
        assert await asyncio.to_thread(opened.wait, 5)
        with pytest.raises(RuntimeError, match="another running event loop"):
            await executor.open()
    finally:
        release.set()
        await asyncio.to_thread(thread.join, 5)
    assert len(pools) == 1
