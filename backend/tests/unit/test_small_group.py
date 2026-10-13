"""The small-group rule (D99): an executive gets no people counts on a pod under five.

A pod of two is effectively two people, so "1 of 2 haven't answered" on its tile
names someone to a reader who reads no one's check-in. For an executive, such a
pod's people roll into its project's line; its colour, its work and the dates
still show. A pod of five or more keeps its counts, and a manager keeps all of
them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.people_words import SMALL_GROUP_LINE
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
    """Project 1 with a pod of two (one silent) and a pod of five (one silent)."""

    async def seed() -> None:
        nodes = [
            Program(tenant_id=T, id="program-1", name="Core Program"),
            Project(tenant_id=T, id="project-1", name="Checkout"),
            Pod(tenant_id=T, id="pod-small", name="Small Pod"),
            Pod(tenant_id=T, id="pod-big", name="Big Pod"),
            *(Developer(tenant_id=T, id=person, name=f"Person {person}") for person in SMALL),
            *(Developer(tenant_id=T, id=person, name=f"Person {person}") for person in BIG),
        ]
        for node in nodes:
            await store.upsert_node(node)
        links = [
            ("program-1", "project-1"),
            ("project-1", "pod-small"),
            ("project-1", "pod-big"),
            *(("pod-small", person) for person in SMALL),
            *(("pod-big", person) for person in BIG),
        ]
        for parent, child in links:
            await store.add_edge(
                GraphEdge(
                    tenant_id=T, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
                )
            )
        silent = ("dev-a2", "dev-b5")
        statuses = [
            *(
                _status(NodeKind.DEVELOPER, person, Rag.UNKNOWN, _unknown(person))
                if person in silent
                else _status(NodeKind.DEVELOPER, person, Rag.GREEN)
                for person in (*SMALL, *BIG)
            ),
            _status(NodeKind.POD, "pod-small", Rag.AMBER, _unknown("dev-a2")),
            _status(NodeKind.POD, "pod-big", Rag.AMBER, _unknown("dev-b5")),
            _status(
                NodeKind.PROJECT, "project-1", Rag.AMBER, _unknown("dev-a2"), _unknown("dev-b5")
            ),
            _status(
                NodeKind.PROGRAM, "program-1", Rag.AMBER, _unknown("dev-a2"), _unknown("dev-b5")
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


def _cells(settings: Settings, store: InMemoryGraphStore, roles: str) -> dict[str, dict]:
    role_settings = settings.model_copy(
        update={"dev_principal_roles": roles, "dev_principal_subject": "reader"}
    )
    app = create_app(
        settings=role_settings, registry=ServiceRegistry(role_settings, graph_store=store)
    )
    with TestClient(app) as client:
        response = client.get(
            f"/portfolio/heatmap?as_of={DAY.isoformat()}&program_root_id=program-1"
        )
    assert response.status_code == 200, response.text
    return {cell["entity_ref"]["id"]: cell for cell in response.json()["cells"]}


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


def test_a_manager_keeps_every_pods_counts(settings: Settings) -> None:
    store = InMemoryGraphStore()
    _seed(store)

    cells = _cells(settings, store, "mgr")

    assert "1 of 2" in _said(cells["pod-small"])
    assert SMALL_GROUP_LINE not in cells["pod-small"]["reasons"]
    assert "1 of 5" in _said(cells["pod-big"])
