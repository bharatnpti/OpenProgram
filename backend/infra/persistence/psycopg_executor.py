from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from opentelemetry import trace
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

_tracer = trace.get_tracer("openprogram.persistence.postgres")


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
    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
    ) -> None:
        self._database_url = database_url
        self._pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        self._open_lock = asyncio.Lock()
        self._opened = False

    async def open(self) -> None:
        if self._opened:
            return
        async with self._open_lock:
            if self._opened:
                return
            with _tracer.start_as_current_span("postgres.pool.open"):
                await self._pool.open(wait=True)
            self._opened = True

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
