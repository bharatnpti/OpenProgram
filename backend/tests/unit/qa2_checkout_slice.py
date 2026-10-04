"""The part of the qa2 org the R2/R3 blocker cases live in, as an in-memory graph.

Digital Platform Program -> Checkout Revamp -> Payments, Storefront and
Platform pods. Zoe is in Payments and Storefront and owns CHK-8 (Payments)
and CHK-11 (Storefront); Omar is in Platform and owns CHK-17 (Platform);
Noah is in Payments; Asha is in Platform.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.graph import (
    Developer,
    EdgeKind,
    GraphEdge,
    GraphNode,
    Pod,
    Program,
    Project,
    Task,
)
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore

TENANT = "demo"
DAY = date(2026, 10, 4)
ZOE, NOAH, OMAR, ASHA = "U-zoe", "U-noah", "U-omar", "U-asha"
# Zoe's R3 check-in restated both blockers; the rows were written then.
R3_STATED = datetime(2026, 10, 4, 0, 4, 43, tzinfo=UTC)
CHK8_BLOCKER = (
    "CHK-8 payment form validation is done on storefront-web !1, but it's blocked "
    "until Noah reviews it, nobody has looked at it yet."
)
CHK11_BLOCKER = "CHK-11 is waiting on Omar's HTTP client upgrade (CHK-17)."

_NODES: tuple[GraphNode, ...] = (
    Program(tenant_id=TENANT, id="program-platform", name="Digital Platform Program"),
    Project(tenant_id=TENANT, id="project-checkout", name="Checkout Revamp"),
    Pod(tenant_id=TENANT, id="pod-payments", name="Payments Pod"),
    Pod(tenant_id=TENANT, id="pod-storefront", name="Storefront Pod"),
    Pod(tenant_id=TENANT, id="pod-platform", name="Platform Pod"),
    Developer(tenant_id=TENANT, id=ZOE, name="Zoe Almeida"),
    Developer(tenant_id=TENANT, id=NOAH, name="Noah Weber"),
    Developer(tenant_id=TENANT, id=OMAR, name="Omar Haddad"),
    Developer(tenant_id=TENANT, id=ASHA, name="Asha Rao"),
    Task(
        tenant_id=TENANT,
        id="CHK-8",
        name="Payment form validation UI",
        metadata={"status": "In Progress"},
    ),
    Task(
        tenant_id=TENANT,
        id="CHK-11",
        name="Cart price breakdown",
        metadata={"status": "In Progress"},
    ),
    Task(
        tenant_id=TENANT,
        id="CHK-17",
        name="Upgrade shared HTTP client",
        metadata={"status": "In Progress"},
    ),
)
_CONTAINS = (
    ("program-platform", "project-checkout"),
    ("project-checkout", "pod-payments"),
    ("project-checkout", "pod-storefront"),
    ("project-checkout", "pod-platform"),
    ("pod-payments", ZOE),
    ("pod-payments", NOAH),
    ("pod-storefront", ZOE),
    ("pod-platform", OMAR),
    ("pod-platform", ASHA),
    ("pod-payments", "CHK-8"),
    ("pod-storefront", "CHK-11"),
    ("pod-platform", "CHK-17"),
    ("project-checkout", "CHK-8"),
    ("project-checkout", "CHK-11"),
    ("project-checkout", "CHK-17"),
)
_ASSIGNED = ((ZOE, "CHK-8"), (ZOE, "CHK-11"), (OMAR, "CHK-17"))


async def checkout_slice(*, stated_at: datetime = R3_STATED) -> InMemoryGraphStore:
    """The slice with everyone confirmed on DAY; Zoe reports both blockers."""
    store = InMemoryGraphStore()
    store.blocker_clock = lambda: stated_at
    for node in _NODES:
        await store.upsert_node(node)
    for parent, child in _CONTAINS:
        await store.add_edge(
            GraphEdge(
                tenant_id=TENANT, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
            )
        )
    for developer, task in _ASSIGNED:
        await store.add_edge(
            GraphEdge(
                tenant_id=TENANT, from_node_id=developer, to_node_id=task, kind=EdgeKind.ASSIGNED_TO
            )
        )
    for developer in (NOAH, OMAR, ASHA):
        await store.record_developer_status(_confirmed(developer, ()))
    await store.record_developer_status_with_blockers(
        _confirmed(ZOE, (CHK11_BLOCKER, CHK8_BLOCKER)),
        (
            zoe_blocker("blk-chk8", CHK8_BLOCKER, work_item_id="CHK-8"),
            zoe_blocker("blk-chk11", CHK11_BLOCKER, work_item_id="CHK-17"),
        ),
    )
    return store


def zoe_blocker(blocker_id: str, description: str, *, work_item_id: str | None) -> DeveloperBlocker:
    return DeveloperBlocker(
        tenant_id=TENANT,
        blocker_id=blocker_id,
        developer_id=ZOE,
        description=description,
        normalized_key=normalize_blocker_key(description),
        work_item_id=work_item_id,
        source=BlockerSource.CHECKIN,
        first_seen_on=date(2026, 10, 3),
        last_seen_on=DAY,
    )


def _confirmed(developer_id: str, blockers: tuple[str, ...]) -> DeveloperStatus:
    return DeveloperStatus(
        tenant_id=TENANT,
        developer_id=developer_id,
        as_of=DAY,
        source=StatusSource.CONFIRMED,
        blockers=blockers,
        summary="Update recorded.",
    )
