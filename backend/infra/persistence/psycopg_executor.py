from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
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
    """Postgres queries through one lazily opened connection pool.

    The pool opens on first use. While the database cannot be reached each use
    fails fast with the driver's error and the next one tries again, so the
    executor recovers once the database is back without a restart.
    """

    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
        connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._database_url = database_url
        self._min_size = min_size
        self._max_size = max_size
        self._connect_timeout_seconds = connect_timeout_seconds
        self._pool = self._new_pool()
        self._open_lock = asyncio.Lock()
        self._opened = False

    def _new_pool(self) -> AsyncConnectionPool:
        return AsyncConnectionPool(
            conninfo=self._database_url,
            min_size=self._min_size,
            max_size=self._max_size,
            kwargs={"row_factory": dict_row},
            open=False,
        )

    async def open(self) -> None:
        if self._opened:
            return
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
        with _tracer.start_as_current_span("postgres.pool.close"):
            await self._pool.close()
        self._opened = False

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
