from __future__ import annotations

from datetime import date

import pytest

from core.application.blocker_resolution import BlockerResolutionService
from core.application.persona_views import PersonaViewService
from core.application.rollup_service import RollupService, task_health, task_rag
from core.application.status_summaries import NO_REPLY_BLOCKER
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    GraphEdge,
    GraphNode,
    GraphTree,
    JsonScalar,
    NodeKind,
    Pod,
    Program,
    Project,
    RepoNode,
    SprintNode,
    Task,
    WorkItem,
    Workstream,
)
from core.domain.rollup import FactorKind, NodeStatus, Rag
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeRollupRepository, FakeStatusRepository


async def test_rollup_escalates_critical_blocker_and_records_node_statuses() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(critical_task=True)
    store = await _store_for_tree(tree)
    rollup_repository = FakeRollupRepository()
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("schema review",),
            summary="Blocked on schema review.",
        ),
        (_blocker("schema review", work_item_id="task-1", as_of=as_of),),
    )

    statuses = await RollupService(
        store,
        rollup_repository,
        BlockerResolutionService(store, store),
    ).compute_and_record(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["dev-1"].rag is Rag.RED
    assert by_id["pod-1"].rag is Rag.RED
    assert by_id["project-1"].rag is Rag.RED
    assert by_id["program-1"].rag is Rag.RED
    assert any(
        factor.source_ref == EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-1")
        for factor in by_id["program-1"].factors
    )
    assert set(by_id.values()) == set(rollup_repository.node_statuses)


async def test_rollup_keeps_stale_and_missing_statuses_out_of_green() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(include_second_developer=True)
    status_repository = FakeStatusRepository()
    await status_repository.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.STALE,
            blockers=(),
            summary="No reply yet.",
        )
    )

    statuses = await RollupService(status_repository).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["dev-1"].rag is Rag.AMBER
    assert by_id["dev-2"].rag is Rag.UNKNOWN
    assert by_id["program-1"].rag is not Rag.GREEN


async def test_rollup_treats_partial_status_as_amber() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree()
    status_repository = FakeStatusRepository()
    await status_repository.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.PARTIAL,
            blockers=(),
            summary="Progress shared. ETA was not provided.",
        )
    )

    statuses = await RollupService(status_repository).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["dev-1"].rag is Rag.AMBER
    assert by_id["dev-1"].source is StatusSource.PARTIAL
    assert by_id["dev-1"].factors[0].description == (
        "Status is partial and needs blocker or ETA confirmation."
    )
    assert by_id["program-1"].source is StatusSource.PARTIAL


async def test_persona_heatmap_fallback_rollup_computes_without_persisting() -> None:
    """A read shows heat for a day with no stored rollup, and stores nothing.

    This deliberately inverts the previous contract. Persisting from the read
    let any caller write a row of derived history for whatever `as_of` it asked
    about -- one /ask question about a date the model had invented left a rollup
    behind -- and made stored history depend on who opened which screen. The
    scheduled rollup (`connector="rollup"`) owns that write now.
    """
    as_of = date(2026, 1, 10)
    tree = _program_tree(critical_task=True)
    status_repository = FakeStatusRepository()
    rollup_repository = FakeRollupRepository()
    await status_repository.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("schema review",),
            summary="Blocked on schema review.",
        )
    )
    service = PersonaViewService(
        graph_repository=_GraphRepository(tree),
        status_repository=status_repository,
        rollup_repository=rollup_repository,
        time_series_repository=_UnusedTimeSeriesRepository(),
    )

    view = await service.portfolio_heatmap("demo", as_of, "program-1")

    # Heat is still shown for the day...
    assert {cell.entity_ref.id for cell in view.cells} >= {
        "dev-1",
        "pod-1",
        "project-1",
        "program-1",
    }
    # ...but nothing was written to get it there.
    assert rollup_repository.node_statuses == []


async def test_persona_heatmap_only_swallows_missing_graph() -> None:
    service = PersonaViewService(
        graph_repository=_BrokenGraphRepository(RuntimeError("database unavailable")),
        status_repository=FakeStatusRepository(),
        rollup_repository=FakeRollupRepository(),
        time_series_repository=_UnusedTimeSeriesRepository(),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.portfolio_heatmap("demo", date(2026, 1, 10), "program-1")

    missing_service = PersonaViewService(
        graph_repository=_BrokenGraphRepository(GraphNotFound("missing graph")),
        status_repository=FakeStatusRepository(),
        rollup_repository=FakeRollupRepository(),
        time_series_repository=_UnusedTimeSeriesRepository(),
    )

    view = await missing_service.portfolio_heatmap("demo", date(2026, 1, 10), "program-1")

    assert view.cells == ()


async def test_rollup_task_root_has_no_node_status() -> None:
    task = Task(tenant_id="demo", id="task-root", name="Task root")
    tree = GraphTree(root=task, nodes=(task,), edges=())

    statuses = await RollupService(FakeStatusRepository()).compute(tree, date(2026, 1, 10))

    assert statuses == ()


async def test_rollup_without_children_records_unknown_factor() -> None:
    program = Program(tenant_id="demo", id="program-empty", name="Program")
    tree = GraphTree(
        root=program,
        nodes=(program,),
        edges=(
            GraphEdge(
                tenant_id="demo",
                from_node_id=program.id,
                to_node_id="missing-child",
                kind=EdgeKind.CONTAINS,
            ),
        ),
    )

    statuses = await RollupService(FakeStatusRepository()).compute(tree, date(2026, 1, 10))

    assert len(statuses) == 1
    assert statuses[0].entity_ref.id == "program-empty"
    assert statuses[0].rag is Rag.UNKNOWN
    assert statuses[0].factors[0].description == "No child status data is available."


async def test_rollup_all_green_children_stay_green() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree()
    status_repository = FakeStatusRepository()
    await status_repository.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Ready.",
        )
    )

    statuses = await RollupService(status_repository).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["dev-1"].rag is Rag.GREEN
    assert by_id["pod-1"].rag is Rag.GREEN
    assert by_id["program-1"].source is StatusSource.CONFIRMED
    assert by_id["program-1"].factors[0].description == (
        "All child statuses are confirmed with no blockers."
    )


async def test_rollup_inferred_status_and_multiple_blockers_escalate() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(include_second_developer=True)
    status_repository = FakeStatusRepository()
    await status_repository.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.INFERRED,
            blockers=(),
            summary="Inferred from activity.",
        )
    )
    await status_repository.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-2",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("schema review", "staging access"),
            summary="Blocked.",
        )
    )

    statuses = await RollupService(status_repository).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["dev-1"].rag is Rag.AMBER
    assert by_id["dev-1"].factors[0].description == ("Status is inferred and needs confirmation.")
    assert by_id["dev-2"].rag is Rag.RED
    assert [factor.description for factor in by_id["dev-2"].factors] == [
        "Blocker: schema review",
        "Blocker: staging access",
    ]
    assert by_id["program-1"].rag is Rag.RED


async def test_rollup_edge_level_critical_metadata_escalates_single_blocker() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(critical_edge=True)
    store = await _store_for_tree(tree)
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("API contract",),
            summary="Blocked.",
        ),
        (_blocker("API contract", work_item_id="task-1", as_of=as_of),),
    )

    statuses = await RollupService(
        store, blocker_resolution=BlockerResolutionService(store, store)
    ).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["dev-1"].rag is Rag.RED
    assert by_id["dev-1"].factors[0].source_ref == EntityRef(
        tenant_id="demo",
        kind=NodeKind.TASK,
        id="task-1",
    )


async def test_workstream_rollup_aggregates_child_task_statuses() -> None:
    as_of = date(2026, 1, 10)
    workstream = Workstream(
        tenant_id="demo",
        id="workstream-1",
        name="Runtime Config Admin",
        metadata={"target_date": "2026-01-15"},
    )
    green_task = Task(
        tenant_id="demo",
        id="task-green",
        name="Ready task",
        metadata={"status": "done"},
    )
    blocked_task = Task(
        tenant_id="demo",
        id="task-blocked",
        name="Blocked task",
        metadata={"status": "blocked"},
    )
    tree = GraphTree(
        root=workstream,
        nodes=(workstream, green_task, blocked_task),
        edges=(
            GraphEdge(
                tenant_id="demo",
                from_node_id=workstream.id,
                to_node_id=green_task.id,
                kind=EdgeKind.CONTAINS,
            ),
            GraphEdge(
                tenant_id="demo",
                from_node_id=workstream.id,
                to_node_id=blocked_task.id,
                kind=EdgeKind.CONTAINS,
            ),
        ),
    )

    statuses = await RollupService(FakeStatusRepository()).compute(tree, as_of)

    assert [status.entity_ref.id for status in statuses] == [workstream.id]
    assert statuses[0].rag is Rag.RED
    assert any("blocked" in factor.description.lower() for factor in statuses[0].factors)


async def test_blocker_attributed_to_pod_a_leaves_pod_b_green() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(include_second_pod=True)
    store = await _store_for_tree(tree)
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("infra access",),
            summary="Blocked on infra access.",
        ),
        (_blocker("infra access", pod_id="pod-1", as_of=as_of),),
    )

    statuses = await _resolving_rollup(store).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["dev-1"].rag is Rag.AMBER
    assert by_id["pod-1"].rag is Rag.AMBER
    assert by_id["pod-2"].rag is Rag.GREEN
    assert not any(factor.kind is FactorKind.BLOCKER for factor in by_id["pod-2"].factors)


async def test_unattributed_blocker_marks_both_pods_amber_and_flagged() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(include_second_pod=True)
    store = await _store_for_tree(tree)
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("mystery dependency",),
            summary="Blocked.",
        ),
        (_blocker("mystery dependency", as_of=as_of),),
    )

    statuses = await _resolving_rollup(store).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    for pod_id in ("pod-1", "pod-2"):
        assert by_id[pod_id].rag is Rag.AMBER
        blocker_factors = [
            factor for factor in by_id[pod_id].factors if factor.kind is FactorKind.BLOCKER
        ]
        assert len(blocker_factors) == 1
        assert blocker_factors[0].unattributed is True


async def test_two_blockers_split_across_pods_keep_each_pod_amber_and_developer_red() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(include_second_pod=True)
    store = await _store_for_tree(tree)
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("first snag", "second snag"),
            summary="Blocked twice.",
        ),
        (
            _blocker("first snag", pod_id="pod-1", as_of=as_of),
            _blocker("second snag", pod_id="pod-2", as_of=as_of),
        ),
    )

    statuses = await _resolving_rollup(store).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    # The person is globally red (two distinct open blockers) while each pod
    # only sees -- and is only amberised by -- its own blocker.
    assert by_id["dev-1"].rag is Rag.RED
    assert by_id["pod-1"].rag is Rag.AMBER
    assert by_id["pod-2"].rag is Rag.AMBER


async def test_program_counts_multipod_unattributed_blocker_once() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(include_second_pod=True)
    store = await _store_for_tree(tree)
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("mystery dependency",),
            summary="Blocked.",
        ),
        (_blocker("mystery dependency", as_of=as_of),),
    )

    statuses = await _resolving_rollup(store).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["program-1"].rag is Rag.AMBER
    program_blocker_factors = [
        factor for factor in by_id["program-1"].factors if factor.kind is FactorKind.BLOCKER
    ]
    assert len(program_blocker_factors) == 1


async def test_project_not_containing_pod_never_sees_pod_scoped_blocker() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(second_project=True)
    store = await _store_for_tree(tree)
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("second pod snag",),
            summary="Blocked in the second pod.",
        ),
        (_blocker("second pod snag", pod_id="pod-2", as_of=as_of),),
    )

    statuses = await _resolving_rollup(store).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["project-1"].rag is Rag.GREEN
    assert not any("second pod snag" in factor.description for factor in by_id["project-1"].factors)
    assert by_id["project-2"].rag is Rag.AMBER


async def test_aggregate_rag_ignores_blocker_word_in_status_descriptions() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(include_second_developer=True)
    store = await _store_for_tree(tree)
    # dev-1's PARTIAL factor text mentions the word "blocker" but is a STATUS
    # factor; only dev-2 has one real blocker, so nothing may escalate to red.
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.PARTIAL,
            blockers=(),
            summary="Progress shared. ETA was not provided.",
        )
    )
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-2",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("staging access",),
            summary="Blocked.",
        ),
        (_blocker("staging access", developer_id="dev-2", as_of=as_of),),
    )

    statuses = await _resolving_rollup(store).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert any("blocker" in factor.description for factor in by_id["dev-1"].factors)
    assert by_id["program-1"].rag is Rag.AMBER


async def test_legacy_status_strings_resolve_as_unattributed_fallback() -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(include_second_pod=True)
    store = await _store_for_tree(tree)
    # No lifecycle rows at all: the flat status strings are synthesized as
    # unattributed pseudo-blockers and surface in every pod of the developer.
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("waiting on schema",),
            summary="Blocked on schema.",
        )
    )

    statuses = await _resolving_rollup(store).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    for pod_id in ("pod-1", "pod-2"):
        assert by_id[pod_id].rag is Rag.AMBER
        blocker_factors = [
            factor for factor in by_id[pod_id].factors if factor.kind is FactorKind.BLOCKER
        ]
        assert len(blocker_factors) == 1
        assert blocker_factors[0].unattributed is True


# A tree synced from Jira and git: a project holds repos, sprints, workstreams,
# pods and tickets. Only people, and a blocked or at-risk ticket, say how it is
# going; the rest is neutral -- it must neither hold the project unknown nor
# make it green.


async def test_project_is_green_when_its_people_report_beside_repos_sprints_and_tickets() -> None:
    as_of = date(2026, 1, 10)
    program, project, pod = _program_project_pod()
    repo = RepoNode(tenant_id="demo", id="repo-1", name="checkout-api")
    sprint = SprintNode(tenant_id="demo", id="sprint-1", name="Sprint 1")
    workstream = Workstream(tenant_id="demo", id="ws-1", name="Payments")
    tree = _contains_tree(
        program,
        (program, project),
        (project, pod),
        (pod, _developer("dev-1")),
        (pod, _developer("dev-2")),
        (pod, repo),
        (pod, _ticket("QA-1", "In Progress", "in_progress")),
        (pod, _ticket("QA-2", "To Do", "todo")),
        # The same repo under the project too: a neutral node reached twice.
        (project, repo),
        (project, sprint),
        (sprint, _ticket("QA-3", "In Progress", "in_progress")),
        (sprint, _ticket("QA-4", "Done", "done")),
        (project, workstream),
        (workstream, _ticket("QA-5", "To Do", "todo")),
        (project, _ticket("QA-6", "To Do", "todo")),
    )

    statuses = await RollupService(await _reported("dev-1", "dev-2", as_of=as_of)).compute(
        tree, as_of
    )
    by_id = {status.entity_ref.id: status for status in statuses}

    for node_id in ("pod-1", "project-1", "program-1"):
        assert by_id[node_id].rag is Rag.GREEN, node_id
        assert by_id[node_id].source is StatusSource.CONFIRMED, node_id
        assert _cited(by_id[node_id]) == {node_id}, node_id
    # A neutral node keeps a status of its own: nothing about it has reported.
    for node_id in ("repo-1", "sprint-1", "ws-1"):
        assert by_id[node_id].rag is Rag.UNKNOWN, node_id


async def test_a_member_with_no_status_keeps_the_pod_unknown_and_is_its_only_reason() -> None:
    as_of = date(2026, 1, 10)
    program, project, pod = _program_project_pod()
    tree = _contains_tree(
        program,
        (program, project),
        (project, pod),
        (pod, _developer("dev-1")),
        (pod, _developer("dev-2")),
        (pod, RepoNode(tenant_id="demo", id="repo-1", name="checkout-api")),
        (pod, _ticket("QA-1", "In Progress", "in_progress")),
        (project, SprintNode(tenant_id="demo", id="sprint-1", name="Sprint 1")),
    )

    statuses = await RollupService(await _reported("dev-1", as_of=as_of)).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    # Silence is never green, and the reason names the person, not the repo,
    # the sprint or the ticket.
    for node_id in ("pod-1", "project-1", "program-1"):
        assert by_id[node_id].rag is Rag.UNKNOWN, node_id
        assert by_id[node_id].source is StatusSource.UNKNOWN, node_id
        assert _cited(by_id[node_id]) == {"dev-2"}, node_id


@pytest.mark.parametrize(
    ("status", "state"),
    [("Blocked", "blocked"), ("On Hold", "blocked"), ("blocked", None)],
)
async def test_a_blocked_ticket_turns_its_sprint_pod_and_project_red(
    status: str, state: str | None
) -> None:
    as_of = date(2026, 1, 10)
    program, project, pod = _program_project_pod()
    sprint = SprintNode(tenant_id="demo", id="sprint-1", name="Sprint 1")
    tree = _contains_tree(
        program,
        (program, project),
        (project, pod),
        (pod, _developer("dev-1")),
        (pod, sprint),
        (sprint, _ticket("QA-1", "In Progress", "in_progress")),
        (sprint, _ticket("QA-9", status, state)),
    )

    statuses = await RollupService(await _reported("dev-1", as_of=as_of)).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    for node_id in ("sprint-1", "pod-1", "project-1", "program-1"):
        assert by_id[node_id].rag is Rag.RED, node_id
        assert _cited(by_id[node_id]) == {"QA-9"}, node_id
    assert by_id["pod-1"].factors[0].description == "Task Ticket QA-9 is blocked."


async def test_a_project_with_only_neutral_children_stays_unknown_never_green() -> None:
    as_of = date(2026, 1, 10)
    program, project, pod = _program_project_pod()
    sprint = SprintNode(tenant_id="demo", id="sprint-1", name="Sprint 1")
    workstream = Workstream(tenant_id="demo", id="ws-1", name="Payments")
    tree = _contains_tree(
        program,
        (program, project),
        # A pod nobody has been added to yet has nobody to hear from.
        (project, pod),
        (project, RepoNode(tenant_id="demo", id="repo-1", name="checkout-api")),
        (project, sprint),
        (sprint, _ticket("QA-1", "In Progress", "in_progress")),
        (sprint, _ticket("QA-2", "Done", "done")),
        (project, workstream),
        (workstream, WorkItem(tenant_id="demo", id="WI-1", name="Refunds")),
        (project, _ticket("QA-3", "To Do", "todo")),
    )

    statuses = await RollupService(FakeStatusRepository()).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    for node_id in ("project-1", "program-1"):
        assert by_id[node_id].rag is Rag.UNKNOWN, node_id
        assert by_id[node_id].source is StatusSource.UNKNOWN, node_id
        assert [
            (factor.description, factor.source_ref.id) for factor in by_id[node_id].factors
        ] == [("No child status data is available.", node_id)]


async def test_a_seeded_task_rag_still_counts_but_a_done_ticket_reports_nothing() -> None:
    # workstream -> work item -> task with an explicit RAG is how the demo seeds
    # its delivery tree; a done ticket is finished work, not a health report.
    as_of = date(2026, 1, 10)
    project = Project(tenant_id="demo", id="project-1", name="Project")
    seeded = Workstream(tenant_id="demo", id="ws-seeded", name="Seeded")
    finished = Workstream(tenant_id="demo", id="ws-finished", name="Finished")
    work_item = WorkItem(tenant_id="demo", id="WI-1", name="Refunds")
    green_task = Task(
        tenant_id="demo",
        id="task-green",
        name="Green task",
        metadata={"status": "green", "source": "confirmed"},
    )
    tree = _contains_tree(
        project,
        (project, seeded),
        (seeded, work_item),
        (work_item, green_task),
        (project, finished),
        (finished, _ticket("QA-1", "Done", "done")),
    )

    statuses = await RollupService(FakeStatusRepository()).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["WI-1"].rag is Rag.GREEN
    assert by_id["ws-seeded"].rag is Rag.GREEN
    assert by_id["ws-finished"].rag is Rag.UNKNOWN
    assert by_id["project-1"].rag is Rag.GREEN
    assert by_id["project-1"].source is StatusSource.CONFIRMED


@pytest.mark.parametrize(
    ("metadata", "health", "rag"),
    [
        ({"status": "To Do", "state": "todo"}, None, None),
        ({"status": "In Progress", "state": "in_progress"}, None, None),
        ({"status": "Done", "state": "done"}, None, Rag.GREEN),
        ({"status": "Closed", "state": "done"}, None, Rag.GREEN),
        ({"status": "Blocked", "state": "blocked"}, Rag.RED, Rag.RED),
        ({"status": "On Hold", "state": "blocked"}, Rag.RED, Rag.RED),
        ({"status": "at-risk"}, Rag.AMBER, Rag.AMBER),
        ({"status": "green", "source": "confirmed"}, Rag.GREEN, Rag.GREEN),
        ({"status": "unknown"}, None, Rag.UNKNOWN),
        ({}, None, None),
    ],
)
def test_task_health_for_its_parent_and_its_own_colour(
    metadata: dict[str, JsonScalar], health: Rag | None, rag: Rag | None
) -> None:
    assert task_health(metadata) is health
    assert task_rag(metadata) is rag


def _program_project_pod() -> tuple[Program, Project, Pod]:
    return (
        Program(tenant_id="demo", id="program-1", name="Program"),
        Project(tenant_id="demo", id="project-1", name="Project"),
        Pod(tenant_id="demo", id="pod-1", name="Pod"),
    )


def _developer(developer_id: str) -> Developer:
    return Developer(tenant_id="demo", id=developer_id, name=developer_id)


def _ticket(key: str, status: str, state: str | None) -> Task:
    """A task as the Jira sync writes it: the tracker's status name and its state."""
    metadata: dict[str, JsonScalar] = {"key": key, "status": status}
    if state is not None:
        metadata["state"] = state
    return Task(tenant_id="demo", id=key, name=f"Ticket {key}", metadata=metadata)


def _contains_tree(root: GraphNode, *links: tuple[GraphNode, GraphNode]) -> GraphTree:
    """A tree of `contains` links, each given as (parent, child)."""
    nodes: dict[str, GraphNode] = {root.id: root}
    for parent, child in links:
        nodes.setdefault(parent.id, parent)
        nodes.setdefault(child.id, child)
    return GraphTree(
        root=root,
        nodes=tuple(nodes.values()),
        edges=tuple(
            GraphEdge(
                tenant_id="demo",
                from_node_id=parent.id,
                to_node_id=child.id,
                kind=EdgeKind.CONTAINS,
            )
            for parent, child in links
        ),
    )


async def _reported(*developer_ids: str, as_of: date) -> FakeStatusRepository:
    """Each of these developers confirmed a status with no blockers on `as_of`."""
    repository = FakeStatusRepository()
    for developer_id in developer_ids:
        await repository.record_developer_status(
            DeveloperStatus(
                tenant_id="demo",
                developer_id=developer_id,
                as_of=as_of,
                source=StatusSource.CONFIRMED,
                blockers=(),
                summary="On track.",
            )
        )
    return repository


def _cited(status: NodeStatus) -> set[str]:
    return {factor.source_ref.id for factor in status.factors}


async def test_a_silent_member_beside_one_real_blocker_is_one_blocker_not_red() -> None:
    # A non-response status carries a placeholder blocker string. The resolver
    # drops it; the rollup fell back to the flat strings and counted it, so a
    # silent member beside one real blocker made two blockers -- and red.
    as_of = date(2026, 1, 10)
    program, project, pod = _program_project_pod()
    tree = _contains_tree(
        program,
        (program, project),
        (project, pod),
        (pod, _developer("dev-1")),
        (pod, _developer("dev-2")),
        (pod, _ticket("PAY-8", "In Progress", "in_progress")),
    )
    store = await _store_for_tree(tree)
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("waiting on review",),
            summary="Waiting on review.",
        ),
        (_blocker("waiting on review", work_item_id="PAY-8", as_of=as_of),),
    )
    await store.record_developer_status(_silent_status("dev-2", StatusSource.INFERRED, as_of))

    statuses = await _resolving_rollup(store).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["dev-2"].rag is Rag.AMBER
    assert [(factor.kind, factor.description) for factor in by_id["dev-2"].factors] == [
        (FactorKind.STATUS, "Status is inferred and needs confirmation.")
    ]
    for node_id in ("pod-1", "project-1", "program-1"):
        blockers = [f for f in by_id[node_id].factors if f.kind is FactorKind.BLOCKER]
        assert [factor.description for factor in blockers] == ["Blocker: waiting on review"]
        # One blocker beside an inferred member is amber; two would be red.
        assert by_id[node_id].rag is Rag.AMBER, node_id
        assert not any(NO_REPLY_BLOCKER in f.description for f in by_id[node_id].factors)


@pytest.mark.parametrize(
    ("source", "rag"),
    [
        (StatusSource.INFERRED, Rag.AMBER),
        (StatusSource.STALE, Rag.AMBER),
        (StatusSource.UNKNOWN, Rag.UNKNOWN),
    ],
)
async def test_without_a_resolver_silence_is_a_status_and_real_strings_still_block(
    source: StatusSource, rag: Rag
) -> None:
    as_of = date(2026, 1, 10)
    tree = _program_tree(include_second_developer=True)
    status_repository = FakeStatusRepository()
    await status_repository.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of,
            source=StatusSource.CONFIRMED,
            blockers=("schema review",),
            summary="Blocked on schema review.",
        )
    )
    await status_repository.record_developer_status(_silent_status("dev-2", source, as_of))

    statuses = await RollupService(status_repository).compute(tree, as_of)
    by_id = {status.entity_ref.id: status for status in statuses}

    assert by_id["dev-2"].rag is rag
    assert all(factor.kind is FactorKind.STATUS for factor in by_id["dev-2"].factors)
    assert [factor.description for factor in by_id["dev-1"].factors] == ["Blocker: schema review"]
    program_blockers = [
        factor for factor in by_id["program-1"].factors if factor.kind is FactorKind.BLOCKER
    ]
    assert [factor.description for factor in program_blockers] == ["Blocker: schema review"]
    # One real blocker: amber, whatever colour the silent member's status is.
    assert by_id["program-1"].rag is Rag.AMBER


def _silent_status(developer_id: str, source: StatusSource, as_of: date) -> DeveloperStatus:
    """What the collector records when someone never answers the check-in."""
    return DeveloperStatus(
        tenant_id="demo",
        developer_id=developer_id,
        as_of=as_of,
        source=source,
        blockers=(NO_REPLY_BLOCKER,),
        summary="No confirmed check-in after a nudge.",
    )


def _resolving_rollup(store: InMemoryGraphStore) -> RollupService:
    return RollupService(store, blocker_resolution=BlockerResolutionService(store, store))


async def _store_for_tree(tree: GraphTree) -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for node in tree.nodes:
        await store.upsert_node(node)
    for edge in tree.edges:
        await store.add_edge(edge)
    return store


def _blocker(
    description: str,
    *,
    as_of: date,
    developer_id: str = "dev-1",
    work_item_id: str | None = None,
    pod_id: str | None = None,
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
        last_seen_on=as_of,
    )


def _program_tree(
    *,
    critical_task: bool = False,
    critical_edge: bool = False,
    include_second_developer: bool = False,
    include_second_pod: bool = False,
    second_project: bool = False,
) -> GraphTree:
    program = Program(tenant_id="demo", id="program-1", name="Program")
    project = Project(tenant_id="demo", id="project-1", name="Project")
    pod = Pod(tenant_id="demo", id="pod-1", name="Pod")
    developer = Developer(tenant_id="demo", id="dev-1", name="Asha")
    task = Task(
        tenant_id="demo",
        id="task-1",
        name="Critical task",
        metadata={"critical_path": critical_task},
    )
    nodes = [program, project, pod, developer, task]
    edges = [
        GraphEdge(
            tenant_id="demo",
            from_node_id="program-1",
            to_node_id="project-1",
            kind=EdgeKind.CONTAINS,
        ),
        GraphEdge(
            tenant_id="demo",
            from_node_id="project-1",
            to_node_id="pod-1",
            kind=EdgeKind.CONTAINS,
        ),
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-1",
            to_node_id="dev-1",
            kind=EdgeKind.CONTAINS,
        ),
        GraphEdge(
            tenant_id="demo",
            from_node_id="dev-1",
            to_node_id="task-1",
            kind=EdgeKind.ASSIGNED_TO,
            metadata={"critical_path": "true"} if critical_edge else {},
        ),
    ]
    if include_second_developer:
        nodes.append(Developer(tenant_id="demo", id="dev-2", name="Liam"))
        edges.append(
            GraphEdge(
                tenant_id="demo",
                from_node_id="pod-1",
                to_node_id="dev-2",
                kind=EdgeKind.CONTAINS,
            )
        )
    if include_second_pod or second_project:
        # pod-2 also contains dev-1: the multi-pod membership under test.
        nodes.append(Pod(tenant_id="demo", id="pod-2", name="Second Pod"))
        pod_2_parent_id = "project-1"
        if second_project:
            nodes.append(Project(tenant_id="demo", id="project-2", name="Second Project"))
            edges.append(
                GraphEdge(
                    tenant_id="demo",
                    from_node_id="program-1",
                    to_node_id="project-2",
                    kind=EdgeKind.CONTAINS,
                )
            )
            pod_2_parent_id = "project-2"
        edges.append(
            GraphEdge(
                tenant_id="demo",
                from_node_id=pod_2_parent_id,
                to_node_id="pod-2",
                kind=EdgeKind.CONTAINS,
            )
        )
        edges.append(
            GraphEdge(
                tenant_id="demo",
                from_node_id="pod-2",
                to_node_id="dev-1",
                kind=EdgeKind.CONTAINS,
            )
        )
    return GraphTree(root=program, nodes=tuple(nodes), edges=tuple(edges))


class _GraphRepository:
    def __init__(self, tree: GraphTree) -> None:
        self._tree = tree

    async def get_program_tree(
        self,
        tenant_id: str,
        root_id: str,
        as_of: date,
    ) -> GraphTree:
        assert tenant_id == "demo"
        assert root_id == self._tree.root.id
        return self._tree

    # The heat map's computed path also reads who is in no team (N5).
    async def list_nodes(
        self, tenant_id: str, kind: NodeKind | None = None, *, as_of: date | None = None
    ) -> list[GraphNode]:
        return [node for node in self._tree.nodes if kind is None or node.kind is kind]

    async def list_edges(self, tenant_id: str, *, kind: EdgeKind | None = None) -> list[GraphEdge]:
        return [edge for edge in self._tree.edges if kind is None or edge.kind is kind]

    async def pods_containing_developer(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> list[Pod]:
        return []

    async def pods_for_task(self, tenant_id: str, task_id: str, as_of: date) -> list[Pod]:
        return []


class _BrokenGraphRepository:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def get_program_tree(
        self,
        tenant_id: str,
        root_id: str,
        as_of: date,
    ) -> GraphTree:
        raise self._error


class _UnusedTimeSeriesRepository:
    pass
