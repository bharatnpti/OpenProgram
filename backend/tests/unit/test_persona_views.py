from __future__ import annotations

from datetime import UTC, date, datetime

from core.application.persona_views import PersonaViewService
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphTree,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
)
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore


async def test_focus_falls_back_to_status_when_developer_graph_is_missing() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-missing",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("waiting on API review",),
            summary="Finishing handoff.",
        )
    )
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.focus("demo", "dev-missing", as_of)

    assert view.developer_id == "dev-missing"
    assert view.developer_name == "dev-missing"
    assert view.status_source is StatusSource.CONFIRMED
    assert view.summary == "Finishing handoff."
    assert view.tasks == ()
    assert [(item.kind, item.label) for item in view.focus] == [
        ("blocker", "waiting on API review")
    ]


async def test_focus_does_not_expose_raw_checkin_fact_content() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    raw_reply = "raw private reply about blockers"
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-missing",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("waiting on API review",),
            summary="Finishing handoff.",
        )
    )
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="checkin",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-missing"),
            payload={"raw_reply": raw_reply, "status_source": "confirmed"},
            observed_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
            correlation_id="corr-1",
        )
    )
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.focus("demo", "dev-missing", as_of)

    assert raw_reply not in str(view)
    assert view.summary == "Finishing handoff."


async def test_focus_uses_latest_task_fact_and_metadata_fallbacks() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    developer = await _populate_developer_task_tree(store)
    task_ref = EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-api")
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="issue",
            entity_ref=task_ref,
            payload={"status": "green", "source": "confirmed", "confidence": 0.2},
            observed_at=datetime(2026, 1, 8, 9, 0, tzinfo=UTC),
            correlation_id="fact-old",
        )
    )
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="issue",
            entity_ref=task_ref,
            payload={"status": "blocked", "source": "not-a-source", "confidence": 2.5},
            observed_at=datetime(2026, 1, 9, 9, 0, tzinfo=UTC),
            correlation_id="fact-new",
        )
    )
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.focus("demo", developer.id, as_of)

    assert view.tasks[0].rag is Rag.RED
    assert view.tasks[0].source is StatusSource.INFERRED
    assert view.tasks[0].confidence == 1.0
    assert view.tasks[0].deadline == date(2026, 1, 12)
    assert [(item.kind, item.label) for item in view.focus] == [("task", "API handoff")]


async def test_project_progress_aggregates_tasks_when_no_rollup_status_exists() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    task_green = Task(
        tenant_id="demo",
        id="task-green",
        name="Green task",
        metadata={"status": "done"},
    )
    task_amber = Task(
        tenant_id="demo",
        id="task-amber",
        name="Amber task",
        metadata={"status": "at-risk"},
    )
    for task in (task_green, task_amber):
        await store.upsert_node(task)
    service = PersonaViewService(
        graph_repository=_StaticGraphRepository(
            root=task_green,
            nodes=(task_green, task_amber),
            edges=(),
        ),
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.project_progress("demo", "task-green", as_of)

    assert view.rag is Rag.AMBER
    assert view.source is StatusSource.UNKNOWN
    assert view.percent_complete == 50.0
    assert view.total_tasks == 2
    assert view.green_tasks == 1
    assert view.amber_tasks == 1


async def test_portfolio_heatmap_uses_existing_rollups_without_graph_fallback() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    source_ref = EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-api")
    await store.record_node_status(
        NodeStatus(
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.PROJECT, id="project-api"),
            rag=Rag.AMBER,
            source=StatusSource.INFERRED,
            factors=(
                RollupFactor(
                    description="Blocker: schema review",
                    contributes=Rag.AMBER,
                    source_ref=source_ref,
                ),
            ),
            as_of=as_of,
        )
    )
    await store.record_node_status(
        NodeStatus(
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.POD, id="pod-runtime"),
            rag=Rag.GREEN,
            source=StatusSource.CONFIRMED,
            factors=(),
            as_of=as_of,
        )
    )
    service = PersonaViewService(
        graph_repository=_FailingGraphRepository(),
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.portfolio_heatmap("demo", as_of, "program-platform")

    assert view.rows == ("pod", "project")
    assert view.columns == ("pod-runtime", "project-api")
    assert view.cells[0].why == "No rollup factors are available."
    assert view.cells[0].source_ref == view.cells[0].entity_ref
    assert view.cells[1].why == "Blocker: schema review"
    assert view.cells[1].source_ref == source_ref


async def test_portfolio_heatmap_uses_first_configured_program_when_root_is_omitted() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    program = Program(tenant_id="demo", id="program-alpha", name="Alpha")
    project = Project(tenant_id="demo", id="project-alpha", name="Project")
    pod = Pod(tenant_id="demo", id="pod-alpha", name="Pod")
    developer = Developer(tenant_id="demo", id="dev-ada", name="Ada")
    for node in (program, project, pod, developer):
        await store.upsert_node(node)
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id=program.id,
            to_node_id=project.id,
            kind=EdgeKind.CONTAINS,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id=project.id,
            to_node_id=pod.id,
            kind=EdgeKind.CONTAINS,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id=pod.id,
            to_node_id=developer.id,
            kind=EdgeKind.CONTAINS,
        )
    )
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.portfolio_heatmap("demo", as_of)

    assert {cell.entity_ref.id for cell in view.cells} >= {
        "program-alpha",
        "project-alpha",
        "pod-alpha",
        "dev-ada",
    }


async def _populate_developer_task_tree(store: InMemoryGraphStore) -> Program:
    program = Program(tenant_id="demo", id="dev-1", name="Asha")
    project = Project(tenant_id="demo", id="project-api", name="API")
    task = Task(
        tenant_id="demo",
        id="task-api",
        name="API handoff",
        metadata={"deadline": "not-a-date", "due_date": "2026-01-12"},
    )
    for node in (program, project, task):
        await store.upsert_node(node)
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id=program.id,
            to_node_id=project.id,
            kind=EdgeKind.CONTAINS,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id=project.id,
            to_node_id=task.id,
            kind=EdgeKind.CONTAINS,
        )
    )
    return program


class _StaticGraphRepository:
    def __init__(
        self,
        *,
        root: Task,
        nodes: tuple[Task, ...],
        edges: tuple[GraphEdge, ...],
    ) -> None:
        self._root = root
        self._nodes = nodes
        self._edges = edges

    async def get_program_tree(
        self,
        tenant_id: str,
        root_id: str,
        as_of: date,
    ) -> GraphTree:
        assert tenant_id == "demo"
        assert root_id == self._root.id
        return GraphTree(root=self._root, nodes=self._nodes, edges=self._edges)


class _FailingGraphRepository:
    async def get_program_tree(
        self,
        tenant_id: str,
        root_id: str,
        as_of: date,
    ) -> GraphTree:
        raise AssertionError("existing rollups should avoid graph fallback")
