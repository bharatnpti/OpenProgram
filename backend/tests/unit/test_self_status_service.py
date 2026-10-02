from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from core.application.blocker_lifecycle import BlockerLifecycleService
from core.application.blocker_resolution import BlockerResolutionService
from core.application.self_status_service import SelfStatusService
from core.application.status_summaries import NO_REPLY_BLOCKER
from core.domain.blockers import (
    BlockerReport,
    BlockerResolutionReason,
    BlockerSource,
    DeveloperBlocker,
    normalize_blocker_key,
)
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore

_INFERRED = (
    "No confirmed check-in after a nudge. Inferred from 2 active issues: "
    "PO-1 Payment intent API (blocked) and PO-2 Refund edge cases."
)


async def test_self_status_service_confirms_latest_status() -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 10),
            source=StatusSource.INFERRED,
            blockers=("dependency",),
            summary=_INFERRED,
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
        summary=(
            "Confirmed the status inferred from 2 active issues: "
            "PO-1 Payment intent API (blocked) and PO-2 Refund edge cases."
        ),
        eta_change_days=1,
        developer_confirmed=True,
        confirmed_at=confirmed_at,
    )
    assert await service.my_status("demo", "dev-1", date(2026, 1, 10)) == confirmed


def _history_status(
    as_of: date, source: StatusSource, summary: str, *blockers: str
) -> DeveloperStatus:
    return DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=as_of,
        source=source,
        blockers=blockers,
        summary=summary,
    )


_STALE_ON_8 = (
    "No confirmed check-in after a nudge. Last known confirmed status on 2026-01-07: "
    "Shipped the refund flow."
)


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        pytest.param(
            [_history_status(date(2026, 1, 10), StatusSource.CONFIRMED, "Shipped it.")],
            "Shipped it.",
            id="confirmed-same-day-unchanged",
        ),
        pytest.param(
            [_history_status(date(2026, 1, 10), StatusSource.PARTIAL, "Halfway; ETA open.")],
            "Halfway; ETA open.",
            id="partial-same-day-unchanged",
        ),
        pytest.param(
            [_history_status(date(2026, 1, 9), StatusSource.INFERRED, _INFERRED)],
            "Confirmed the status inferred on Jan 9 from 2 active issues: "
            "PO-1 Payment intent API (blocked) and PO-2 Refund edge cases.",
            id="inferred-carried-forward",
        ),
        pytest.param(
            [_history_status(date(2026, 1, 9), StatusSource.CONFIRMED, "Friday's answer.")],
            "Confirmed the status from Jan 9: Friday's answer.",
            id="confirmed-carried-forward",
        ),
        pytest.param(
            [_history_status(date(2026, 1, 9), StatusSource.PARTIAL, "Halfway; ETA open.")],
            "Confirmed the status from Jan 9: Halfway; ETA open.",
            id="partial-carried-forward",
        ),
        pytest.param(
            [
                _history_status(
                    date(2026, 1, 9),
                    StatusSource.CONFIRMED,
                    "Confirmed the status from Jan 7: Shipped the refund flow.",
                )
            ],
            "Confirmed the status from Jan 7: Shipped the refund flow.",
            id="reconfirmation-carried-forward-not-nested",
        ),
        pytest.param(
            # Two days of non-response in the stored, nested wording: the
            # confirmation names the status they stood for, once.
            [
                _history_status(
                    date(2026, 1, 7), StatusSource.CONFIRMED, "Shipped the refund flow."
                ),
                _history_status(
                    date(2026, 1, 8), StatusSource.STALE, _STALE_ON_8, NO_REPLY_BLOCKER
                ),
                _history_status(
                    date(2026, 1, 10),
                    StatusSource.STALE,
                    "No confirmed check-in after a nudge. Last known stale status on "
                    f"2026-01-08: {_STALE_ON_8}",
                    NO_REPLY_BLOCKER,
                ),
            ],
            "Confirmed the status from Jan 7: Shipped the refund flow.",
            id="stale-names-last-confirmed",
        ),
        pytest.param(
            [
                _history_status(
                    date(2026, 1, 8),
                    StatusSource.INFERRED,
                    "No confirmed check-in after a nudge. Inferred from recent Git "
                    "activity: 1 commit.",
                ),
                _history_status(
                    date(2026, 1, 10),
                    StatusSource.STALE,
                    "No confirmed check-in after a nudge. Last known inferred status on "
                    "2026-01-08: No confirmed check-in after a nudge. Inferred from recent "
                    "Git activity: 1 commit.",
                    NO_REPLY_BLOCKER,
                ),
            ],
            "Confirmed the status inferred on Jan 8 from recent Git activity: 1 commit.",
            id="stale-names-inferred-basis",
        ),
        pytest.param(
            [
                _history_status(
                    date(2026, 1, 10),
                    StatusSource.UNKNOWN,
                    "No confirmed check-in after a nudge. Current status is unknown.",
                    NO_REPLY_BLOCKER,
                )
            ],
            "Confirmed the status, with no details recorded.",
            id="unknown",
        ),
        pytest.param(
            # Nothing that says anything within the lookback: no basis to name.
            [
                _history_status(date(2025, 9, 1), StatusSource.CONFIRMED, "Long ago."),
                _history_status(
                    date(2026, 1, 10), StatusSource.STALE, _STALE_ON_8, NO_REPLY_BLOCKER
                ),
            ],
            "Confirmed the status, with no details recorded.",
            id="stale-without-basis-in-lookback",
        ),
        pytest.param(
            # Confirm used to copy the non-response summary in verbatim.
            [
                _history_status(
                    date(2026, 1, 9),
                    StatusSource.CONFIRMED,
                    "No confirmed check-in after a nudge. Inferred from 1 active issue: "
                    "PO-1 Payment intent API.",
                )
            ],
            "Confirmed the status from Jan 9: Inferred from 1 active issue: "
            "PO-1 Payment intent API.",
            id="legacy-confirmed-copy",
        ),
        pytest.param(
            [_history_status(date(2026, 1, 10), StatusSource.INFERRED, "Working the queue.")],
            "Confirmed the status inferred: Working the queue.",
            id="inferred-in-other-words",
        ),
    ],
)
async def test_self_status_confirm_says_what_was_confirmed(
    history: list[DeveloperStatus], expected: str
) -> None:
    store = InMemoryGraphStore()
    for status in history:
        await store.record_developer_status(status)
    service = SelfStatusService(store)

    confirmed = await service.confirm(
        "demo", "dev-1", date(2026, 1, 10), datetime(2026, 1, 10, 10, tzinfo=UTC)
    )

    assert confirmed is not None
    assert confirmed.source is StatusSource.CONFIRMED
    assert confirmed.summary == expected
    # A confirmed status never says it wasn't confirmed, nor carries the
    # non-response placeholder as a blocker.
    assert "No confirmed check-in" not in confirmed.summary
    assert NO_REPLY_BLOCKER not in confirmed.blockers
    # The days confirmed from are left as they were.
    for status in history:
        if status.as_of < date(2026, 1, 10):
            assert await service.my_status("demo", "dev-1", status.as_of) == status


async def test_self_status_confirm_writes_the_day_being_confirmed() -> None:
    """Confirming on a day with no reply must record *that* day.

    `my_status` answers with the most recent status at or before the day asked
    for, so on an unanswered day it returns an earlier one carried forward.
    Building the confirmation with `existing.as_of` rewrote that earlier day --
    leaving today with no status for any rollup to count, and restamping
    yesterday's record as confirmed just now.
    """
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 9),
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Friday's answer.",
            developer_confirmed=True,
            confirmed_at=datetime(2026, 1, 9, 9, tzinfo=UTC),
        )
    )
    service = SelfStatusService(store)

    confirmed = await service.confirm(
        "demo", "dev-1", date(2026, 1, 10), datetime(2026, 1, 10, 10, tzinfo=UTC)
    )

    assert confirmed is not None
    assert confirmed.as_of == date(2026, 1, 10)
    # The earlier day is left exactly as it was.
    earlier = await service.my_status("demo", "dev-1", date(2026, 1, 9))
    assert earlier is not None
    assert earlier.as_of == date(2026, 1, 9)
    assert earlier.confirmed_at == datetime(2026, 1, 9, 9, tzinfo=UTC)


async def test_self_status_correct_writes_the_day_being_corrected() -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 9),
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Friday's answer.",
        )
    )
    service = SelfStatusService(store)

    corrected = await service.correct(
        "demo",
        "dev-1",
        date(2026, 1, 10),
        summary="Today's answer.",
        blockers=(),
        eta_change_days=None,
        confirmed_at=datetime(2026, 1, 10, 10, tzinfo=UTC),
    )

    assert corrected is not None
    assert corrected.as_of == date(2026, 1, 10)
    assert corrected.summary == "Today's answer."
    earlier = await service.my_status("demo", "dev-1", date(2026, 1, 9))
    assert earlier is not None
    assert earlier.summary == "Friday's answer."


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
