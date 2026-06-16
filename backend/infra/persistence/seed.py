from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol

from psycopg import AsyncConnection, OperationalError
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from config.settings import get_settings
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_graph import PostgresGraphRepository
from infra.persistence.seed_data import seed_demo_graph


async def main() -> None:
    settings = get_settings()
    if settings.database_url.startswith("postgresql"):
        try:
            node_count, edge_count = await _seed_postgres(settings.database_url, settings.tenant_id)
            print(f"seeded {node_count} graph nodes and {edge_count} graph edges in postgres")
            return
        except OperationalError as exc:
            print(f"postgres unavailable ({exc}); seeded in-memory smoke graph instead")

    store = InMemoryGraphStore()
    await seed_demo_graph(store, store, settings.tenant_id)
    tree = await store.get_program_tree(settings.tenant_id, "program-platform", date.today())
    print(f"seeded {len(tree.nodes)} graph nodes and {len(tree.edges)} graph edges")


class AsyncCursorLike(Protocol):
    async def fetchall(self) -> Sequence[Mapping[str, object]]: ...


class AsyncConnectionLike(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> AsyncCursorLike: ...

    async def commit(self) -> None: ...

    async def close(self) -> None: ...


class PsycopgExecutor:
    def __init__(self, connection: AsyncConnectionLike) -> None:
        self._connection = connection

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        return await self._connection.execute(query, _adapt_params(params))

    async def fetch(self, query: str, params: Sequence[object] = ()) -> list[dict[str, object]]:
        cursor = await self._connection.execute(query, _adapt_params(params))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def _seed_postgres(database_url: str, tenant_id: str) -> tuple[int, int]:
    connection = await AsyncConnection.connect(database_url, row_factory=dict_row)
    try:
        repository = PostgresGraphRepository(PsycopgExecutor(connection))
        await seed_demo_graph(repository, repository, tenant_id)
        await connection.commit()
        tree = await repository.get_program_tree(tenant_id, "program-platform", date.today())
        return len(tree.nodes), len(tree.edges)
    finally:
        await connection.close()


def _adapt_params(params: Sequence[object]) -> tuple[object, ...]:
    adapted: list[object] = []
    for param in params:
        if isinstance(param, dict):
            adapted.append(Jsonb(param))
        else:
            adapted.append(param)
    return tuple(adapted)


if __name__ == "__main__":
    asyncio.run(main())
