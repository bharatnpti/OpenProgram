from __future__ import annotations

from datetime import UTC, date, datetime

from core.application.graph_queries import GraphQueryService
from core.domain.graph import EntityRef, FactEvent, NodeKind, Program
from infra.persistence.in_memory_graph import InMemoryGraphStore


async def test_graph_query_service_delegates_tree_and_fact_recording() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Program(tenant_id="demo", id="program-1", name="Program"))
    service = GraphQueryService(graph_repository=store, time_series_repository=store)
    fact = FactEvent(
        tenant_id="demo",
        source="test",
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.PROGRAM, id="program-1"),
        payload={"status": "green"},
        observed_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
        correlation_id="fact-1",
    )

    tree = await service.program_tree("demo", "program-1", date(2026, 1, 10))
    await service.record_fact(fact)

    assert tree.root.id == "program-1"
    assert await store.list_facts("demo", fact.entity_ref) == [fact]
