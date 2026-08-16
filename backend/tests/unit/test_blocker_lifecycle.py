"""Tests for the write-side blocker lifecycle orchestration."""

from __future__ import annotations

from datetime import date

from core.application.blocker_lifecycle import BlockerLifecycleService
from core.domain.blockers import (
    BlockerReport,
    BlockerSource,
    DeveloperBlocker,
    ReconcileMode,
    normalize_blocker_key,
)
from core.domain.graph import EdgeKind, GraphEdge, Pod
from core.domain.status import CheckInSignals, DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore

DAY_1 = date(2026, 1, 10)
DAY_2 = date(2026, 1, 11)


async def _store_with_pods(*pod_names: str) -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for name in pod_names:
        pod_id = f"pod-{name.lower()}"
        await store.upsert_node(Pod(tenant_id="demo", id=pod_id, name=name))
        await store.add_edge(
            GraphEdge(
                tenant_id="demo",
                from_node_id=pod_id,
                to_node_id="dev-1",
                kind=EdgeKind.CONTAINS,
            )
        )
    return store


def _blocker(description: str, *, blocker_id: str = "b-1") -> DeveloperBlocker:
    return DeveloperBlocker(
        tenant_id="demo",
        blocker_id=blocker_id,
        developer_id="dev-1",
        description=description,
        normalized_key=normalize_blocker_key(description),
        source=BlockerSource.CHECKIN,
        first_seen_on=DAY_1,
        last_seen_on=DAY_1,
    )


def _signals(*reports: BlockerReport, resolved_ids: tuple[str, ...] = ()) -> CheckInSignals:
    return CheckInSignals(
        progress_note="update",
        blockers=tuple(report.description for report in reports),
        blocker_reports=reports,
        resolved_blocker_ids=resolved_ids,
    )


async def test_open_blockers_prefers_lifecycle_rows() -> None:
    store = InMemoryGraphStore()
    service = BlockerLifecycleService(store, store)
    row = _blocker("waiting on DBA")
    await store.record_developer_blockers("demo", (row,))

    result = await service.open_blockers("demo", "dev-1", DAY_2)

    assert result == (row,)


async def test_open_blockers_shims_legacy_status_strings() -> None:
    store = InMemoryGraphStore()
    service = BlockerLifecycleService(store, store)
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=DAY_1,
            source=StatusSource.CONFIRMED,
            blockers=("legacy blocker", "no confirmed reply"),
            summary="s",
        )
    )

    result = await service.open_blockers("demo", "dev-1", DAY_2)

    assert len(result) == 1
    assert result[0].description == "legacy blocker"
    assert result[0].blocker_id.startswith("shim:")
    assert result[0].first_seen_on == DAY_1
    assert result[0].source is BlockerSource.CARRY_FORWARD


async def test_reconcile_persists_carried_shim_rows_with_real_ids() -> None:
    store = InMemoryGraphStore()
    service = BlockerLifecycleService(store, store)
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=DAY_1,
            source=StatusSource.CONFIRMED,
            blockers=("legacy blocker",),
            summary="s",
        )
    )
    prior = await service.open_blockers("demo", "dev-1", DAY_2)

    reconciliation = await service.reconcile(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=DAY_2,
        prior=prior,
        signals=_signals(BlockerReport(description="new blocker")),
        mode=ReconcileMode.CHECKIN,
        source=BlockerSource.CHECKIN,
    )

    descriptions = {blocker.description for blocker in reconciliation.upserts}
    assert descriptions == {"new blocker", "legacy blocker"}
    assert all(not blocker.blocker_id.startswith("shim:") for blocker in reconciliation.upserts)
    assert {blocker.description for blocker in reconciliation.open_after} == descriptions


async def test_reconcile_maps_pod_hint_to_known_pod_case_insensitively() -> None:
    store = await _store_with_pods("Checkout", "Payments")
    service = BlockerLifecycleService(store, store)

    reconciliation = await service.reconcile(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=DAY_2,
        prior=(),
        signals=_signals(BlockerReport(description="staging DB", pod_id="checkout")),
        mode=ReconcileMode.CHECKIN,
        source=BlockerSource.CHECKIN,
    )

    assert reconciliation.minted[0].pod_id == "pod-checkout"


async def test_reconcile_drops_unmatched_pod_hint() -> None:
    store = await _store_with_pods("Checkout")
    service = BlockerLifecycleService(store, store)

    reconciliation = await service.reconcile(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=DAY_2,
        prior=(),
        signals=_signals(BlockerReport(description="staging DB", pod_id="warehouse team")),
        mode=ReconcileMode.CHECKIN,
        source=BlockerSource.CHECKIN,
    )

    assert reconciliation.minted[0].pod_id is None


async def test_reconcile_resolves_by_model_reported_ids() -> None:
    store = InMemoryGraphStore()
    service = BlockerLifecycleService(store, store)
    row = _blocker("staging DB access")
    await store.record_developer_blockers("demo", (row,))

    reconciliation = await service.reconcile(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=DAY_2,
        prior=(row,),
        signals=_signals(resolved_ids=("b-1",)),
        mode=ReconcileMode.CHECKIN,
        source=BlockerSource.CHECKIN,
    )
    status = DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=DAY_2,
        source=StatusSource.CONFIRMED,
        blockers=(),
        summary="resolved",
    )
    await service.persist_with_status(status, reconciliation)

    assert await store.open_blockers("demo", "dev-1", DAY_2) == []
    assert await store.has_blocker_rows("demo", "dev-1") is True


async def test_pods_for_developer_without_graph_repository_is_empty() -> None:
    store = InMemoryGraphStore()
    service = BlockerLifecycleService(store, None)

    assert await service.pods_for_developer("demo", "dev-1", DAY_2) == ()
