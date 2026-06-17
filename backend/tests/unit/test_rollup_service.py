from __future__ import annotations

from datetime import date

import pytest

from core.application.persona_views import PersonaViewService
from core.application.rollup_service import RollupService
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
)
from core.domain.rollup import Rag
from core.domain.status import DeveloperStatus, StatusSource
from tests.contract.fakes import FakeRollupRepository, FakeStatusRepository


async def test_rollup_escalates_critical_blocker_and_records_node_statuses() -> None:
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

    statuses = await RollupService(
        status_repository,
        rollup_repository,
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


def _program_tree(
    *,
    critical_task: bool = False,
    include_second_developer: bool = False,
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
