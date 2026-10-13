"""The small-group rule (D99, D104): an executive gets no people counts on a group under five.

A pod of two is effectively two people, so "1 of 2 haven't answered" on its tile
names someone to a reader who reads no one's check-in. For an executive, such a
pod's people roll into its project's line; its colour, its work and the dates
still show. A project or program under five is the same (D104): its people are
counted in the program's line, or nowhere. A group of five or more keeps its
counts, and a manager keeps all of them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.people_words import (
    SMALL_GROUP_ALONE_LINE,
    SMALL_GROUP_LINE,
    SMALL_GROUP_PROGRAM_LINE,
)
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    GraphEdge,
    NodeKind,
    Pod,
    Program,
    Project,
)
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from core.domain.status import CheckIn, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry

T = "demo"
DAY = date(2026, 6, 15)
SMALL = ("dev-a1", "dev-a2")
BIG = ("dev-b1", "dev-b2", "dev-b3", "dev-b4", "dev-b5")
#: Project 2 is one pod of three: a small project in a program of ten (D104).
TINY = ("dev-c1", "dev-c2", "dev-c3")


def _ref(kind: NodeKind, node_id: str) -> EntityRef:
    return EntityRef(tenant_id=T, kind=kind, id=node_id)


def _unknown(person: str) -> RollupFactor:
    return RollupFactor(
        description="Developer status is unknown.",
        contributes=Rag.UNKNOWN,
        source_ref=_ref(NodeKind.DEVELOPER, person),
        kind=FactorKind.STATUS,
    )


def _status(kind: NodeKind, node_id: str, rag: Rag, *factors: RollupFactor) -> NodeStatus:
    source = StatusSource.UNKNOWN if rag is Rag.UNKNOWN else StatusSource.CONFIRMED
    return NodeStatus(
        entity_ref=_ref(kind, node_id), rag=rag, source=source, factors=factors, as_of=DAY
    )


def _seed(store: InMemoryGraphStore) -> None:
    """Project 1 with a pod of two (one silent) and a pod of five (one silent);
    project 2 with a pod of three (one silent)."""

    async def seed() -> None:
        nodes = [
            Program(tenant_id=T, id="program-1", name="Core Program"),
            Project(tenant_id=T, id="project-1", name="Checkout"),
            Project(tenant_id=T, id="project-2", name="Insights"),
            Pod(tenant_id=T, id="pod-small", name="Small Pod"),
            Pod(tenant_id=T, id="pod-big", name="Big Pod"),
            Pod(tenant_id=T, id="pod-tiny", name="Tiny Pod"),
            *(
                Developer(tenant_id=T, id=person, name=f"Person {person}")
                for person in (*SMALL, *BIG, *TINY)
            ),
        ]
        for node in nodes:
            await store.upsert_node(node)
        links = [
            ("program-1", "project-1"),
            ("program-1", "project-2"),
            ("project-1", "pod-small"),
            ("project-1", "pod-big"),
            ("project-2", "pod-tiny"),
            *(("pod-small", person) for person in SMALL),
            *(("pod-big", person) for person in BIG),
            *(("pod-tiny", person) for person in TINY),
        ]
        for parent, child in links:
            await store.add_edge(
                GraphEdge(
                    tenant_id=T, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
                )
            )
        silent = ("dev-a2", "dev-b5", "dev-c3")
        statuses = [
            *(
                _status(NodeKind.DEVELOPER, person, Rag.UNKNOWN, _unknown(person))
                if person in silent
                else _status(NodeKind.DEVELOPER, person, Rag.GREEN)
                for person in (*SMALL, *BIG, *TINY)
            ),
            _status(NodeKind.POD, "pod-small", Rag.AMBER, _unknown("dev-a2")),
            _status(NodeKind.POD, "pod-big", Rag.AMBER, _unknown("dev-b5")),
            _status(NodeKind.POD, "pod-tiny", Rag.AMBER, _unknown("dev-c3")),
            _status(
                NodeKind.PROJECT, "project-1", Rag.AMBER, _unknown("dev-a2"), _unknown("dev-b5")
            ),
            _status(NodeKind.PROJECT, "project-2", Rag.AMBER, _unknown("dev-c3")),
            _status(
                NodeKind.PROGRAM,
                "program-1",
                Rag.AMBER,
                _unknown("dev-a2"),
                _unknown("dev-b5"),
                _unknown("dev-c3"),
            ),
        ]
        for status in statuses:
            await store.record_node_status(status)
        for person in silent:
            await store.record_checkin(
                CheckIn(
                    tenant_id=T,
                    developer_id=person,
                    correlation_id=f"checkin-{person}",
                    asked_at=datetime(2026, 6, 15, 9, 0, tzinfo=UTC),
                    replied_at=None,
                    raw_reply=None,
                    signals=None,
                )
            )

    asyncio.run(seed())


def _get(settings: Settings, store: InMemoryGraphStore, roles: str, path: str) -> dict:
    role_settings = settings.model_copy(
        update={"dev_principal_roles": roles, "dev_principal_subject": "reader"}
    )
    app = create_app(
        settings=role_settings, registry=ServiceRegistry(role_settings, graph_store=store)
    )
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _cells(settings: Settings, store: InMemoryGraphStore, roles: str) -> dict[str, dict]:
    path = f"/portfolio/heatmap?as_of={DAY.isoformat()}&program_root_id=program-1"
    return {cell["entity_ref"]["id"]: cell for cell in _get(settings, store, roles, path)["cells"]}


def _said(cell: dict) -> str:
    return " ".join([cell["reason"], cell["why"], *cell["reasons"]])


def test_an_executive_reads_no_people_counts_on_a_pod_under_five(settings: Settings) -> None:
    store = InMemoryGraphStore()
    _seed(store)

    cells = _cells(settings, store, "exec")

    small = cells["pod-small"]
    # Its colour stays; its people are counted in the project's line instead.
    assert small["rag"] == "amber"
    assert "of 2" not in _said(small) and "missing update" not in _said(small)
    assert SMALL_GROUP_LINE in small["reasons"]
    assert small["why"] == SMALL_GROUP_LINE
    # A pod of five keeps its counts.
    assert "1 of 5" in _said(cells["pod-big"])
    # The project counts everyone, the small pod's people too.
    assert "2 of 7" in _said(cells["project-1"]), _said(cells["project-1"])


def test_an_executive_reads_no_people_counts_on_a_project_under_five(settings: Settings) -> None:
    """D104: a project of three, and its pod, are counted with the program."""
    store = InMemoryGraphStore()
    _seed(store)

    cells = _cells(settings, store, "exec")

    for group in ("project-2", "pod-tiny"):
        said = _said(cells[group])
        assert cells[group]["rag"] == "amber", group
        assert "of 3" not in said and "missing update" not in said, (group, said)
        assert SMALL_GROUP_PROGRAM_LINE in cells[group]["reasons"], group
    # The program counts all ten, the small project's people too.
    assert "3 of 10" in _said(cells["program-1"]), _said(cells["program-1"])
    # A manager keeps the small project's counts.
    managers = _cells(settings, store, "mgr")
    assert "1 of 3" in _said(managers["project-2"])


def test_a_program_under_five_counts_nobody_for_an_executive(settings: Settings) -> None:
    """D104: no group above a program of two, so its line says no counts are shown."""
    store = InMemoryGraphStore()

    async def seed() -> None:
        for node in (
            Program(tenant_id=T, id="program-1", name="Core Program"),
            Pod(tenant_id=T, id="pod-small", name="Small Pod"),
            *(Developer(tenant_id=T, id=person, name=f"Person {person}") for person in SMALL),
        ):
            await store.upsert_node(node)
        for parent, child in (
            ("program-1", "pod-small"),
            *(("pod-small", person) for person in SMALL),
        ):
            await store.add_edge(
                GraphEdge(
                    tenant_id=T, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
                )
            )
        for status in (
            _status(NodeKind.DEVELOPER, "dev-a1", Rag.GREEN),
            _status(NodeKind.DEVELOPER, "dev-a2", Rag.UNKNOWN, _unknown("dev-a2")),
            _status(NodeKind.POD, "pod-small", Rag.AMBER, _unknown("dev-a2")),
            _status(NodeKind.PROGRAM, "program-1", Rag.AMBER, _unknown("dev-a2")),
        ):
            await store.record_node_status(status)

    asyncio.run(seed())
    query = f"as_of={DAY.isoformat()}&program_root_id=program-1"

    cells = {
        cell["entity_ref"]["id"]: cell
        for cell in _get(settings, store, "exec", f"/portfolio/heatmap?{query}")["cells"]
    }
    attention = _get(settings, store, "exec", f"/portfolio/attention?{query}")
    managers = _get(settings, store, "mgr", f"/portfolio/attention?{query}")

    for group in ("program-1", "pod-small"):
        assert SMALL_GROUP_ALONE_LINE in cells[group]["reasons"], group
        assert "of 2" not in _said(cells[group]), group
    # Exec Today's read: no check-in counts and no driver about people.
    assert attention["checkins"] is None
    assert "of 2" not in attention["headline"] + (attention["detail"] or "")
    assert managers["checkins"]["people"] == 2
    # Today's replies over a portfolio of two: none for an executive.
    assert _get(settings, store, "exec", f"/me/today-counts?as_of={DAY}")["replies"] is None
    assert _get(settings, store, "mgr", f"/me/today-counts?as_of={DAY}")["replies"]["people"] == 2


def test_an_executives_program_tree_counts_people_and_names_nobody(settings: Settings) -> None:
    """D103: developer nodes become counts on their groups; none under five (D99, D104)."""
    store = InMemoryGraphStore()
    _seed(store)
    path = f"/programs/program-1/tree?as_of={DAY.isoformat()}"

    executive = _get(settings, store, "exec", path)
    manager = _get(settings, store, "mgr", path)

    nodes = {node["id"]: node for node in executive["nodes"]}
    assert not [node for node in executive["nodes"] if node["kind"] == "developer"]
    assert not [
        edge
        for edge in executive["edges"]
        if edge["from_node_id"].startswith("dev-") or edge["to_node_id"].startswith("dev-")
    ]
    assert "Person" not in str(executive)
    assert {group: nodes[group]["people"] for group in nodes if nodes[group]["kind"] != "task"} == {
        "program-1": 10,
        "project-1": 7,
        "project-2": None,
        "pod-big": 5,
        "pod-small": None,
        "pod-tiny": None,
    }
    # No person's own status among a team's factors.
    assert not [
        factor
        for node in executive["nodes"]
        for factor in node["factors"]
        if factor["kind"] == "status" and factor["source_ref"]["kind"] == "developer"
    ]
    # A manager reads every person, and every group's count.
    assert len([node for node in manager["nodes"] if node["kind"] == "developer"]) == 10
    counts = {node["id"]: node["people"] for node in manager["nodes"]}
    assert (counts["pod-small"], counts["project-2"]) == (2, 3)


def test_a_manager_keeps_every_pods_counts(settings: Settings) -> None:
    store = InMemoryGraphStore()
    _seed(store)

    cells = _cells(settings, store, "mgr")

    assert "1 of 2" in _said(cells["pod-small"])
    assert SMALL_GROUP_LINE not in cells["pod-small"]["reasons"]
    assert "1 of 5" in _said(cells["pod-big"])
