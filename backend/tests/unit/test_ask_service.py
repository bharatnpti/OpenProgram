from __future__ import annotations

import json

from datetime import date

from core.application.ask_service import GraphNeighborsTool, SearchGraphNodesTool
from core.domain.graph import Developer, EdgeKind, GraphEdge, Pod, Program, Task, WorkItem
from infra.persistence.in_memory_graph import InMemoryGraphStore


async def test_search_graph_nodes_tool_filters_by_query_kind_and_limit() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(
        Program(
            tenant_id="demo",
            id="program-platform",
            name="Platform Program",
            metadata={"portfolio": "platform"},
        )
    )
    await store.upsert_node(
        WorkItem(
            tenant_id="demo",
            id="wi-auth",
            name="Browser SSO BFF",
            metadata={"repo": "openprogram/auth"},
        )
    )
    await store.upsert_node(
        WorkItem(
            tenant_id="demo",
            id="wi-risk",
            name="Risk board",
            metadata={"repo": "openprogram/risk"},
        )
    )
    tool = SearchGraphNodesTool(tenant_id="demo", repository=store)

    payload = json.loads(
        await tool.run(
            {
                "query": "openprogram",
                "kinds": ["work_item"],
                "limit": 1,
            }
        )
    )

    assert payload == [
        {
            "id": "wi-auth",
            "kind": "work_item",
            "name": "Browser SSO BFF",
            "metadata": {"repo": "openprogram/auth"},
        }
    ]


async def test_graph_neighbors_tool_finds_a_developers_assignments() -> None:
    """Relationship questions need edges; node search alone cannot answer them.

    Without this tool, "which tasks is this developer on" came back as "no
    tasks found in the current graph" -- blaming correct data for a missing
    capability.
    """
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id="demo", id="U1004", name="Noah Weber"))
    await store.upsert_node(
        Task(tenant_id="demo", id="task-chk-102", name="Refund edge cases: settle scope")
    )
    await store.upsert_node(Pod(tenant_id="demo", id="pod-payments", name="Payments Pod"))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="U1004",
            to_node_id="task-chk-102",
            kind=EdgeKind.ASSIGNED_TO,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-payments",
            to_node_id="U1004",
            kind=EdgeKind.CONTAINS,
        )
    )
    tool = GraphNeighborsTool(tenant_id="demo", repository=store, as_of=date(2026, 9, 25))

    payload = json.loads(await tool.run({"node_id": "U1004"}))

    assert payload["node"] == {"id": "U1004", "kind": "developer", "name": "Noah Weber"}
    assert payload["edges"] == [
        {
            "direction": "out",
            "edge_kind": "assigned_to",
            "neighbor_id": "task-chk-102",
            "neighbor_kind": "task",
            "neighbor_name": "Refund edge cases: settle scope",
        },
        {
            "direction": "in",
            "edge_kind": "contains",
            "neighbor_id": "pod-payments",
            "neighbor_kind": "pod",
            "neighbor_name": "Payments Pod",
        },
    ]


async def test_graph_neighbors_tool_filters_by_direction_and_kind() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id="demo", id="U1004", name="Noah Weber"))
    await store.upsert_node(Task(tenant_id="demo", id="task-chk-102", name="Refund edge cases"))
    await store.upsert_node(Pod(tenant_id="demo", id="pod-payments", name="Payments Pod"))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="U1004",
            to_node_id="task-chk-102",
            kind=EdgeKind.ASSIGNED_TO,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-payments",
            to_node_id="U1004",
            kind=EdgeKind.CONTAINS,
        )
    )
    tool = GraphNeighborsTool(tenant_id="demo", repository=store, as_of=date(2026, 9, 25))

    payload = json.loads(
        await tool.run({"node_id": "U1004", "direction": "out", "kinds": ["assigned_to"]})
    )

    assert [edge["neighbor_id"] for edge in payload["edges"]] == ["task-chk-102"]


async def test_graph_neighbors_tool_omits_an_edge_that_had_ended() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id="demo", id="U1004", name="Noah Weber"))
    await store.upsert_node(Task(tenant_id="demo", id="task-old", name="Finished work"))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="U1004",
            to_node_id="task-old",
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 6, 1),
        )
    )
    tool = GraphNeighborsTool(tenant_id="demo", repository=store, as_of=date(2026, 9, 25))

    payload = json.loads(await tool.run({"node_id": "U1004"}))

    assert payload["edges"] == []


async def test_graph_neighbors_tool_requires_a_node_id() -> None:
    tool = GraphNeighborsTool(
        tenant_id="demo", repository=InMemoryGraphStore(), as_of=date(2026, 9, 25)
    )

    payload = json.loads(await tool.run({}))

    assert payload == {"error": "node_id is required"}
