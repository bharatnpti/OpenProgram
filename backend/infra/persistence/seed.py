from __future__ import annotations

import asyncio
from datetime import date

from config.settings import get_settings
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_graph import PostgresGraphRepository, PostgresTimeSeriesRepository
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor
from infra.persistence.seed_data import seed_demo_graph


async def main() -> None:
    settings = get_settings()
    if settings.runtime_mode == "container":
        node_count, edge_count = await _seed_postgres(settings.database_url, settings.tenant_id)
        print(f"seeded {node_count} graph nodes and {edge_count} graph edges in postgres")
        return

    store = InMemoryGraphStore()
    await seed_demo_graph(store, store, settings.tenant_id)
    tree = await store.get_program_tree(settings.tenant_id, "program-platform", date.today())
    print(f"seeded {len(tree.nodes)} graph nodes and {len(tree.edges)} graph edges")


async def _seed_postgres(database_url: str, tenant_id: str) -> tuple[int, int]:
    executor = PsycopgAsyncExecutor(database_url)
    graph_repository = PostgresGraphRepository(executor)
    time_series_repository = PostgresTimeSeriesRepository(executor)
    try:
        await seed_demo_graph(graph_repository, time_series_repository, tenant_id)
        tree = await graph_repository.get_program_tree(tenant_id, "program-platform", date.today())
        return len(tree.nodes), len(tree.edges)
    finally:
        await executor.close()


if __name__ == "__main__":
    asyncio.run(main())
