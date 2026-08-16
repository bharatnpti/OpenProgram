from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from core.application.persona_views import PersonaViewService
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
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
    Workstream,
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


async def test_workstream_progress_uses_latest_task_facts_for_rollup() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    workstream = Workstream(tenant_id="demo", id="workstream-1", name="Runtime Config Admin")
    green_task = Task(tenant_id="demo", id="task-green", name="Green task")
    blocked_task = Task(tenant_id="demo", id="task-blocked", name="Blocked task")
    for node in (workstream, green_task, blocked_task):
        await store.upsert_node(node)
    for task in (green_task, blocked_task):
        await store.add_edge(
            GraphEdge(
                tenant_id="demo",
                from_node_id=workstream.id,
                to_node_id=task.id,
                kind=EdgeKind.CONTAINS,
            )
        )
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="issue",
            entity_ref=green_task.ref,
            payload={"status": "green", "source": "confirmed", "confidence": 0.8},
            observed_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            correlation_id="fact-green",
        )
    )
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="issue",
            entity_ref=blocked_task.ref,
            payload={"status": "blocked", "source": "confirmed", "confidence": 0.6},
            observed_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
            correlation_id="fact-blocked",
        )
    )
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.workstream_progress("demo", workstream.id, as_of)

    assert view.workstream_id == workstream.id
    assert view.rag is Rag.RED
    assert view.total_tasks == 2
    assert view.green_tasks == 1
    assert view.red_tasks == 1
    assert view.confidence == 0.7
    assert any(factor.source_ref == blocked_task.ref for factor in view.factors)


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


async def test_node_trend_returns_daily_rag_history_in_window() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    entity_ref = EntityRef(tenant_id="demo", kind=NodeKind.PROJECT, id="project-api")
    for day, rag in (
        (date(2026, 1, 8), Rag.RED),
        (date(2026, 1, 9), Rag.AMBER),
        (date(2026, 1, 10), Rag.GREEN),
    ):
        await store.record_node_status(
            NodeStatus(
                entity_ref=entity_ref,
                rag=rag,
                source=StatusSource.CONFIRMED,
                factors=(),
                as_of=day,
            )
        )
    # A status outside the window must be excluded.
    await store.record_node_status(
        NodeStatus(
            entity_ref=entity_ref,
            rag=Rag.UNKNOWN,
            source=StatusSource.STALE,
            factors=(),
            as_of=date(2026, 1, 1),
        )
    )
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.node_trend("demo", NodeKind.PROJECT, "project-api", as_of, window_days=5)

    assert view.entity_ref == entity_ref
    assert view.window_days == 5
    assert view.start == date(2026, 1, 6)
    assert view.end == as_of
    assert [point.as_of for point in view.points] == [
        date(2026, 1, 8),
        date(2026, 1, 9),
        date(2026, 1, 10),
    ]
    assert [point.rag for point in view.points] == [Rag.RED, Rag.AMBER, Rag.GREEN]
    assert [point.score for point in view.points] == [1, 2, 3]


async def test_node_trend_is_empty_when_no_history_exists() -> None:
    store = InMemoryGraphStore()
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.node_trend(
        "demo", NodeKind.POD, "pod-runtime", date(2026, 1, 10), window_days=30
    )

    assert view.points == ()


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


async def test_pod_checkins_counts_partial_statuses() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    pod = Pod(tenant_id="demo", id="pod-runtime", name="Runtime")
    developers = (
        Developer(tenant_id="demo", id="dev-confirmed", name="Confirmed"),
        Developer(tenant_id="demo", id="dev-partial", name="Partial"),
        Developer(tenant_id="demo", id="dev-stale", name="Stale"),
        Developer(tenant_id="demo", id="dev-missing", name="Missing"),
    )
    await store.upsert_node(pod)
    for developer in developers:
        await store.upsert_node(developer)
        await store.add_edge(
            GraphEdge(
                tenant_id="demo",
                from_node_id=pod.id,
                to_node_id=developer.id,
                kind=EdgeKind.CONTAINS,
            )
        )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-confirmed",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Done.",
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-partial",
            as_of=as_of,
            source=StatusSource.PARTIAL,
            blockers=(),
            summary="Progress shared. ETA was not provided.",
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-stale",
            as_of=date(2026, 1, 9),
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Yesterday.",
        )
    )
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.pod_checkins("demo", pod.id, as_of)

    assert (view.confirmed, view.partial, view.stale, view.missing) == (1, 1, 1, 1)
    states = {developer.developer_id: developer.state for developer in view.developers}
    assert states == {
        "dev-confirmed": "confirmed",
        "dev-partial": "partial",
        "dev-stale": "stale",
        "dev-missing": "missing",
    }


async def test_pod_blockers_excludes_blockers_attributed_to_other_pod() -> None:
    store = await _two_pod_store()
    as_of = date(2026, 1, 10)
    await store.record_developer_status_with_blockers(
        _status("dev-1", as_of, blockers=("infra access",)),
        (_blocker_row("infra access", pod_id="pod-a", as_of=as_of),),
    )
    service = _persona_service(store)

    pod_a_view = await service.pod_blockers("demo", "pod-a", as_of)
    pod_b_view = await service.pod_blockers("demo", "pod-b", as_of)

    assert pod_b_view.blockers == ()
    assert len(pod_a_view.blockers) == 1
    blocker = pod_a_view.blockers[0]
    assert blocker.id == blocker.blocker_id
    assert blocker.pod_ref is not None
    assert blocker.pod_ref.id == "pod-a"
    assert blocker.unattributed is False


async def test_pod_blockers_includes_unattributed_blockers_with_flag() -> None:
    store = await _two_pod_store()
    as_of = date(2026, 1, 10)
    await store.record_developer_status_with_blockers(
        _status("dev-1", as_of, blockers=("mystery dependency",)),
        (_blocker_row("mystery dependency", as_of=as_of),),
    )
    service = _persona_service(store)

    pod_a_view = await service.pod_blockers("demo", "pod-a", as_of)
    pod_b_view = await service.pod_blockers("demo", "pod-b", as_of)

    for view in (pod_a_view, pod_b_view):
        assert len(view.blockers) == 1
        assert view.blockers[0].unattributed is True
        assert view.blockers[0].pod_ref is None


async def test_pod_blockers_age_days_counts_from_first_seen_on_not_status_date() -> None:
    store = await _two_pod_store()
    as_of = date(2026, 1, 10)
    first_seen_on = as_of - timedelta(days=5)
    await store.record_developer_status_with_blockers(
        _status("dev-1", as_of, blockers=("infra access",)),
        (_blocker_row("infra access", as_of=first_seen_on, last_seen_on=as_of),),
    )
    service = _persona_service(store)

    view = await service.pod_blockers("demo", "pod-a", as_of)

    assert len(view.blockers) == 1
    assert view.blockers[0].age_days == 5
    assert view.blockers[0].first_seen_on == first_seen_on
    assert view.blockers[0].status_as_of == as_of


async def test_pod_blockers_falls_back_to_legacy_status_blockers() -> None:
    store = await _two_pod_store()
    as_of = date(2026, 1, 10)
    status_as_of = as_of - timedelta(days=2)
    await store.record_developer_status(
        _status("dev-1", status_as_of, blockers=("waiting on schema", "no confirmed reply"))
    )
    service = _persona_service(store)

    view = await service.pod_blockers("demo", "pod-a", as_of)

    assert [blocker.description for blocker in view.blockers] == ["waiting on schema"]
    assert view.blockers[0].unattributed is True
    assert view.blockers[0].age_days == 2
    assert view.blockers[0].first_seen_on == status_as_of


async def test_pod_checkins_counts_multipod_developer_in_both_pods() -> None:
    store = await _two_pod_store()
    as_of = date(2026, 1, 10)
    await store.record_developer_status(_status("dev-1", as_of))
    service = _persona_service(store)

    pod_a_view = await service.pod_checkins("demo", "pod-a", as_of)
    pod_b_view = await service.pod_checkins("demo", "pod-b", as_of)

    # Check-in rosters are person-global by design: a multi-pod developer is
    # accountable for a check-in on every board they belong to.
    for view in (pod_a_view, pod_b_view):
        assert view.confirmed == 1
        assert [developer.developer_id for developer in view.developers] == ["dev-1"]


async def test_focus_blocker_details_carry_attribution() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    developer = Developer(tenant_id="demo", id="dev-1", name="Asha")
    pod = Pod(tenant_id="demo", id="pod-x", name="Pod X")
    task = Task(tenant_id="demo", id="task-api", name="API handoff")
    for node in (developer, pod, task):
        await store.upsert_node(node)
    await store.add_edge(
        GraphEdge(
            tenant_id="demo", from_node_id="pod-x", to_node_id="dev-1", kind=EdgeKind.CONTAINS
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo", from_node_id="pod-x", to_node_id="task-api", kind=EdgeKind.CONTAINS
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo", from_node_id="dev-1", to_node_id="task-api", kind=EdgeKind.ASSIGNED_TO
        )
    )
    first_seen_on = as_of - timedelta(days=3)
    await store.record_developer_status_with_blockers(
        _status("dev-1", as_of, blockers=("waiting on API review",)),
        (
            _blocker_row(
                "waiting on API review",
                work_item_id="task-api",
                as_of=first_seen_on,
                last_seen_on=as_of,
            ),
        ),
    )
    service = _persona_service(store)

    view = await service.focus("demo", "dev-1", as_of)

    assert view.blockers == ("waiting on API review",)
    assert len(view.blocker_details) == 1
    detail = view.blocker_details[0]
    assert detail.blocker_id == "blk-waiting-on-api-review"
    assert detail.work_item_id == "task-api"
    assert detail.work_item_name == "API handoff"
    assert detail.pod_id is None
    assert detail.unattributed is False
    assert detail.first_seen_on == first_seen_on
    assert detail.age_days == 3


def _persona_service(store: InMemoryGraphStore) -> PersonaViewService:
    return PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )


async def _two_pod_store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    developer = Developer(tenant_id="demo", id="dev-1", name="Asha")
    await store.upsert_node(developer)
    for pod_id, pod_name in (("pod-a", "Pod A"), ("pod-b", "Pod B")):
        await store.upsert_node(Pod(tenant_id="demo", id=pod_id, name=pod_name))
        await store.add_edge(
            GraphEdge(
                tenant_id="demo",
                from_node_id=pod_id,
                to_node_id="dev-1",
                kind=EdgeKind.CONTAINS,
            )
        )
    return store


def _status(
    developer_id: str,
    as_of: date,
    *,
    blockers: tuple[str, ...] = (),
) -> DeveloperStatus:
    return DeveloperStatus(
        tenant_id="demo",
        developer_id=developer_id,
        as_of=as_of,
        source=StatusSource.CONFIRMED,
        blockers=blockers,
        summary="Status summary.",
    )


def _blocker_row(
    description: str,
    *,
    as_of: date,
    developer_id: str = "dev-1",
    work_item_id: str | None = None,
    pod_id: str | None = None,
    last_seen_on: date | None = None,
) -> DeveloperBlocker:
    return DeveloperBlocker(
        tenant_id="demo",
        blocker_id=f"blk-{normalize_blocker_key(description).replace(' ', '-')}",
        developer_id=developer_id,
        description=description,
        normalized_key=normalize_blocker_key(description),
        work_item_id=work_item_id,
        pod_id=pod_id,
        source=BlockerSource.CHECKIN,
        first_seen_on=as_of,
        last_seen_on=last_seen_on or as_of,
    )


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
