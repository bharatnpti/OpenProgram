from __future__ import annotations

from datetime import date

from core.domain.graph import Program
from core.domain.rollup import NodeStatus, Rag
from core.domain.status import StatusSource
from infra.workflows.rollup import _days_to_record
from tests.contract.fakes import FakeRollupRepository

_PROGRAM = Program(tenant_id="demo", id="program-1", name="Programme")
# Friday 25 September 2026.
_FRIDAY = date(2026, 9, 25)


async def test_rollup_recomputes_today_even_though_it_is_already_recorded() -> None:
    """Check-ins land all day, so today is rolled up again on every tick."""
    rollups = FakeRollupRepository()
    # Every weekday in the window, today included, is already on file.
    for day in (
        date(2026, 9, 21),
        date(2026, 9, 22),
        date(2026, 9, 23),
        date(2026, 9, 24),
        _FRIDAY,
    ):
        await rollups.record_node_status(_recorded(day))

    days = await _days_to_record(rollups, _PROGRAM, _FRIDAY, backfill_days=4)

    assert days == [_FRIDAY]


async def test_rollup_backfills_a_weekday_that_was_never_recorded() -> None:
    """History should not depend on who happened to open a screen that day."""
    rollups = FakeRollupRepository()
    for day in (date(2026, 9, 22), date(2026, 9, 23), _FRIDAY):
        await rollups.record_node_status(_recorded(day))

    days = await _days_to_record(rollups, _PROGRAM, _FRIDAY, backfill_days=4)

    # 21st is the Monday, 24th the Thursday: both weekdays, neither recorded.
    assert days == [date(2026, 9, 21), date(2026, 9, 24), _FRIDAY]


async def test_rollup_backfill_skips_weekends() -> None:
    rollups = FakeRollupRepository()
    await rollups.record_node_status(_recorded(_FRIDAY))

    days = await _days_to_record(rollups, _PROGRAM, _FRIDAY, backfill_days=3)

    # 22nd/23rd/24th are Tue-Thu; the 26th and 27th are the weekend and are
    # outside the window anyway.
    assert days == [date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24), _FRIDAY]
    assert all(day.weekday() < 5 for day in days)


async def test_rollup_backfill_window_of_zero_is_today_only() -> None:
    rollups = FakeRollupRepository()

    days = await _days_to_record(rollups, _PROGRAM, _FRIDAY, backfill_days=0)

    assert days == [_FRIDAY]


def _recorded(as_of: date) -> NodeStatus:
    return NodeStatus(
        entity_ref=_PROGRAM.ref,
        as_of=as_of,
        rag=Rag.GREEN,
        source=StatusSource.CONFIRMED,
        factors=(),
    )
