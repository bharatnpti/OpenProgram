from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from core.application.persona_views import PersonaViewService
from core.application.rollup_service import RollupService
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    GraphTree,
    JsonScalar,
    NodeKind,
    Pod,
    Program,
    Project,
    SprintNode,
    Task,
    WorkItem,
    Workstream,
)
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
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
    # project -> workstream -> work item -> task, the chain the seeder builds
    # and the only shape `ConfigService`'s owning links can produce.
    project = Project(tenant_id="demo", id="project-1", name="Checkout")
    workstream = Workstream(tenant_id="demo", id="ws-1", name="Payments")
    work_item = WorkItem(tenant_id="demo", id="WI-1", name="Refunds")
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
            root=project,
            nodes=(project, workstream, work_item, task_green, task_amber),
            edges=(
                _contains(project.id, workstream.id),
                _contains(workstream.id, work_item.id),
                _contains(work_item.id, task_green.id),
                _contains(work_item.id, task_amber.id),
            ),
        ),
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.project_progress("demo", project.id, as_of)

    assert view.rag is Rag.AMBER
    assert view.percent_complete == 50.0
    assert view.total_tasks == 2
    assert view.green_tasks == 1
    assert view.amber_tasks == 1
    # Nothing is stored, so `_node_statuses_for_tree` computes the project's
    # rollup from its children and the view reports that provenance. Derived
    # is not the same as unknown: we did work out a status here.
    assert view.source is StatusSource.INFERRED


async def test_project_and_workstream_progress_show_each_tasks_tracker_status() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 10, 3)
    project = Project(tenant_id="demo", id="project-checkout", name="Checkout")
    workstream = Workstream(tenant_id="demo", id="ws-payments", name="Payments")
    synced = Task(
        tenant_id="demo",
        id="CHK-4",
        name="3-D Secure step-up flow",
        metadata={"key": "CHK-4", "status": "In Progress", "state": "in_progress"},
    )
    seeded = Task(tenant_id="demo", id="task-seeded", name="Seeded", metadata={"status": "green"})
    for node in (project, workstream, synced, seeded):
        await store.upsert_node(node)
    for parent, child in ((project, workstream), (workstream, synced), (workstream, seeded)):
        await store.add_edge(_contains(parent.id, child.id))
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    project_view = await service.project_progress("demo", project.id, as_of)
    workstream_view = await service.workstream_progress("demo", workstream.id, as_of)

    for view in (project_view, workstream_view):
        assert {task.id: task.tracker_status for task in view.tasks} == {
            "CHK-4": "In Progress",
            "task-seeded": None,
        }


async def test_project_progress_excludes_tasks_reached_through_a_shared_pod() -> None:
    """A pod serving two projects must not lend one project the other's tasks.

    `get_program_tree` is an untyped closure over `contains`/`assigned_to`, so
    `project-ours > pod-shared > ws-theirs > WI-theirs > task-theirs` is
    reachable and a flat scan for task nodes counted it. That made one project
    report progress over another's work, skewing optimistic when the borrowed
    tasks were green -- which is exactly what a shared platform pod produces.
    """
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    ours = Project(tenant_id="demo", id="project-ours", name="Ours")
    our_ws = Workstream(tenant_id="demo", id="ws-ours", name="Our stream")
    our_wi = WorkItem(tenant_id="demo", id="WI-ours", name="Our item")
    our_task = Task(
        tenant_id="demo",
        id="task-ours",
        name="Our task",
        metadata={"status": "at-risk"},
    )
    # The shared pod hangs off our project, but the workstream it is assigned
    # to -- and everything under it -- belongs to another project.
    shared_pod = Pod(tenant_id="demo", id="pod-shared", name="Platform Pod")
    their_ws = Workstream(tenant_id="demo", id="ws-theirs", name="Their stream")
    their_wi = WorkItem(tenant_id="demo", id="WI-theirs", name="Their item")
    their_task = Task(
        tenant_id="demo",
        id="task-theirs",
        name="Their task",
        metadata={"status": "done"},
    )
    for task in (our_task, their_task):
        await store.upsert_node(task)
    service = PersonaViewService(
        graph_repository=_StaticGraphRepository(
            root=ours,
            nodes=(ours, our_ws, our_wi, our_task, shared_pod, their_ws, their_wi, their_task),
            edges=(
                _contains(ours.id, our_ws.id),
                _contains(our_ws.id, our_wi.id),
                _contains(our_wi.id, our_task.id),
                _contains(ours.id, shared_pod.id),
                GraphEdge(
                    tenant_id="demo",
                    from_node_id=shared_pod.id,
                    to_node_id=their_ws.id,
                    kind=EdgeKind.ASSIGNED_TO,
                ),
                _contains(their_ws.id, their_wi.id),
                _contains(their_wi.id, their_task.id),
            ),
        ),
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.project_progress("demo", ours.id, as_of)

    assert [task.id for task in view.tasks] == ["task-ours"]
    assert view.total_tasks == 1
    assert view.green_tasks == 0
    assert view.amber_tasks == 1
    # The borrowed task was green; counting it would have read 50%, not 0%.
    assert view.percent_complete == 0.0


async def test_project_progress_counts_the_tasks_it_contains_not_its_peoples_assignments() -> None:
    """A person working in two projects, and a pod both projects share.

    Jira tickets hang off a project, a sprint or a pod, and off their assignee.
    The project's tree follows the assignments into the other project, so
    counting every task in it put each project's tickets in both.
    """
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    checkout = Project(
        tenant_id="demo", id="project-chk", name="Checkout", metadata={"jira_project_key": "CHK"}
    )
    identity = Project(
        tenant_id="demo", id="project-idp", name="Identity", metadata={"jira_project_key": "IDP"}
    )
    checkout_pod = Pod(tenant_id="demo", id="pod-checkout", name="Checkout Pod")
    identity_pod = Pod(tenant_id="demo", id="pod-identity", name="Identity Pod")
    # One Jira filter (a label) spans both projects, so this pod holds both
    # projects' tickets.
    platform_pod = Pod(tenant_id="demo", id="pod-platform", name="Platform Pod")
    noah = Developer(tenant_id="demo", id="dev-noah", name="Noah")
    omar = Developer(tenant_id="demo", id="dev-omar", name="Omar")
    sprint = SprintNode(tenant_id="demo", id="sprint-chk", name="CHK Sprint 1")
    tickets = {
        "CHK-1": _ticket("CHK-1", "Done", "done"),
        "CHK-2": _ticket("CHK-2", "In Progress", "in_progress"),
        "IDP-3": _ticket("IDP-3", "Done", "done"),
        "CHK-17": _ticket("CHK-17", "To Do", "todo"),
        "IDP-8": _ticket("IDP-8", "In Progress", "in_progress"),
    }
    # Assigned to Noah with no container at all: unclaimed, not foreign.
    loose = Task(tenant_id="demo", id="task-loose", name="Loose task")
    for node in (
        checkout,
        identity,
        checkout_pod,
        identity_pod,
        platform_pod,
        noah,
        omar,
        sprint,
        loose,
        *tickets.values(),
    ):
        await store.upsert_node(node)
    for parent, child in (
        (checkout, checkout_pod),
        (identity, identity_pod),
        (checkout, platform_pod),
        (identity, platform_pod),
        (checkout_pod, noah),
        (identity_pod, noah),
        (platform_pod, omar),
        (checkout, sprint),
        (checkout, tickets["CHK-1"]),
        (sprint, tickets["CHK-2"]),
        (identity, tickets["IDP-3"]),
        (platform_pod, tickets["CHK-17"]),
        (platform_pod, tickets["IDP-8"]),
    ):
        await store.add_edge(_contains(parent.id, child.id))
    for assignee, task_id in (
        (noah, "CHK-1"),
        (noah, "CHK-2"),
        (noah, "IDP-3"),
        (noah, "task-loose"),
        (omar, "CHK-17"),
        (omar, "IDP-8"),
    ):
        await store.add_edge(
            GraphEdge(
                tenant_id="demo",
                from_node_id=assignee.id,
                to_node_id=task_id,
                kind=EdgeKind.ASSIGNED_TO,
            )
        )
    service = _persona_service(store)

    checkout_view = await service.project_progress("demo", checkout.id, as_of)
    identity_view = await service.project_progress("demo", identity.id, as_of)

    assert {task.id for task in checkout_view.tasks} == {"CHK-1", "CHK-2", "CHK-17", "task-loose"}
    assert (checkout_view.total_tasks, checkout_view.green_tasks) == (4, 1)
    assert checkout_view.percent_complete == 25.0
    assert {task.id for task in identity_view.tasks} == {"IDP-3", "IDP-8", "task-loose"}
    assert (identity_view.total_tasks, identity_view.green_tasks) == (3, 1)


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


async def test_workstream_progress_leaves_open_tickets_out_of_its_colour_and_reasons() -> None:
    # To do and in progress tickets have no colour of their own. They used to
    # turn the workstream amber, each listed as "Task ... is unknown.".
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    workstream = Workstream(tenant_id="demo", id="ws-1", name="Payments")
    tickets = (
        _ticket("QA-1", "In Progress", "in_progress"),
        _ticket("QA-2", "To Do", "todo"),
        _ticket("QA-3", "Closed", "done"),
    )
    for node in (workstream, *tickets):
        await store.upsert_node(node)
    for ticket in tickets:
        await store.add_edge(_contains(workstream.id, ticket.id))

    view = await _persona_service(store).workstream_progress("demo", workstream.id, as_of)

    assert view.rag is Rag.UNKNOWN
    assert [(factor.description, factor.source_ref.id) for factor in view.factors] == [
        ("No child task status data is available.", "ws-1")
    ]
    # Each ticket keeps its own colour: done is green whatever the tracker calls it.
    assert {task.id: task.rag for task in view.tasks} == {
        "QA-1": Rag.UNKNOWN,
        "QA-2": Rag.UNKNOWN,
        "QA-3": Rag.GREEN,
    }
    assert (view.green_tasks, view.unknown_tasks) == (1, 2)


async def test_a_ticket_blocked_under_another_status_name_is_red_and_the_reason() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    workstream = Workstream(tenant_id="demo", id="ws-1", name="Payments")
    held = _ticket("QA-7", "On Hold", "blocked")
    moving = _ticket("QA-8", "In Progress", "in_progress")
    for node in (workstream, held, moving):
        await store.upsert_node(node)
    for ticket in (held, moving):
        await store.add_edge(_contains(workstream.id, ticket.id))

    view = await _persona_service(store).workstream_progress("demo", workstream.id, as_of)

    assert view.rag is Rag.RED
    assert {task.id: task.rag for task in view.tasks} == {"QA-7": Rag.RED, "QA-8": Rag.UNKNOWN}
    assert {factor.source_ref.id for factor in view.factors} == {"QA-7"}


async def test_progress_routes_refuse_wrong_node_kinds() -> None:
    """project_progress requires a project id, workstream_progress requires a workstream id."""
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    project = Project(tenant_id="demo", id="project-a", name="Project A")
    workstream = Workstream(tenant_id="demo", id="ws-a", name="Workstream A")
    pod = Pod(tenant_id="demo", id="pod-a", name="Pod A")
    developer = Developer(tenant_id="demo", id="dev-1", name="Dev One")
    for node in (project, workstream, pod, developer):
        await store.upsert_node(node)
    for from_id, to_id in (
        (project.id, workstream.id),
        (workstream.id, pod.id),
        (pod.id, developer.id),
    ):
        await store.add_edge(
            GraphEdge(
                tenant_id="demo", from_node_id=from_id, to_node_id=to_id, kind=EdgeKind.CONTAINS
            )
        )
    service = _persona_service(store)

    # Valid ids work
    project_view = await service.project_progress("demo", project.id, as_of)
    assert project_view.project_id == project.id
    workstream_view = await service.workstream_progress("demo", workstream.id, as_of)
    assert workstream_view.workstream_id == workstream.id

    # Wrong node kind or missing id raises GraphNotFound
    for node_id in (workstream.id, pod.id, developer.id, "project-missing"):
        with pytest.raises(GraphNotFound):
            await service.project_progress("demo", node_id, as_of)

    for node_id in (project.id, pod.id, developer.id, "workstream-missing"):
        with pytest.raises(GraphNotFound):
            await service.workstream_progress("demo", node_id, as_of)


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


async def test_portfolio_heatmap_names_what_drives_an_amber_parent() -> None:
    """N2: "Status is partial" was the program's whole reason beside an open blocker."""
    store = InMemoryGraphStore()
    as_of = date(2026, 10, 3)
    for node in (
        Developer(tenant_id="demo", id="U-zoe", name="Zoe Almeida"),
        Developer(tenant_id="demo", id="U-ira", name="Ira Novak"),
        Developer(tenant_id="demo", id="U-sam", name="Sam Lee"),
        Developer(tenant_id="demo", id="U-kai", name="Kai Berg"),
        Task(
            tenant_id="demo",
            id="CHK-8",
            name="Payment form validation",
            metadata={"key": "CHK-8", "status": "In Review", "state": "in_progress"},
        ),
    ):
        await store.upsert_node(node)

    def ref(kind: NodeKind, node_id: str) -> EntityRef:
        return EntityRef(tenant_id="demo", kind=kind, id=node_id)

    def partial(developer_id: str) -> RollupFactor:
        return RollupFactor(
            description="Status is partial and needs blocker or ETA confirmation.",
            contributes=Rag.AMBER,
            source_ref=ref(NodeKind.DEVELOPER, developer_id),
            kind=FactorKind.STATUS,
        )

    blocker = RollupFactor(
        description="Blocker: CHK-8 waits on a review nobody has started.",
        contributes=Rag.AMBER,
        source_ref=ref(NodeKind.TASK, "CHK-8"),
        kind=FactorKind.BLOCKER,
        blocker_id="blocker-chk-8",
        work_item_ref=ref(NodeKind.TASK, "CHK-8"),
    )
    inferred = RollupFactor(
        description="Status is inferred and needs confirmation.",
        contributes=Rag.AMBER,
        source_ref=ref(NodeKind.DEVELOPER, "U-kai"),
        kind=FactorKind.STATUS,
    )
    stored = (
        (ref(NodeKind.DEVELOPER, "U-zoe"), StatusSource.CONFIRMED, (blocker,)),
        (ref(NodeKind.DEVELOPER, "U-ira"), StatusSource.PARTIAL, (partial("U-ira"),)),
        (ref(NodeKind.DEVELOPER, "U-sam"), StatusSource.PARTIAL, (partial("U-sam"),)),
        (ref(NodeKind.DEVELOPER, "U-kai"), StatusSource.INFERRED, (inferred,)),
        (
            ref(NodeKind.POD, "pod-payments"),
            StatusSource.PARTIAL,
            (partial("U-ira"), blocker),
        ),
        (
            ref(NodeKind.PROGRAM, "program-platform"),
            StatusSource.PARTIAL,
            # The first factor is a partial update, as in the run.
            (partial("U-ira"), blocker, partial("U-sam"), inferred),
        ),
    )
    for entity_ref, source, factors in stored:
        await store.record_node_status(
            NodeStatus(
                entity_ref=entity_ref,
                rag=Rag.AMBER,
                source=source,
                factors=factors,
                as_of=as_of,
            )
        )
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.portfolio_heatmap("demo", as_of, "program-platform")

    why = {cell.column: cell.why for cell in view.cells}
    assert why["program-platform"] == (
        "1 open blocker (CHK-8, Zoe Almeida); 2 partial updates (Ira Novak; Sam Lee); "
        "1 inferred status (Kai Berg)."
    )
    assert why["pod-payments"] == (
        "1 open blocker (CHK-8, Zoe Almeida); 1 partial update (Ira Novak)."
    )
    # A person with one reason keeps it: that reason is the whole story.
    assert why["U-ira"] == "Status is partial and needs blocker or ETA confirmation."
    assert why["U-zoe"] == "Blocker: CHK-8 waits on a review nobody has started."
    # The cell still points at its first factor's source.
    program_cell = next(cell for cell in view.cells if cell.column == "program-platform")
    assert program_cell.source_ref == ref(NodeKind.DEVELOPER, "U-ira")
    # Names and issue keys only, never a node id.
    assert not any(
        node_id in why["program-platform"] for node_id in ("U-zoe", "U-ira", "U-sam", "U-kai")
    )


async def test_portfolio_heatmap_sums_up_a_red_parent_from_the_computed_rollup() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 10, 3)
    program = Program(tenant_id="demo", id="program-alpha", name="Alpha")
    project = Project(tenant_id="demo", id="project-alpha", name="Checkout")
    pod = Pod(tenant_id="demo", id="pod-alpha", name="Payments")
    workstream = Workstream(tenant_id="demo", id="ws-refunds", name="Refunds")
    zoe = Developer(tenant_id="demo", id="U-zoe", name="Zoe Almeida")
    omar = Developer(tenant_id="demo", id="U-omar", name="Omar Haddad")
    ira = Developer(tenant_id="demo", id="U-ira", name="Ira Novak")
    task = Task(
        tenant_id="demo",
        id="CHK-5",
        name="Refund API",
        metadata={"key": "CHK-5", "status": "On Hold", "state": "blocked"},
    )
    for node in (program, project, pod, workstream, zoe, omar, ira, task):
        await store.upsert_node(node)
    for parent, child in (
        (program, project),
        (project, pod),
        (project, workstream),
        (pod, zoe),
        (pod, omar),
        (pod, ira),
        (workstream, task),
    ):
        await store.add_edge(
            GraphEdge(
                tenant_id="demo",
                from_node_id=parent.id,
                to_node_id=child.id,
                kind=EdgeKind.CONTAINS,
            )
        )
    for developer, source, blockers in (
        (zoe, StatusSource.CONFIRMED, ("waiting on a review", "waiting on the client upgrade")),
        (omar, StatusSource.CONFIRMED, ("waiting on vendor access",)),
        (ira, StatusSource.PARTIAL, ()),
    ):
        await store.record_developer_status(
            DeveloperStatus(
                tenant_id="demo",
                developer_id=developer.id,
                as_of=as_of,
                source=source,
                blockers=blockers,
                summary="Working on checkout.",
            )
        )
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.portfolio_heatmap("demo", as_of, "program-alpha")

    cells = {cell.column: cell for cell in view.cells}
    assert cells["program-alpha"].rag is Rag.RED
    assert cells["program-alpha"].why == (
        "3 open blockers (Zoe Almeida; Omar Haddad); 1 blocked task (CHK-5); "
        "1 partial update (Ira Novak)."
    )
    assert cells["ws-refunds"].why == "1 blocked task (CHK-5)."
    # Red from two blockers that are amber alone: the count says why. Her own
    # cell does not name her.
    assert cells["U-zoe"].rag is Rag.RED
    assert cells["U-zoe"].why == "2 open blockers."
    assert cells["U-omar"].why == "Blocker: waiting on vendor access"
    # Green and unknown cells keep their own reason.
    assert cells["U-ira"].why == "Status is partial and needs blocker or ETA confirmation."


async def _omar_partial_org(as_of: date) -> InMemoryGraphStore:
    """Live R5 (N44), with made-up ids: Omar sits in Platform and Data, and closes partial.

    Platform is in Checkout Revamp, Data in Customer Insights, and both
    projects in the program. Sofia, in Data too, confirmed.
    """
    store = InMemoryGraphStore()
    program = Program(tenant_id="demo", id="program-acme", name="Acme")
    checkout = Project(tenant_id="demo", id="project-checkout", name="Checkout Revamp")
    insights = Project(tenant_id="demo", id="project-insights", name="Customer Insights")
    platform = Pod(tenant_id="demo", id="pod-platform", name="Platform")
    data = Pod(tenant_id="demo", id="pod-data", name="Data")
    omar = Developer(tenant_id="demo", id="dev-omar", name="Omar Haddad")
    sofia = Developer(tenant_id="demo", id="dev-sofia", name="Sofia Bergmann")
    for node in (program, checkout, insights, platform, data, omar, sofia):
        await store.upsert_node(node)
    for parent, child in (
        (program, checkout),
        (program, insights),
        (checkout, platform),
        (insights, data),
        (platform, omar),
        (data, omar),
        (data, sofia),
    ):
        await store.add_edge(
            GraphEdge(
                tenant_id="demo",
                from_node_id=parent.id,
                to_node_id=child.id,
                kind=EdgeKind.CONTAINS,
            )
        )
    for developer, source in ((omar, StatusSource.PARTIAL), (sofia, StatusSource.CONFIRMED)):
        await store.record_developer_status(
            DeveloperStatus(
                tenant_id="demo",
                developer_id=developer.id,
                as_of=as_of,
                source=source,
                blockers=(),
                summary="Working on the data export.",
            )
        )
    return store


@pytest.mark.parametrize("stored", [True, False], ids=["stored", "computed"])
@pytest.mark.parametrize("program_root_id", ["program-acme", None], ids=["by-id", "tree"])
async def test_portfolio_heatmap_names_the_person_behind_a_partial_update(
    stored: bool, program_root_id: str | None
) -> None:
    """N44: Omar closed partial ("ETA was not provided"), and only his own cell said who.

    Platform, Data, Checkout Revamp, Customer Insights and the program read
    "1 partial update." while a blocker or drift cell names its person (N2,
    N3). Each now names him the same way, whether the map reads the stored
    rollup rows -- by id or through the tree -- or computes them.
    """
    as_of = date(2026, 10, 4)
    store = await _omar_partial_org(as_of)
    if stored:
        tree = await store.get_program_tree("demo", "program-acme", as_of)
        for status in await RollupService(store).compute(tree, as_of):
            await store.record_node_status(status)
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.portfolio_heatmap("demo", as_of, program_root_id)

    cells = {cell.column: cell for cell in view.cells}
    for node_id in (
        "pod-platform",
        "pod-data",
        "project-checkout",
        "project-insights",
        "program-acme",
    ):
        assert cells[node_id].rag is Rag.AMBER, node_id
        assert cells[node_id].why == "1 partial update (Omar Haddad).", node_id
    # His own cell keeps its one reason, which is the whole story.
    assert cells["dev-omar"].why == "Status is partial and needs blocker or ETA confirmation."
    assert cells["dev-sofia"].rag is Rag.GREEN
    assert not any("dev-omar" in cell.why for cell in view.cells)


async def test_portfolio_heatmap_names_people_as_it_names_blockers() -> None:
    """N44: two people named at most, then "; N more"; none on their own cell; never an id."""
    store = InMemoryGraphStore()
    as_of = date(2026, 10, 4)
    for node in (
        Developer(tenant_id="demo", id="dev-omar", name="Omar Haddad"),
        Developer(tenant_id="demo", id="dev-sofia", name="Sofia Bergmann"),
        Developer(tenant_id="demo", id="dev-raj", name="Raj Mehta"),
        Developer(tenant_id="demo", id="dev-noah", name="Noah Weber"),
        Developer(tenant_id="demo", id="dev-ira", name="Ira Novak"),
        Task(tenant_id="demo", id="IDP-3", name="Token refresh", metadata={"key": "IDP-3"}),
    ):
        await store.upsert_node(node)
    # dev-ghost has a status but no graph node, so no name to print.

    def ref(kind: NodeKind, node_id: str) -> EntityRef:
        return EntityRef(tenant_id="demo", kind=kind, id=node_id)

    def partial(developer_id: str) -> RollupFactor:
        return RollupFactor(
            description="Status is partial and needs blocker or ETA confirmation.",
            contributes=Rag.AMBER,
            source_ref=ref(NodeKind.DEVELOPER, developer_id),
            kind=FactorKind.STATUS,
        )

    missing = RollupFactor(
        description="No developer status data is available.",
        contributes=Rag.UNKNOWN,
        source_ref=ref(NodeKind.DEVELOPER, "dev-raj"),
        kind=FactorKind.STATUS,
    )
    drift = RollupFactor(
        description="Signals disagree: ETAs given for IDP-3 do not overlap.",
        contributes=Rag.AMBER,
        source_ref=ref(NodeKind.DEVELOPER, "dev-noah"),
        kind=FactorKind.DRIFT,
        work_item_ref=ref(NodeKind.TASK, "IDP-3"),
    )
    stored = (
        (ref(NodeKind.DEVELOPER, "dev-omar"), StatusSource.PARTIAL, (partial("dev-omar"),)),
        (ref(NodeKind.DEVELOPER, "dev-sofia"), StatusSource.PARTIAL, (partial("dev-sofia"),)),
        (ref(NodeKind.DEVELOPER, "dev-ira"), StatusSource.PARTIAL, (partial("dev-ira"),)),
        (ref(NodeKind.DEVELOPER, "dev-ghost"), StatusSource.PARTIAL, (partial("dev-ghost"),)),
        # Noah's own cell: partial, and drift on his issue.
        (
            ref(NodeKind.DEVELOPER, "dev-noah"),
            StatusSource.PARTIAL,
            (partial("dev-noah"), drift),
        ),
        (ref(NodeKind.POD, "pod-identity"), StatusSource.PARTIAL, (drift, partial("dev-omar"))),
        (
            ref(NodeKind.POD, "pod-data"),
            StatusSource.UNKNOWN,
            (partial("dev-ghost"), partial("dev-omar"), missing),
        ),
        (ref(NodeKind.POD, "pod-platform"), StatusSource.PARTIAL, (partial("dev-ghost"),)),
        (
            ref(NodeKind.PROGRAM, "program-acme"),
            StatusSource.UNKNOWN,
            (
                partial("dev-omar"),
                partial("dev-sofia"),
                partial("dev-noah"),
                partial("dev-ira"),
            ),
        ),
    )
    for entity_ref, source, factors in stored:
        await store.record_node_status(
            NodeStatus(
                entity_ref=entity_ref,
                rag=Rag.AMBER,
                source=source,
                factors=factors,
                as_of=as_of,
            )
        )
    service = PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )

    view = await service.portfolio_heatmap("demo", as_of, "program-acme")

    why = {cell.column: cell.why for cell in view.cells}
    # Two named at most, as with blockers.
    assert why["program-acme"] == "4 partial updates (Omar Haddad; Sofia Bergmann; 2 more)."
    # The live R5 program: Noah's drift beside Omar's partial update.
    assert why["pod-identity"] == (
        "Signals disagree: ETAs given for IDP-3 do not overlap (Noah Weber); "
        "1 partial update (Omar Haddad)."
    )
    # Counted all the same, but a person with no name to print is left unnamed.
    assert why["pod-data"] == "2 partial updates (Omar Haddad); 1 missing update (Raj Mehta)."
    assert why["pod-platform"] == "1 partial update."
    # A person's own cell names nobody.
    noah = "Signals disagree: ETAs given for IDP-3 do not overlap; 1 partial update."
    assert why["dev-noah"] == noah
    # Names only, never a node id.
    assert not any("dev-" in reason for reason in why.values())


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


async def test_pod_views_refuse_a_node_that_is_not_a_pod() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    project = Project(tenant_id="demo", id="project-a", name="Project A")
    pod = Pod(tenant_id="demo", id="pod-a", name="Pod A")
    developer = Developer(tenant_id="demo", id="dev-1", name="Dev One")
    for node in (project, pod, developer):
        await store.upsert_node(node)
    for from_id, to_id in ((project.id, pod.id), (pod.id, developer.id)):
        await store.add_edge(
            GraphEdge(
                tenant_id="demo", from_node_id=from_id, to_node_id=to_id, kind=EdgeKind.CONTAINS
            )
        )
    service = _persona_service(store)

    assert (await service.pod_checkins("demo", pod.id, as_of)).pod_id == pod.id
    assert (await service.pod_blockers("demo", pod.id, as_of)).pod_id == pod.id
    for node_id in (project.id, developer.id, "pod-missing"):
        with pytest.raises(GraphNotFound):
            await service.pod_checkins("demo", node_id, as_of)
        with pytest.raises(GraphNotFound):
            await service.pod_blockers("demo", node_id, as_of)


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


def _ticket(key: str, status: str, state: str) -> Task:
    """A task as the Jira sync writes it: status name, state and Jira project."""
    metadata: dict[str, JsonScalar] = {
        "key": key,
        "status": status,
        "state": state,
        "project_key": key.rsplit("-", 1)[0],
    }
    return Task(tenant_id="demo", id=key, name=f"Ticket {key}", metadata=metadata)


def _contains(from_id: str, to_id: str) -> GraphEdge:
    return GraphEdge(
        tenant_id="demo",
        from_node_id=from_id,
        to_node_id=to_id,
        kind=EdgeKind.CONTAINS,
    )


class _StaticGraphRepository:
    def __init__(
        self,
        *,
        root: GraphNode,
        nodes: tuple[GraphNode, ...],
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

    # The static graph is the whole tenant: ownership reads it through these.
    async def list_nodes(
        self, tenant_id: str, kind: NodeKind | None = None, *, as_of: date | None = None
    ) -> list[GraphNode]:
        return [node for node in self._nodes if kind is None or node.kind is kind]

    async def list_edges(
        self,
        tenant_id: str,
        from_node_id: str | None = None,
        to_node_id: str | None = None,
        kind: EdgeKind | None = None,
    ) -> list[GraphEdge]:
        return [
            edge
            for edge in self._edges
            if (kind is None or edge.kind is kind)
            and (from_node_id is None or edge.from_node_id == from_node_id)
            and (to_node_id is None or edge.to_node_id == to_node_id)
        ]


class _FailingGraphRepository:
    async def get_program_tree(
        self,
        tenant_id: str,
        root_id: str,
        as_of: date,
    ) -> GraphTree:
        raise AssertionError("existing rollups should avoid graph fallback")

    # The cell reasons count who is in each team from the flat node and edge
    # lists, read once; they never walk the tree.
    async def list_nodes(
        self, tenant_id: str, kind: NodeKind | None = None, *, as_of: date | None = None
    ) -> list[GraphNode]:
        return []

    async def list_edges(self, tenant_id: str, **_: object) -> list[GraphEdge]:
        return []
