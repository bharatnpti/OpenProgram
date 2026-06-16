from __future__ import annotations

from datetime import UTC, date, datetime

from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
)
from core.ports.repositories import GraphRepository, TimeSeriesRepository


def demo_nodes(tenant_id: str) -> tuple[GraphNode, ...]:
    return (
        Program(tenant_id=tenant_id, id="program-platform", name="Platform Program"),
        Project(tenant_id=tenant_id, id="project-foundations", name="Foundations"),
        Project(tenant_id=tenant_id, id="project-insights", name="Insights"),
        Pod(tenant_id=tenant_id, id="pod-runtime", name="Runtime Pod"),
        Pod(tenant_id=tenant_id, id="pod-experience", name="Experience Pod"),
        Pod(tenant_id=tenant_id, id="pod-data", name="Data Pod"),
        Developer(tenant_id=tenant_id, id="dev-asha", name="Asha"),
        Developer(tenant_id=tenant_id, id="dev-liam", name="Liam"),
        Developer(tenant_id=tenant_id, id="dev-maya", name="Maya"),
        Developer(tenant_id=tenant_id, id="dev-noah", name="Noah"),
        Developer(tenant_id=tenant_id, id="dev-zoe", name="Zoe"),
        Developer(tenant_id=tenant_id, id="dev-ira", name="Ira"),
        Developer(tenant_id=tenant_id, id="dev-kai", name="Kai"),
        Developer(tenant_id=tenant_id, id="dev-omar", name="Omar"),
        Task(tenant_id=tenant_id, id="task-api", name="FastAPI shell"),
        Task(tenant_id=tenant_id, id="task-graph", name="Graph persistence"),
        Task(tenant_id=tenant_id, id="task-chat", name="Chat adapter"),
        Task(tenant_id=tenant_id, id="task-ui", name="React shell"),
    )


def demo_edges(tenant_id: str) -> tuple[GraphEdge, ...]:
    start = date(2026, 1, 1)
    return (
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="program-platform",
            to_node_id="project-foundations",
            kind=EdgeKind.CONTAINS,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="program-platform",
            to_node_id="project-insights",
            kind=EdgeKind.CONTAINS,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="project-foundations",
            to_node_id="pod-runtime",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="project-foundations",
            to_node_id="pod-experience",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="project-insights",
            to_node_id="pod-data",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-runtime",
            to_node_id="dev-asha",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-runtime",
            to_node_id="dev-liam",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-experience",
            to_node_id="dev-maya",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-experience",
            to_node_id="dev-noah",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-data",
            to_node_id="dev-zoe",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-data",
            to_node_id="dev-ira",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-data",
            to_node_id="dev-kai",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-runtime",
            to_node_id="dev-omar",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="dev-asha",
            to_node_id="task-api",
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="dev-liam",
            to_node_id="task-graph",
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="dev-maya",
            to_node_id="task-chat",
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="dev-noah",
            to_node_id="task-ui",
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=start,
        ),
    )


def demo_facts(tenant_id: str) -> tuple[FactEvent, ...]:
    now = datetime.now(tz=UTC)
    return (
        FactEvent(
            tenant_id=tenant_id,
            source="seed",
            entity_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id="task-api"),
            payload={"status": "green", "confidence": 0.8},
            observed_at=now,
            correlation_id="seed-demo",
        ),
        FactEvent(
            tenant_id=tenant_id,
            source="seed",
            entity_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id="task-graph"),
            payload={"status": "amber", "confidence": 0.6},
            observed_at=now,
            correlation_id="seed-demo",
        ),
    )


async def seed_demo_graph(
    graph_repository: GraphRepository,
    time_series_repository: TimeSeriesRepository,
    tenant_id: str,
) -> None:
    for node in demo_nodes(tenant_id):
        await graph_repository.upsert_node(node)
    for edge in demo_edges(tenant_id):
        await graph_repository.add_edge(edge)
    for fact in demo_facts(tenant_id):
        await time_series_repository.append_fact(fact)
