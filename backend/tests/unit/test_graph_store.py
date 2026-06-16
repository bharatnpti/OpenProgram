from __future__ import annotations

from datetime import UTC, date, datetime

from core.domain.graph import Developer, EdgeKind, EntityRef, FactEvent, GraphEdge, NodeKind
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.seed_data import seed_demo_graph


async def test_seeded_graph_is_queryable_with_time_bounded_edges() -> None:
    store = InMemoryGraphStore()
    await seed_demo_graph(store, store, "demo")
    tree = await store.get_program_tree("demo", "program-platform", date(2026, 6, 15))
    assert tree.root.id == "program-platform"
    assert {node.id for node in tree.nodes} >= {"project-foundations", "pod-runtime", "dev-asha"}
    assert any(edge.kind is EdgeKind.CONTAINS for edge in tree.edges)

    await store.upsert_node(Developer(tenant_id="demo", id="dev-historical", name="Historical Dev"))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-runtime",
            to_node_id="dev-historical",
            kind=EdgeKind.CONTAINS,
            valid_from=date(2025, 1, 1),
            valid_to=date(2025, 12, 31),
        )
    )
    old_memberships = await store.active_developer_memberships(
        "demo", "dev-historical", date(2025, 6, 1)
    )
    current_memberships = await store.active_developer_memberships(
        "demo", "dev-historical", date(2026, 6, 1)
    )
    assert len(old_memberships) == 1
    assert current_memberships == []


async def test_fact_log_is_append_only_from_port_perspective() -> None:
    store = InMemoryGraphStore()
    fact = FactEvent(
        tenant_id="demo",
        source="unit",
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-api"),
        payload={"status": "green"},
        observed_at=datetime.now(tz=UTC),
        correlation_id="corr-1",
    )
    await store.append_fact(fact)
    await store.append_fact(fact)
    facts = await store.list_facts("demo", fact.entity_ref)
    assert facts == [fact, fact]


async def test_vector_search_returns_best_match() -> None:
    store = InMemoryGraphStore()
    ref = EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-api")
    await store.upsert_embedding("demo", ref, [1.0, 0.0])
    matches = await store.search("demo", [1.0, 0.0], limit=1)
    assert matches[0].entity_ref == ref
    assert matches[0].score == 1.0
