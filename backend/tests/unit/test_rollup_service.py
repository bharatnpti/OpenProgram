from __future__ import annotations

from datetime import date

import pytest

from core.application.blocker_resolution import BlockerResolutionService
from core.application.persona_views import PersonaViewService
from core.application.rollup_service import RollupService
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    GraphEdge,
    GraphTree,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
    Workstream,
)
from core.domain.rollup import FactorKind, Rag
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


async def test_persona_heatmap_fallback_rollup_persists_computed_statuses() -> None:
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

    assert view.cells
    assert {status.entity_ref.id for status in rollup_repository.node_statuses} >= {
        "dev-1",
        "pod-1",
        "project-1",
        "program-1",
    }


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
