"""The portfolio's heat map and attention read name nobody to an executive (D87).

The same day read by a manager names who has not answered and whose blockers
are open; read by an executive it counts them, names the work items, and holds
no person's cell but the reader's own.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.portfolio_privacy import (
    OPEN_BLOCKER,
    counted_statuses,
    own_cells_only,
    reads_people,
)
from core.domain.auth import Principal, Role
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    GraphEdge,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
)
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from core.domain.status import CheckIn, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry

T = "demo"
DAY = date(2026, 6, 15)
#: Everyone the reasons could name, but the reader.
OTHERS = ("Ada Quill", "Ada", "Quill", "Ben Marsh", "Ben", "Marsh")


def _ref(kind: NodeKind, node_id: str) -> EntityRef:
    return EntityRef(tenant_id=T, kind=kind, id=node_id)


def _blocker() -> RollupFactor:
    return RollupFactor(
        description="Blocker: waiting on Ben Marsh to review the KEY-1 schema",
        contributes=Rag.RED,
        source_ref=_ref(NodeKind.TASK, "KEY-1"),
        kind=FactorKind.BLOCKER,
        blocker_id="blocker-1",
        work_item_ref=_ref(NodeKind.TASK, "KEY-1"),
        applies_to_pod_ids=("pod-core",),
    )


def _dependency() -> RollupFactor:
    return RollupFactor(
        description="Incoming dependency: Ada Quill waits on KEY-2.",
        contributes=Rag.GREEN,
        source_ref=_ref(NodeKind.DEVELOPER, "dev-ada"),
        kind=FactorKind.DEPENDENCY,
    )


def _person_update(developer_id: str) -> RollupFactor:
    return RollupFactor(
        description="Developer status is unknown.",
        contributes=Rag.UNKNOWN,
        source_ref=_ref(NodeKind.DEVELOPER, developer_id),
        kind=FactorKind.STATUS,
    )


def _seed(store: InMemoryGraphStore) -> None:
    async def seed() -> None:
        for node in (
            Program(tenant_id=T, id="program-1", name="Core Program"),
            Project(tenant_id=T, id="project-1", name="Checkout"),
            Pod(tenant_id=T, id="pod-core", name="Core Pod"),
            Developer(tenant_id=T, id="dev-ada", name="Ada Quill"),
            Developer(tenant_id=T, id="dev-ben", name="Ben Marsh"),
            Developer(tenant_id=T, id="dev-elle", name="Elle Exec"),
            Task(tenant_id=T, id="KEY-1", name="Schema", metadata={"key": "KEY-1"}),
        ):
            await store.upsert_node(node)
        for parent, child in (
            ("program-1", "project-1"),
            ("project-1", "pod-core"),
            ("pod-core", "dev-ada"),
            ("pod-core", "dev-ben"),
            ("pod-core", "KEY-1"),
        ):
            await store.add_edge(
                GraphEdge(
                    tenant_id=T, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
                )
            )
        team = (_blocker(), _person_update("dev-ben"), _dependency())
        statuses = (
            NodeStatus(
                entity_ref=_ref(NodeKind.DEVELOPER, "dev-ada"),
                rag=Rag.RED,
                source=StatusSource.CONFIRMED,
                factors=(_blocker(),),
                as_of=DAY,
            ),
            NodeStatus(
                entity_ref=_ref(NodeKind.DEVELOPER, "dev-ben"),
                rag=Rag.UNKNOWN,
                source=StatusSource.UNKNOWN,
                factors=(_person_update("dev-ben"),),
                as_of=DAY,
            ),
            NodeStatus(
                entity_ref=_ref(NodeKind.DEVELOPER, "dev-elle"),
                rag=Rag.GREEN,
                source=StatusSource.CONFIRMED,
                factors=(
                    RollupFactor(
                        description="In no pod.",
                        contributes=Rag.GREEN,
                        source_ref=_ref(NodeKind.DEVELOPER, "dev-elle"),
                        kind=FactorKind.NO_POD,
                    ),
                    RollupFactor(
                        description="Confirmed status has no blockers.",
                        contributes=Rag.GREEN,
                        source_ref=_ref(NodeKind.DEVELOPER, "dev-elle"),
                    ),
                ),
                as_of=DAY,
            ),
            *(
                NodeStatus(
                    entity_ref=_ref(kind, node_id),
                    rag=Rag.RED,
                    source=StatusSource.CONFIRMED,
                    factors=team,
                    as_of=DAY,
                )
                for kind, node_id in (
                    (NodeKind.POD, "pod-core"),
                    (NodeKind.PROJECT, "project-1"),
                    (NodeKind.PROGRAM, "program-1"),
                )
            ),
        )
        for status in statuses:
            await store.record_node_status(status)
        await store.record_checkin(
            CheckIn(
                tenant_id=T,
                developer_id="dev-ben",
                correlation_id="checkin-ben",
                asked_at=datetime(2026, 6, 15, 9, 0, tzinfo=UTC),
                replied_at=None,
                raw_reply=None,
                signals=None,
            )
        )

    asyncio.run(seed())


def _bodies(settings: Settings, store: InMemoryGraphStore, roles: str) -> tuple[dict, dict]:
    role_settings = settings.model_copy(
        update={"dev_principal_roles": roles, "dev_principal_subject": "dev-elle"}
    )
    app = create_app(
        settings=role_settings, registry=ServiceRegistry(role_settings, graph_store=store)
    )
    query = f"as_of={DAY.isoformat()}&program_root_id=program-1"
    with TestClient(app) as client:
        heatmap = client.get(f"/portfolio/heatmap?{query}")
        attention = client.get(f"/portfolio/attention?{query}")
    assert heatmap.status_code == 200, heatmap.text
    assert attention.status_code == 200, attention.text
    return heatmap.json(), attention.json()


def _named(body: dict) -> list[str]:
    text = json.dumps(body)
    return [name for name in OTHERS if re.search(rf"\b{re.escape(name)}\b", text)]


def test_a_manager_reads_who_and_an_executive_reads_counts(settings: Settings) -> None:
    store = InMemoryGraphStore()
    _seed(store)

    heatmap, attention = _bodies(settings, store, "mgr")
    # The manager keeps what they have: the people and the blocker's own words.
    assert _named(heatmap)
    assert any(cell["entity_ref"]["kind"] == "developer" for cell in heatmap["cells"])

    heatmap, attention = _bodies(settings, store, "exec")
    assert _named(heatmap) == []
    assert _named(attention) == []
    text = json.dumps(heatmap)
    assert "waiting on" not in text and "schema" not in text.casefold()
    # No one's cell but the reader's own, which is on the "no pod" row (N5).
    people = [cell for cell in heatmap["cells"] if cell["entity_ref"]["kind"] == "developer"]
    assert [cell["entity_ref"]["id"] for cell in people] == ["dev-elle"]
    # The reasons still say how many, and on which work.
    project = next(cell for cell in heatmap["cells"] if cell["entity_ref"]["id"] == "project-1")
    assert any("KEY-1" in line for line in project["reasons"])
    assert any("1 of 2" in line for line in project["reasons"]), project["reasons"]
    assert "another team" in text


def test_counted_statuses_keep_each_blocker_once_without_its_words() -> None:
    twin = RollupFactor(
        description="Blocker: Ada says the vendor is late",
        contributes=Rag.AMBER,
        source_ref=_ref(NodeKind.DEVELOPER, "dev-ada"),
        kind=FactorKind.BLOCKER,
    )
    other = RollupFactor(
        description="Blocker: Ben is out sick",
        contributes=Rag.AMBER,
        source_ref=_ref(NodeKind.DEVELOPER, "dev-ben"),
        kind=FactorKind.BLOCKER,
    )
    status = NodeStatus(
        entity_ref=_ref(NodeKind.POD, "pod-core"),
        rag=Rag.AMBER,
        source=StatusSource.CONFIRMED,
        factors=(twin, twin, other, _blocker(), _dependency()),
        as_of=DAY,
    )

    (counted,) = counted_statuses([status])

    blockers = [factor for factor in counted.factors if factor.kind is FactorKind.BLOCKER]
    assert {factor.description for factor in blockers} == {OPEN_BLOCKER}
    # Two different blockers stay two; the same one stays one; an id is kept.
    assert len({factor.blocker_id for factor in blockers}) == 3
    assert "blocker-1" in {factor.blocker_id for factor in blockers}
    dependency = counted.factors[-1]
    assert dependency.description == "Incoming dependency: another team waits on KEY-2."


def test_only_the_readers_own_person_cell_is_kept() -> None:
    cells = [
        NodeStatus(
            entity_ref=_ref(kind, node_id),
            rag=Rag.GREEN,
            source=StatusSource.CONFIRMED,
            factors=(),
            as_of=DAY,
        )
        for kind, node_id in (
            (NodeKind.DEVELOPER, "dev-ada"),
            (NodeKind.DEVELOPER, "dev-elle"),
            (NodeKind.POD, "pod-core"),
        )
    ]

    kept = own_cells_only(cells, "dev-elle")

    assert [status.entity_ref.id for status in kept] == ["dev-elle", "pod-core"]


def test_who_reads_people_follows_the_pod_check_ins() -> None:
    def principal(*roles: Role) -> Principal:
        return Principal(subject="u", tenant_id=T, roles=frozenset(roles))

    assert reads_people(principal(Role.MGR))
    assert reads_people(principal(Role.ADMIN))
    assert reads_people(principal(Role.SM))
    assert not reads_people(principal(Role.EXEC))
    # An executive who is also a manager reads what the manager reads.
    assert reads_people(principal(Role.EXEC, Role.MGR))
