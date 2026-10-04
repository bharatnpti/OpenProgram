"""A person in no team gets their own cell, which no team's colour counts (N5).

R1-R4: Elena (exec, no pod) answered every check-in, but no pod contains her,
so she was in no program tree, no rollup reached her and her check-in showed
no colour anywhere. The user's decision: keep execs out of team colours, but
show their check-in on their own row. She now has her own developer cell, on
a "no pod" row of the heat map, and no pod, project or program reads it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest

from core.application.blocker_resolution import BlockerResolutionService
from core.application.persona_views import PersonaViewService
from core.application.rollup_service import (
    NO_POD_REASON,
    RollupService,
    is_outside_teams,
    people_outside_teams,
)
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    GraphEdge,
    GraphNode,
    NodeKind,
    Pod,
    Program,
    Project,
)
from core.domain.rollup import FactorKind, NodeStatus, Rag
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.workflows import rollup as rollup_workflow
from infra.workflows.rollup import RollupInput

TENANT = "demo"
DAY = date(2026, 10, 5)
ELENA, OMAR, ASHA = "U-elena", "U-omar", "U-asha"
_TEAMS = ("pod-platform", "project-checkout", "program-platform")

_NODES: tuple[GraphNode, ...] = (
    Program(tenant_id=TENANT, id="program-platform", name="Digital Platform Program"),
    Project(tenant_id=TENANT, id="project-checkout", name="Checkout Revamp"),
    Pod(tenant_id=TENANT, id="pod-platform", name="Platform Pod"),
    Developer(tenant_id=TENANT, id=OMAR, name="Omar Haddad"),
    Developer(tenant_id=TENANT, id=ASHA, name="Asha Rao", metadata={"app_roles": "mgr,admin"}),
    Developer(tenant_id=TENANT, id=ELENA, name="Elena Fischer", metadata={"app_roles": "exec"}),
)
_CONTAINS = (
    ("program-platform", "project-checkout"),
    ("project-checkout", "pod-platform"),
    ("pod-platform", OMAR),
    ("pod-platform", ASHA),
)


async def _org(
    *, elena: StatusSource | None, omar: StatusSource = StatusSource.CONFIRMED
) -> InMemoryGraphStore:
    """Omar and Asha in Platform; Elena, an exec, in no pod."""
    store = InMemoryGraphStore()
    for node in _NODES:
        await store.upsert_node(node)
    for parent, child in _CONTAINS:
        await store.add_edge(
            GraphEdge(
                tenant_id=TENANT, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
            )
        )
    await store.record_developer_status(_status(OMAR, omar))
    await store.record_developer_status(_status(ASHA, StatusSource.CONFIRMED))
    if elena is not None:
        await store.record_developer_status(_status(ELENA, elena))
    return store


def _status(developer_id: str, source: StatusSource) -> DeveloperStatus:
    return DeveloperStatus(
        tenant_id=TENANT,
        developer_id=developer_id,
        as_of=DAY,
        source=source,
        blockers=(),
        summary="No changes, all quiet.",
        developer_confirmed=source is StatusSource.CONFIRMED,
    )


@dataclass
class _Settings:
    rollup_backfill_days: int = 0
    jira_base_url: str | None = None
    github_base_url: str = "https://api.github.com"
    risk_default_no_pr_days: int = 3
    risk_default_pr_age_days: int = 3
    risk_default_stale_days: int = 7
    drift_no_activity_days: int = 3


@dataclass
class _Registry:
    store: InMemoryGraphStore
    settings: _Settings = field(default_factory=_Settings)

    def graph_repository(self) -> InMemoryGraphStore:
        return self.store

    def rollup_repository(self) -> InMemoryGraphStore:
        return self.store

    def status_repository(self) -> InMemoryGraphStore:
        return self.store

    def time_series_repository(self) -> InMemoryGraphStore:
        return self.store

    async def close(self) -> None:
        return None


async def _run_rollup(store: InMemoryGraphStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rollup_workflow, "_service_registry", lambda: _Registry(store))
    await rollup_workflow.run_rollup_activity(
        RollupInput(tenant_id=TENANT, as_of=DAY.isoformat(), backfill_days=0)
    )


async def _team_rags(store: InMemoryGraphStore) -> dict[str, Rag]:
    service = RollupService(store, store, BlockerResolutionService(store, store))
    tree = await store.get_program_tree(TENANT, "program-platform", DAY)
    return {
        status.entity_ref.id: status.rag
        for status in await service.compute(tree, DAY)
        if status.entity_ref.id in (*_TEAMS, OMAR, ASHA)
    }


def _ref(node_id: str) -> EntityRef:
    return EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id=node_id)


async def test_only_people_no_team_contains_are_outside_the_teams() -> None:
    store = await _org(elena=StatusSource.CONFIRMED)

    people = await people_outside_teams(store, TENANT, DAY)

    assert [person.id for person in people] == [ELENA]


@pytest.mark.parametrize(
    ("elena", "rag", "why"),
    [
        pytest.param(
            StatusSource.CONFIRMED,
            Rag.GREEN,
            "No pod, outside team colours: Confirmed status has no blockers.",
            id="confirmed",
        ),
        pytest.param(
            None,
            Rag.UNKNOWN,
            "No pod, outside team colours: No developer status data is available.",
            id="no-status",
        ),
        pytest.param(
            StatusSource.UNKNOWN,
            Rag.UNKNOWN,
            "No pod, outside team colours: Developer status is unknown.",
            id="unknown",
        ),
        pytest.param(
            StatusSource.PARTIAL,
            Rag.AMBER,
            "No pod, outside team colours: Status is partial and needs blocker or ETA "
            "confirmation.",
            id="partial",
        ),
    ],
)
async def test_elena_appears_with_her_state_and_changes_no_team_colour(
    monkeypatch: pytest.MonkeyPatch, elena: StatusSource | None, rag: Rag, why: str
) -> None:
    # Omar's update is partial, so the teams are amber whatever Elena says.
    store = await _org(elena=elena, omar=StatusSource.PARTIAL)
    teams_before = await _team_rags(store)

    await _run_rollup(store, monkeypatch)

    stored = await store.latest_node_status(TENANT, _ref(ELENA), DAY)
    assert stored is not None
    assert stored.rag is rag
    assert is_outside_teams(stored)
    assert stored.factors[-1].kind is FactorKind.NO_POD
    assert stored.factors[-1].description == NO_POD_REASON
    # No team reads her: their stored colours are what they were without her.
    for node_id, before in teams_before.items():
        node = await store.get_node(TENANT, node_id)
        assert node is not None
        recorded = await store.latest_node_status(TENANT, node.ref, DAY)
        assert recorded is not None and recorded.rag is before, node_id
        assert all(f.source_ref.id != ELENA for f in recorded.factors), node_id
    assert teams_before["program-platform"] is Rag.AMBER

    heatmap = await PersonaViewService(store, store, store, store).portfolio_heatmap(TENANT, DAY)

    cell = next(cell for cell in heatmap.cells if cell.entity_ref.id == ELENA)
    assert (cell.row, cell.rag, cell.why) == ("no pod", rag, why)
    assert "no pod" in heatmap.rows
    team_cells = {cell.entity_ref.id: cell.rag for cell in heatmap.cells if cell.row != "no pod"}
    assert {node_id: team_cells[node_id] for node_id in teams_before} == teams_before


async def test_a_green_elena_does_not_make_amber_teams_green_nor_a_silent_one_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for elena in (StatusSource.CONFIRMED, None):
        store = await _org(elena=elena, omar=StatusSource.CONFIRMED)

        await _run_rollup(store, monkeypatch)

        for node_id in _TEAMS:
            node = await store.get_node(TENANT, node_id)
            assert node is not None
            status = await store.latest_node_status(TENANT, node.ref, DAY)
            assert status is not None and status.rag is Rag.GREEN, (elena, node_id)


async def test_the_computed_heat_map_shows_elena_too() -> None:
    # Nothing stored for the day: the read computes the tree and her cell alike.
    store = await _org(elena=StatusSource.CONFIRMED)

    for program_root_id in (None, "program-platform"):
        heatmap = await PersonaViewService(store, store, store, store).portfolio_heatmap(
            TENANT, DAY, program_root_id
        )

        rows = {cell.entity_ref.id: cell.row for cell in heatmap.cells}
        assert rows[ELENA] == "no pod", program_root_id
        assert rows[OMAR] == "developer", program_root_id
    assert await store.list_node_statuses(TENANT, DAY) == []  # a read records nothing


async def test_her_own_trend_has_her_check_in(monkeypatch: pytest.MonkeyPatch) -> None:
    store = await _org(elena=StatusSource.CONFIRMED)

    await _run_rollup(store, monkeypatch)

    trend = await PersonaViewService(store, store, store, store).node_trend(
        TENANT, NodeKind.DEVELOPER, ELENA, DAY, 7
    )
    assert [(point.as_of, point.rag) for point in trend.points] == [(DAY, Rag.GREEN)]


async def test_someone_placed_in_a_pod_leaves_the_no_pod_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = await _org(elena=StatusSource.PARTIAL)
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT, from_node_id="pod-platform", to_node_id=ELENA, kind=EdgeKind.CONTAINS
        )
    )

    await _run_rollup(store, monkeypatch)

    stored = await store.latest_node_status(TENANT, _ref(ELENA), DAY)
    assert stored is not None and not is_outside_teams(stored)
    platform: NodeStatus | None = await store.latest_node_status(
        TENANT, EntityRef(tenant_id=TENANT, kind=NodeKind.POD, id="pod-platform"), DAY
    )
    assert platform is not None and platform.rag is Rag.AMBER  # now she counts for her pod
