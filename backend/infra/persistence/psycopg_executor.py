from __future__ import annotations

from collections.abc import Mapping, Sequence

from opentelemetry import trace
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_tracer = trace.get_tracer("pulseops.persistence.postgres")


class PsycopgAsyncExecutor:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        with _tracer.start_as_current_span("postgres.execute"):
            connection = await AsyncConnection.connect(self._database_url, row_factory=dict_row)
            try:
                result = await connection.execute(query, _adapt_params(params))
                await connection.commit()
                return result
            finally:
                await connection.close()

    async def fetch(self, query: str, params: Sequence[object] = ()) -> list[dict[str, object]]:
        with _tracer.start_as_current_span("postgres.fetch"):
            connection = await AsyncConnection.connect(self._database_url, row_factory=dict_row)
            try:
                cursor = await connection.execute(query, _adapt_params(params))
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
            finally:
                await connection.close()


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
