"""A blocker on another team's work counts where the blocked work is (N15).

R2/R3: Zoe (Storefront) said "CHK-11 is waiting on Omar's HTTP client upgrade
(CHK-17)" (Platform). The blocker was recorded on CHK-17, so it applied only to
the Platform pod, which Zoe is not in: Storefront, Checkout and the program
never showed it, and Platform read green "no blockers". Only Zoe's own cell
was red.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from core.application.blocker_resolution import BlockerResolutionService
from core.application.rollup_service import RollupService
from core.domain.blockers import BlockerResolutionReason, DeveloperBlocker
from core.domain.graph import EdgeKind, EntityRef, GraphEdge, NodeKind, Pod, Task
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_status import _factors_from_json, _factors_to_json
from tests.unit.qa2_checkout_slice import (
    CHK11_BLOCKER,
    DAY,
    NOAH,
    OMAR,
    TENANT,
    ZOE,
    checkout_slice,
    zoe_blocker,
)


async def test_zoes_chk11_blocker_counts_for_storefront_and_waits_on_platform() -> None:
    store = await checkout_slice()

    blockers = await BlockerResolutionService(store, store).open_blockers_for_developer(
        TENANT, ZOE, DAY
    )
    chk11 = next(blocker for blocker in blockers if blocker.blocker_id == "blk-chk11")

    assert chk11.pod_ids == ("pod-storefront",)
    assert not chk11.unattributed
    assert chk11.work_item_ref == EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-11")
    assert chk11.depends_on_pod_ids == ("pod-platform",)
    assert chk11.depends_on_pod_names == ("Platform Pod",)
    assert chk11.depends_on_ref == EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-17")
    # Her CHK-8 blocker is her own work in her own pod: nothing changes for it.
    chk8 = next(blocker for blocker in blockers if blocker.blocker_id == "blk-chk8")
    assert chk8.pod_ids == ("pod-payments",)
    assert chk8.depends_on_pod_ids == ()


async def test_the_cross_team_blocker_reaches_storefront_checkout_and_the_program() -> None:
    store = await _only_the_chk11_blocker()

    by_id = await _rollup(store)

    assert by_id[ZOE].rag is Rag.AMBER
    assert by_id["pod-storefront"].rag is Rag.AMBER
    assert by_id["project-checkout"].rag is Rag.AMBER
    assert by_id["program-platform"].rag is Rag.AMBER
    expected = (
        "Blocker: CHK-11 is waiting on Omar's HTTP client upgrade (CHK-17); "
        "cross-team dependency on Platform."
    )
    for node_id in ("pod-storefront", "project-checkout", "program-platform"):
        assert expected in _texts(by_id[node_id], FactorKind.BLOCKER), node_id
    # Zoe's other pod does not carry it.
    assert by_id["pod-payments"].rag is Rag.GREEN


async def test_platform_reads_an_incoming_dependency_not_a_blocker_and_not_no_blockers() -> None:
    store = await _only_the_chk11_blocker()

    platform = (await _rollup(store))["pod-platform"]

    assert platform.rag is Rag.GREEN
    assert [
        (factor.kind, factor.description, factor.contributes) for factor in platform.factors
    ] == [
        (
            FactorKind.DEPENDENCY,
            "Incoming dependency: Zoe Almeida waits on CHK-17 for CHK-11.",
            Rag.GREEN,
        )
    ]
    factor = platform.factors[0]
    assert factor.source_ref.id == ZOE
    assert factor.work_item_ref == EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-17")


async def test_platforms_own_view_finds_the_dependency_without_the_program_tree() -> None:
    store = await _only_the_chk11_blocker()
    tree = await store.get_program_tree(TENANT, "pod-platform", DAY)
    assert ZOE not in {node.id for node in tree.nodes}

    statuses = await RollupService(
        store, blocker_resolution=BlockerResolutionService(store, store)
    ).compute(tree, DAY)
    platform = next(status for status in statuses if status.entity_ref.id == "pod-platform")

    assert platform.rag is Rag.GREEN
    assert _texts(platform, FactorKind.DEPENDENCY) == [
        "Incoming dependency: Zoe Almeida waits on CHK-17 for CHK-11."
    ]


async def test_both_of_zoes_blockers_make_her_red_but_each_pod_sees_only_its_own() -> None:
    store = await checkout_slice()

    by_id = await _rollup(store)

    assert by_id[ZOE].rag is Rag.RED
    assert by_id["pod-payments"].rag is Rag.AMBER
    assert by_id["pod-storefront"].rag is Rag.AMBER
    assert by_id["pod-platform"].rag is Rag.GREEN
    # Two distinct blockers under one project: red, as two blockers anywhere are.
    assert by_id["project-checkout"].rag is Rag.RED


async def test_a_blocker_naming_only_another_teams_issue_counts_in_the_persons_pods() -> None:
    store = await checkout_slice()
    await _resolve_both(store)
    await store.record_developer_blockers(
        TENANT, (zoe_blocker("blk-only", "Waiting on Omar's CHK-17 merge", work_item_id="CHK-17"),)
    )

    [blocker] = await BlockerResolutionService(store, store).open_blockers_for_developer(
        TENANT, ZOE, DAY
    )

    assert blocker.pod_ids == ("pod-payments", "pod-storefront")
    assert blocker.unattributed
    assert blocker.depends_on_pod_ids == ("pod-platform",)


async def test_a_blocker_on_an_issue_with_no_pod_attaches_to_the_persons_pods() -> None:
    store = await checkout_slice()
    await store.record_developer_blockers(
        TENANT,
        (zoe_blocker("blk-unknown", "CHK-99 needs a design sign-off", work_item_id="CHK-99"),),
    )
    await store.record_developer_blockers(
        TENANT,
        (
            replace(
                zoe_blocker("blk-omar-1", "IDP-42 waits on a vendor key", work_item_id="IDP-42"),
                developer_id=OMAR,
            ),
        ),
    )
    resolution = BlockerResolutionService(store, store)

    zoes = {b.blocker_id: b for b in await resolution.open_blockers_for_developer(TENANT, ZOE, DAY)}
    [omars] = await resolution.open_blockers_for_developer(TENANT, OMAR, DAY)

    assert zoes["blk-unknown"].pod_ids == ("pod-payments", "pod-storefront")
    assert zoes["blk-unknown"].depends_on_pod_ids == ()
    assert omars.pod_ids == ("pod-platform",)


async def test_a_blocker_on_ones_own_issue_in_another_pod_still_shows_in_ones_own() -> None:
    store = await checkout_slice()
    await store.upsert_node(Pod(tenant_id=TENANT, id="pod-identity", name="Identity Pod"))
    await store.upsert_node(Task(tenant_id=TENANT, id="IDP-6", name="SAML metadata refresh"))
    for edge in (
        GraphEdge(
            tenant_id=TENANT,
            from_node_id="pod-identity",
            to_node_id="IDP-6",
            kind=EdgeKind.CONTAINS,
        ),
        GraphEdge(
            tenant_id=TENANT, from_node_id=OMAR, to_node_id="IDP-6", kind=EdgeKind.ASSIGNED_TO
        ),
    ):
        await store.add_edge(edge)
    await store.record_developer_blockers(
        TENANT,
        (
            replace(
                zoe_blocker("blk-idp6", "IDP-6 waits on the IdP sandbox", work_item_id="IDP-6"),
                developer_id=OMAR,
            ),
        ),
    )

    [blocker] = await BlockerResolutionService(store, store).open_blockers_for_developer(
        TENANT, OMAR, DAY
    )

    assert blocker.pod_ids == ("pod-identity", "pod-platform")
    assert (await _rollup(store))["pod-platform"].rag is Rag.AMBER


async def test_waiting_on_a_teammate_is_not_a_cross_team_dependency() -> None:
    store = await checkout_slice()
    await store.record_developer_blockers(
        TENANT,
        (
            replace(
                zoe_blocker(
                    "blk-noah", "Waiting on Zoe's CHK-8 before I can start", work_item_id="CHK-8"
                ),
                developer_id=NOAH,
            ),
        ),
    )

    [blocker] = await BlockerResolutionService(store, store).open_blockers_for_developer(
        TENANT, NOAH, DAY
    )

    assert blocker.pod_ids == ("pod-payments",)
    assert blocker.depends_on_pod_ids == ()
    statuses = await _rollup(store)
    assert _texts(statuses["pod-payments"], FactorKind.DEPENDENCY) == []


def test_a_dependency_factor_survives_the_stored_rollup_row() -> None:
    factor = (_factors_from_json(_factors_to_json((_dependency_factor(),))))[0]

    assert factor.kind is FactorKind.DEPENDENCY
    assert factor.contributes is Rag.GREEN


# --- helpers --------------------------------------------------------------------


async def _only_the_chk11_blocker() -> InMemoryGraphStore:
    """The slice after N19 closed the CHK-8 blocker: only CHK-11 waits."""
    store = await checkout_slice()
    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None
    [chk8] = [b for b in store._developer_blockers.values() if b.blocker_id == "blk-chk8"]
    await store.record_developer_status_with_blockers(
        replace(status, blockers=(CHK11_BLOCKER,)), (_resolved(chk8),)
    )
    return store


async def _resolve_both(store: InMemoryGraphStore) -> None:
    await store.record_developer_blockers(
        TENANT, [_resolved(row) for row in list(store._developer_blockers.values())]
    )


def _resolved(blocker: DeveloperBlocker) -> DeveloperBlocker:
    return replace(
        blocker, resolved_on=DAY, resolved_reason=BlockerResolutionReason.REPORTED_RESOLVED
    )


async def _rollup(store: InMemoryGraphStore, as_of: date = DAY) -> dict[str, NodeStatus]:
    tree = await store.get_program_tree(TENANT, "program-platform", as_of)
    statuses = await RollupService(
        store, blocker_resolution=BlockerResolutionService(store, store)
    ).compute(tree, as_of)
    return {status.entity_ref.id: status for status in statuses}


def _texts(status: NodeStatus, kind: FactorKind) -> list[str]:
    return [factor.description for factor in status.factors if factor.kind is kind]


def _dependency_factor() -> RollupFactor:
    return RollupFactor(
        description="Incoming dependency: Zoe Almeida waits on CHK-17 for CHK-11.",
        contributes=Rag.GREEN,
        source_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id=ZOE),
        kind=FactorKind.DEPENDENCY,
        blocker_id="blk-chk11",
    )
