from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

_tracer = trace.get_tracer("openprogram.persistence.postgres")

# How long the reachability check waits for a server that never answers. A
# refused connection or an unknown host fails at once; a connect_timeout in
# the database URL wins over this.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class PoolConfig:
    """How big one process's application pool may grow, and how long a caller waits.

    ``max_size`` is a hard cap: under load a caller waits up to
    ``timeout_seconds`` for a connection to come back and then gets
    ``psycopg_pool.PoolTimeout``; the pool never opens one beyond the cap.
    """

    min_size: int = 1
    max_size: int = 10
    timeout_seconds: float = 30.0


class PsycopgSession:
    def __init__(self, connection: AsyncConnection[Any]) -> None:
        self._connection = connection

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        with _tracer.start_as_current_span("postgres.session.execute"):
            return await self._connection.execute(query, _adapt_params(params))

    async def fetch(self, query: str, params: Sequence[object] = ()) -> list[dict[str, object]]:
        with _tracer.start_as_current_span("postgres.session.fetch"):
            cursor = await self._connection.execute(query, _adapt_params(params))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


class PsycopgAsyncExecutor:
    """Postgres queries through one lazily opened, bounded connection pool.

    The pool opens on first use. While the database cannot be reached each use
    fails fast with the driver's error and the next one tries again, so the
    executor recovers once the database is back without a restart. A closed
    executor opens a fresh pool on its next use.

    The pool belongs to the event loop that opened it. When that loop has
    closed (a test's loop, say) the next use opens a fresh pool on the current
    one; a use from a second loop that is still running is refused, because a
    psycopg pool cannot serve two loops.
    """

    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._database_url = database_url
        self._min_size = min_size
        self._max_size = max_size
        self._timeout_seconds = timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._pool = self._new_pool()
        self._open_lock = asyncio.Lock()
        self._opened = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def pool_config(self) -> PoolConfig:
        return PoolConfig(
            min_size=self._min_size,
            max_size=self._max_size,
            timeout_seconds=self._timeout_seconds,
        )

    def _new_pool(self) -> AsyncConnectionPool:
        return AsyncConnectionPool(
            conninfo=self._database_url,
            min_size=self._min_size,
            max_size=self._max_size,
            timeout=self._timeout_seconds,
            kwargs={"row_factory": dict_row},
            open=False,
        )

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        if self._opened and self._loop is loop:
            return
        if self._loop is not None and self._loop is not loop:
            self._rebind(loop)
        async with self._open_lock:
            if self._opened:
                return
            with _tracer.start_as_current_span("postgres.pool.open"):
                await self._check_reachable()
                pool = self._pool
                try:
                    await pool.open(wait=True)
                except BaseException:
                    # psycopg never reopens a pool whose open timed out, and an
                    # open cancelled by a caller's deadline leaves it unable to
                    # wait again: either way the next open starts a fresh pool.
                    self._pool = self._new_pool()
                    await pool.close()
                    raise
            self._opened = True
            self._loop = loop

    def _rebind(self, loop: asyncio.AbstractEventLoop) -> None:
        previous = self._loop
        if previous is not None and not previous.is_closed() and previous.is_running():
            raise RuntimeError(
                "the Postgres pool is in use on another running event loop; "
                "one process serves its pool from one loop"
            )
        # The loop that owned the pool has gone, and its connections and pool
        # workers with it: start over on this loop.
        self._pool = self._new_pool()
        self._open_lock = asyncio.Lock()
        self._opened = False
        self._loop = loop

    async def _check_reachable(self) -> None:
        """Connect once outside the pool, so an unreachable database fails now.

        Opening the pool waits up to 30 s for its first connection while it
        retries in the background, which made every query against a database
        that is down hang that long before failing.
        """
        conninfo = self._database_url
        if "connect_timeout" not in conninfo_to_dict(conninfo):
            conninfo = make_conninfo(conninfo, connect_timeout=self._connect_timeout_seconds)
        connection = await AsyncConnection.connect(conninfo)
        await connection.close()

    async def close(self) -> None:
        if not self._opened:
            return
        pool = self._pool
        # A closed psycopg pool cannot open again, so the next use gets a new one.
        self._pool = self._new_pool()
        self._opened = False
        self._loop = None
        with _tracer.start_as_current_span("postgres.pool.close"):
            await pool.close()

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        with _tracer.start_as_current_span("postgres.execute"):
            async with self.transaction() as session:
                return await session.execute(query, params)

    async def fetch(self, query: str, params: Sequence[object] = ()) -> list[dict[str, object]]:
        with _tracer.start_as_current_span("postgres.fetch"):
            await self.open()
            async with self._pool.connection() as connection:
                cursor = await connection.execute(query, _adapt_params(params))
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[PsycopgSession]:
        await self.open()
        async with self._pool.connection() as connection:
            async with connection.transaction():
                yield PsycopgSession(connection)


# One executor, and so one pool, per database and pool configuration in this
# process. Every ServiceRegistry borrows it: the API's, the worker's, and the
# short-lived one each workflow step builds.
_shared_executors: dict[tuple[str, PoolConfig], PsycopgAsyncExecutor] = {}


def shared_executor(database_url: str, config: PoolConfig) -> PsycopgAsyncExecutor:
    """The process's executor for ``database_url``, created on first ask.

    Callers never close it; the process does, once, through
    ``close_shared_executors`` as it shuts down.
    """
    key = (database_url, config)
    executor = _shared_executors.get(key)
    if executor is None:
        executor = PsycopgAsyncExecutor(
            database_url,
            min_size=config.min_size,
            max_size=config.max_size,
            timeout_seconds=config.timeout_seconds,
        )
        _shared_executors[key] = executor
    return executor


async def close_shared_executors() -> None:
    """Close every shared pool. A later use opens a fresh one."""
    for executor in list(_shared_executors.values()):
        await executor.close()


def _adapt_params(params: Sequence[object]) -> tuple[object, ...]:
    adapted: list[object] = []
    for param in params:
        if isinstance(param, dict):
            adapted.append(Jsonb(param))
        elif isinstance(param, Mapping):
            adapted.append(Jsonb(dict(param)))
        else:
            adapted.append(param)
    return tuple(adapted)
