from __future__ import annotations

from datetime import UTC, date, datetime

from core.application.blocker_lifecycle import BlockerLifecycleService
from core.application.blocker_resolution import BlockerResolutionService
from core.application.self_status_service import SelfStatusService
from core.domain.blockers import (
    BlockerReport,
    BlockerResolutionReason,
    BlockerSource,
    DeveloperBlocker,
    normalize_blocker_key,
)
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore


async def test_self_status_service_confirms_latest_status() -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 10),
            source=StatusSource.INFERRED,
            blockers=("dependency",),
            summary="Inferred status.",
            eta_change_days=1,
        )
    )
    confirmed_at = datetime(2026, 1, 10, 10, tzinfo=UTC)
    service = SelfStatusService(store)

    confirmed = await service.confirm("demo", "dev-1", date(2026, 1, 10), confirmed_at)

    assert confirmed == DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 10),
        source=StatusSource.CONFIRMED,
        blockers=("dependency",),
        summary="Inferred status.",
        eta_change_days=1,
        developer_confirmed=True,
        confirmed_at=confirmed_at,
    )
    assert await service.my_status("demo", "dev-1", date(2026, 1, 10)) == confirmed


async def test_self_status_service_corrects_structured_fields() -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 10),
            source=StatusSource.STALE,
            blockers=("old dependency",),
            summary="Old status.",
        )
    )
    confirmed_at = datetime(2026, 1, 10, 11, tzinfo=UTC)
    service = SelfStatusService(store)

    corrected = await service.correct(
        "demo",
        "dev-1",
        date(2026, 1, 10),
        summary="Corrected status.",
        blockers=("new dependency",),
        eta_change_days=3,
        confirmed_at=confirmed_at,
    )

    assert corrected is not None
    assert corrected.source is StatusSource.CONFIRMED
    assert corrected.developer_confirmed is True
    assert corrected.confirmed_at == confirmed_at
    assert corrected.summary == "Corrected status."
    assert corrected.blockers == ("new dependency",)
    assert corrected.eta_change_days == 3


async def test_self_status_service_returns_none_without_existing_status() -> None:
    service = SelfStatusService(InMemoryGraphStore())

    assert await service.my_status("demo", "dev-1", date(2026, 1, 10)) is None
    assert await service.confirm("demo", "dev-1", date(2026, 1, 10)) is None
    assert (
        await service.correct(
            "demo",
            "dev-1",
            date(2026, 1, 10),
            summary="Corrected status.",
            blockers=(),
            eta_change_days=None,
        )
        is None
    )


# --- Blocker lifecycle semantics ---------------------------------------------


def _open_blocker(
    blocker_id: str,
    description: str,
    *,
    work_item_id: str | None = None,
    pod_id: str | None = None,
    first_seen_on: date = date(2026, 1, 8),
) -> DeveloperBlocker:
    return DeveloperBlocker(
        tenant_id="demo",
        blocker_id=blocker_id,
        developer_id="dev-1",
        description=description,
        normalized_key=normalize_blocker_key(description),
        work_item_id=work_item_id,
        pod_id=pod_id,
        source=BlockerSource.CHECKIN,
        first_seen_on=first_seen_on,
        last_seen_on=first_seen_on,
    )


async def _lifecycle_service(
    store: InMemoryGraphStore,
) -> SelfStatusService:
    return SelfStatusService(
        store,
        blocker_lifecycle=BlockerLifecycleService(store, store),
        blocker_resolution=BlockerResolutionService(store, store),
    )


async def _seed_status(store: InMemoryGraphStore, *blockers: str) -> None:
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 10),
            source=StatusSource.INFERRED,
            blockers=blockers,
            summary="Working through the queue.",
        )
    )


async def test_self_status_correct_resolves_omitted_blockers() -> None:
    store = InMemoryGraphStore()
    await _seed_status(store, "kept blocker", "dropped blocker")
    await store.record_developer_blockers(
        "demo",
        (
            _open_blocker("blk-1", "kept blocker"),
            _open_blocker("blk-2", "dropped blocker"),
        ),
    )
    service = await _lifecycle_service(store)

    corrected = await service.correct(
        "demo",
        "dev-1",
        date(2026, 1, 10),
        summary="Corrected.",
        blockers=("kept blocker",),
        eta_change_days=None,
    )

    assert corrected is not None
    assert corrected.blockers == ("kept blocker",)
    open_rows = await store.open_blockers("demo", "dev-1", date(2026, 1, 10))
    assert [row.blocker_id for row in open_rows] == ["blk-1"]
    all_rows = store._developer_blockers
    dropped = all_rows[("demo", "blk-2")]
    assert dropped.resolved_on == date(2026, 1, 10)
    assert dropped.resolved_reason is BlockerResolutionReason.OMITTED_IN_CORRECTION


async def test_self_status_correct_detailed_items_set_and_overwrite_attribution() -> None:
    store = InMemoryGraphStore()
    await _seed_status(store, "vendor API")
    await store.record_developer_blockers(
        "demo",
        (_open_blocker("blk-1", "vendor API", work_item_id="OLD-1"),),
    )
    service = await _lifecycle_service(store)

    corrected = await service.correct(
        "demo",
        "dev-1",
        date(2026, 1, 10),
        summary="Corrected.",
        blockers=("vendor API",),
        eta_change_days=None,
        blocker_reports=(BlockerReport(description="vendor API", issue_key="PAY-7"),),
    )

    assert corrected is not None
    rows = await store.open_blockers("demo", "dev-1", date(2026, 1, 10))
    assert rows[0].work_item_id == "PAY-7"  # explicit human edit overwrites


async def test_self_status_correct_resolved_flag_resolves_matched_blocker() -> None:
    store = InMemoryGraphStore()
    await _seed_status(store, "vendor API")
    await store.record_developer_blockers("demo", (_open_blocker("blk-1", "vendor API"),))
    service = await _lifecycle_service(store)

    corrected = await service.correct(
        "demo",
        "dev-1",
        date(2026, 1, 10),
        summary="Corrected.",
        blockers=(),
        eta_change_days=None,
        blocker_reports=(BlockerReport(description="vendor API", resolved=True),),
    )

    assert corrected is not None
    assert corrected.blockers == ()
    assert await store.open_blockers("demo", "dev-1", date(2026, 1, 10)) == []


async def test_self_status_confirm_bumps_last_seen_on_open_blockers() -> None:
    store = InMemoryGraphStore()
    await _seed_status(store, "vendor API")
    await store.record_developer_blockers("demo", (_open_blocker("blk-1", "vendor API"),))
    service = await _lifecycle_service(store)

    confirmed = await service.confirm("demo", "dev-1", date(2026, 1, 10))

    assert confirmed is not None
    assert confirmed.developer_confirmed is True
    rows = await store.open_blockers("demo", "dev-1", date(2026, 1, 10))
    assert rows[0].last_seen_on == date(2026, 1, 10)
    assert rows[0].first_seen_on == date(2026, 1, 8)  # aging preserved


async def test_self_status_my_blocker_details_expose_attribution() -> None:
    store = InMemoryGraphStore()
    await _seed_status(store, "vendor API")
    await store.record_developer_blockers(
        "demo",
        (_open_blocker("blk-1", "vendor API", pod_id="pod-checkout"),),
    )
    service = await _lifecycle_service(store)

    details = await service.my_blocker_details("demo", "dev-1", date(2026, 1, 10))

    assert len(details) == 1
    assert details[0].blocker_id == "blk-1"
    assert details[0].pod_id == "pod-checkout"
    assert details[0].unattributed is False
    assert details[0].age_days == 2
