from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

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
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import CheckIn, CheckInSignals, DeveloperStatus, Mood, StatusSource
from core.ports.repositories import (
    GraphRepository,
    RollupRepository,
    StatusRepository,
    TimeSeriesRepository,
)


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


def demo_checkins(tenant_id: str) -> tuple[CheckIn, ...]:
    asked_at = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)
    replied_at = datetime(2026, 6, 15, 9, 7, tzinfo=UTC)
    return (
        CheckIn(
            tenant_id=tenant_id,
            developer_id="dev-asha",
            correlation_id="seed-checkin-asha",
            asked_at=asked_at,
            replied_at=replied_at,
            raw_reply="API shell is ready for review; no blockers.",
            signals=CheckInSignals(
                progress_note="API shell ready for review",
                blockers=(),
                mood=Mood.POSITIVE,
            ),
        ),
        CheckIn(
            tenant_id=tenant_id,
            developer_id="dev-liam",
            correlation_id="seed-checkin-liam",
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
        ),
    )


def demo_developer_statuses(tenant_id: str) -> tuple[DeveloperStatus, ...]:
    return (
        DeveloperStatus(
            tenant_id=tenant_id,
            developer_id="dev-asha",
            as_of=date(2026, 6, 15),
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="API shell is ready for review with no blockers.",
        ),
        DeveloperStatus(
            tenant_id=tenant_id,
            developer_id="dev-liam",
            as_of=date(2026, 6, 14),
            source=StatusSource.STALE,
            blockers=("schema review",),
            summary="Last update is stale while graph persistence awaits schema review.",
        ),
    )


def demo_node_statuses(tenant_id: str) -> tuple[NodeStatus, ...]:
    return (
        NodeStatus(
            entity_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.POD, id="pod-runtime"),
            rag=Rag.AMBER,
            source=StatusSource.INFERRED,
            factors=(
                RollupFactor(
                    description="Graph persistence has an unresolved schema review.",
                    contributes=Rag.AMBER,
                    source_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id="task-graph"),
                ),
            ),
            as_of=date(2026, 6, 15),
        ),
    )


async def seed_demo_graph(
    graph_repository: GraphRepository,
    time_series_repository: TimeSeriesRepository,
    tenant_id: str,
    status_repository: StatusRepository | None = None,
    rollup_repository: RollupRepository | None = None,
) -> None:
    for node in demo_nodes(tenant_id):
        await graph_repository.upsert_node(node)
    for edge in demo_edges(tenant_id):
        await graph_repository.add_edge(edge)
    for fact in demo_facts(tenant_id):
        await time_series_repository.append_fact(fact)
    status_repository = status_repository or _status_repository_from(graph_repository)
    if status_repository is not None:
        for checkin in demo_checkins(tenant_id):
            await status_repository.record_checkin(checkin)
        for developer_status in demo_developer_statuses(tenant_id):
            await status_repository.record_developer_status(developer_status)
    rollup_repository = rollup_repository or _rollup_repository_from(graph_repository)
    if rollup_repository is not None:
        for node_status in demo_node_statuses(tenant_id):
            await rollup_repository.record_node_status(node_status)


def _status_repository_from(value: object) -> StatusRepository | None:
    methods = (
        "record_checkin",
        "record_developer_status",
        "latest_developer_status",
        "developers_without_checkin",
    )
    if all(callable(getattr(value, method_name, None)) for method_name in methods):
        return cast(StatusRepository, value)
    return None


def _rollup_repository_from(value: object) -> RollupRepository | None:
    methods = ("record_node_status", "latest_node_status", "list_node_statuses")
    if all(callable(getattr(value, method_name, None)) for method_name in methods):
        return cast(RollupRepository, value)
    return None
