"""A developer reads their pod's colour in counts and work items, never a teammate.

The scrum master reads the same pod's reasons as built: whose status is
unknown, whose blocker says what. A developer in the pod reads "2 not
replied" and "1 blocker on KEY-1"; their own reasons keep their own words.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.persona_views import PodRollupView
from core.application.teammate_reasons import teammates_counted
from core.domain.graph import Developer, EdgeKind, EntityRef, GraphEdge, NodeKind, Pod, Task
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from core.domain.status import StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry

T = "demo"
DAY = date(2026, 6, 15)
TEAMMATES = ("Ada Quill", "Ben Marsh", "Cy Rowe")


def _ref(kind: NodeKind, node_id: str) -> EntityRef:
    return EntityRef(tenant_id=T, kind=kind, id=node_id)


def _status(person: str, description: str, rag: Rag) -> RollupFactor:
    return RollupFactor(
        description=description,
        contributes=rag,
        source_ref=_ref(NodeKind.DEVELOPER, person),
        kind=FactorKind.STATUS,
    )


def _blocker(blocker_id: str, words: str, item: str) -> RollupFactor:
    return RollupFactor(
        description=f"Blocker: {words}",
        contributes=Rag.AMBER,
        source_ref=_ref(NodeKind.WORK_ITEM, item),
        kind=FactorKind.BLOCKER,
        blocker_id=blocker_id,
        work_item_ref=_ref(NodeKind.WORK_ITEM, item),
        applies_to_pod_ids=("pod-core",),
    )


FACTORS = (
    _blocker("b-1", "waiting on Ben Marsh for the KEY-1 schema", "KEY-1"),
    _blocker("b-2", "Ada Quill is out until Friday", "KEY-1"),
    _blocker("b-3", "vendor sandbox down", "KEY-2"),
    _status("dev-ada", "Developer status is unknown.", Rag.UNKNOWN),
    _status("dev-ben", "Developer status is unknown.", Rag.UNKNOWN),
    _status("dev-cy", "Status is partial and needs blocker or ETA confirmation.", Rag.AMBER),
    _status("dev-kai", "Developer status is unknown.", Rag.UNKNOWN),
    RollupFactor(
        description="Ben Marsh says KEY-2 is on track; it has had no commit in 9 days.",
        contributes=Rag.AMBER,
        source_ref=_ref(NodeKind.DEVELOPER, "dev-ben"),
        kind=FactorKind.DRIFT,
        work_item_ref=_ref(NodeKind.WORK_ITEM, "KEY-2"),
    ),
)
NAMES = {
    "dev-ada": "Ada Quill",
    "dev-ben": "Ben Marsh",
    "dev-cy": "Cy Rowe",
    "dev-kai": "Kai Lund",
    "KEY-1": "Schema",
    "KEY-2": "Sandbox",
}


def _view() -> PodRollupView:
    return PodRollupView(
        pod_id="pod-core",
        pod_name="Core Pod",
        as_of=DAY,
        rag=Rag.RED,
        source=StatusSource.UNKNOWN,
        factors=FACTORS,
        source_names=NAMES,
    )


def test_teammates_are_counted_and_the_reader_keeps_their_own_row() -> None:
    seen = teammates_counted(_view(), "dev-kai")

    lines = [factor.description for factor in seen.factors]
    assert lines == [
        "Developer status is unknown.",  # Kai's own, named as his
        "2 blockers on KEY-1.",
        "1 blocker on KEY-2.",
        "Status and Jira disagree on KEY-2.",
        "2 not replied.",
        "1 replied in part.",
    ]
    text = json.dumps([lines, seen.source_names])
    assert not any(name in text for name in TEAMMATES)
    assert seen.source_names["dev-kai"] == "Kai Lund"
    assert seen.source_names["pod-core"] == "Core Pod"
    # Two blockers on two work items stay two (the red-by-count rule still reads them).
    blocker_ids = {f.blocker_id for f in seen.factors if f.kind is FactorKind.BLOCKER}
    assert len(blocker_ids) == 2
    # The counts keep the colour they gave: the worst of their group.
    assert seen.factors[4].contributes is Rag.UNKNOWN


def _seed(store: InMemoryGraphStore) -> None:
    async def seed() -> None:
        for node in (
            Pod(tenant_id=T, id="pod-core", name="Core Pod"),
            Developer(tenant_id=T, id="dev-ada", name="Ada Quill"),
            Developer(tenant_id=T, id="dev-ben", name="Ben Marsh"),
            Developer(tenant_id=T, id="dev-cy", name="Cy Rowe"),
            Developer(tenant_id=T, id="dev-kai", name="Kai Lund"),
            Task(tenant_id=T, id="KEY-1", name="Schema", metadata={"key": "KEY-1"}),
        ):
            await store.upsert_node(node)
        for member in ("dev-ada", "dev-ben", "dev-cy", "dev-kai", "KEY-1"):
            await store.add_edge(
                GraphEdge(
                    tenant_id=T, from_node_id="pod-core", to_node_id=member, kind=EdgeKind.CONTAINS
                )
            )
        await store.record_node_status(
            NodeStatus(
                entity_ref=_ref(NodeKind.POD, "pod-core"),
                rag=Rag.RED,
                source=StatusSource.UNKNOWN,
                factors=FACTORS,
                as_of=DAY,
            )
        )

    asyncio.run(seed())


def _rollup(settings: Settings, store: InMemoryGraphStore, roles: str, subject: str) -> dict:
    role_settings = settings.model_copy(
        update={"dev_principal_roles": roles, "dev_principal_subject": subject}
    )
    app = create_app(
        settings=role_settings, registry=ServiceRegistry(role_settings, graph_store=store)
    )
    with TestClient(app) as client:
        response = client.get(f"/pods/pod-core/rollup?as_of={DAY.isoformat()}")
    assert response.status_code == 200, response.text
    return response.json()


def test_the_route_counts_teammates_for_a_developer_only(settings: Settings) -> None:
    store = InMemoryGraphStore()
    _seed(store)

    developer = _rollup(settings, store, "dev", "dev-kai")
    manager = _rollup(settings, store, "mgr", "someone")

    dev_text = json.dumps(developer)
    assert not any(name in dev_text for name in TEAMMATES), dev_text
    assert "2 not replied." in dev_text and "2 blockers on KEY-1." in dev_text
    assert "out until Friday" not in dev_text
    assert developer["rag"] == manager["rag"] == "red"
    # The manager reads whose status and whose blocker, as built.
    mgr_text = json.dumps(manager)
    assert "Ben Marsh" in mgr_text and "Ada Quill is out until Friday" in mgr_text
