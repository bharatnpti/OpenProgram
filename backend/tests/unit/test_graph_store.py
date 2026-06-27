from __future__ import annotations

from datetime import UTC, date, datetime

from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.fixtures.demo_graph import populate_demo_graph


async def test_demo_graph_is_queryable_with_time_bounded_edges() -> None:
    store = InMemoryGraphStore()
    await populate_demo_graph(store, store, "demo")
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


async def test_fact_log_inserts_once_by_identity() -> None:
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
    assert facts == [fact]


async def test_graph_store_lists_gets_and_deletes_nodes_and_edges() -> None:
    store = InMemoryGraphStore()
    program = Program(tenant_id="demo", id="program-1", name="Program")
    project = Project(tenant_id="demo", id="project-1", name="Project")
    pod = Pod(tenant_id="demo", id="pod-1", name="Pod")
    member = Developer(tenant_id="demo", id="dev-1", name="Asha")
    task = Task(tenant_id="demo", id="task-1", name="Task")
    for node in (program, project, pod, member, task):
        await store.upsert_node(node)
    program_edge = GraphEdge(
        tenant_id="demo",
        from_node_id=program.id,
        to_node_id=project.id,
        kind=EdgeKind.CONTAINS,
    )
    pod_edge = GraphEdge(
        tenant_id="demo",
        from_node_id=project.id,
        to_node_id=pod.id,
        kind=EdgeKind.CONTAINS,
    )
    assignment = GraphEdge(
        tenant_id="demo",
        from_node_id=member.id,
        to_node_id=task.id,
        kind=EdgeKind.ASSIGNED_TO,
    )
    for edge in (program_edge, pod_edge, assignment):
        await store.add_edge(edge)

    assert await store.get_node("demo", "program-1") == program
    assert [node.id for node in await store.list_nodes("demo", NodeKind.PROJECT)] == ["project-1"]
    assert await store.list_edges("demo", from_node_id="project-1") == [pod_edge]
    assert await store.list_edges("demo", kind=EdgeKind.ASSIGNED_TO) == [assignment]

    await store.remove_edge(pod_edge)
    assert await store.list_edges("demo", from_node_id="project-1") == []

    await store.delete_node("demo", "dev-1")
    assert await store.get_node("demo", "dev-1") is None
    assert await store.list_edges("demo", kind=EdgeKind.ASSIGNED_TO) == []


async def test_fact_log_filters_by_observed_since() -> None:
    store = InMemoryGraphStore()
    ref = EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-api")
    old_fact = FactEvent(
        tenant_id="demo",
        source="unit",
        entity_ref=ref,
        payload={"status": "old"},
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        correlation_id="corr-old",
    )
    new_fact = FactEvent(
        tenant_id="demo",
        source="unit",
        entity_ref=ref,
        payload={"status": "new"},
        observed_at=datetime(2026, 1, 10, tzinfo=UTC),
        correlation_id="corr-new",
    )
    await store.append_fact(old_fact)
    await store.append_fact(new_fact)

    assert await store.list_facts("demo", ref, datetime(2026, 1, 5, tzinfo=UTC)) == [new_fact]


async def test_vector_search_returns_best_match() -> None:
    store = InMemoryGraphStore()
    ref = EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-api")
    await store.upsert_embedding("demo", ref, [1.0, 0.0])
    matches = await store.search("demo", [1.0, 0.0], limit=1)
    assert matches[0].entity_ref == ref
    assert matches[0].score == 1.0
