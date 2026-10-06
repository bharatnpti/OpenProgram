"""Opening the Postgres pool while the database cannot be reached: fail fast, recover later."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from psycopg import OperationalError

from infra.persistence import psycopg_executor
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor

DATABASE_URL = "postgresql://openprogram@db.invalid:5432/openprogram"


@dataclass
class _FakePool:
    hangs: bool = False
    opened: bool = False
    closed: bool = False

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

    def new_pool(**_: object) -> _FakePool:
        created.append(_FakePool())
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
