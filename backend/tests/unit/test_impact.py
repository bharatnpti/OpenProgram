"""Why the date moved, taken apart: the replay order, its exact sums, and the words.

Pure domain (core/domain/impact.py, impact_words.py). Made-up keys and days.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from core.domain.delivery import DeliveryStage
from core.domain.forecast import history_forecast
from core.domain.impact import (
    DAY_DRIVERS,
    Driver,
    KeptDay,
    Measure,
    NotTakenApart,
    PlanningRule,
    ScopeDay,
    ScopeGroup,
    add_working_days,
    attribution_from_payload,
    attribution_payload,
    decompose,
    scope_group,
    summed,
    working_days_moved,
)
from core.domain.impact_words import answer_line, day_line, moved_words, signed

S = DeliveryStage
THURSDAY = date(2026, 10, 8)
FRIDAY = date(2026, 10, 9)
MONDAY = date(2026, 10, 12)


def _samples(end: date, values: list[float]) -> tuple[tuple[date, float], ...]:
    """Working-day samples ending the day before ``end``, oldest first."""
    days: list[date] = []
    day = end
    while len(days) < len(values):
        day -= timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return tuple(zip(reversed(days), values, strict=True))


def _day(
    day: date,
    stages: dict[str, DeliveryStage],
    samples: tuple[tuple[date, float], ...],
    *,
    points: dict[str, float] | None = None,
    seed: str | None = None,
) -> ScopeDay:
    """A scope day with its forecast replayed as the forecast service replays it."""
    unit = "story points" if points is not None else "requirements"
    remaining = (
        sum(points.get(key, 0.0) for key, stage in stages.items() if stage is not S.PRODUCTION)
        if points is not None
        else float(sum(1 for stage in stages.values() if stage is not S.PRODUCTION))
    )
    seed = seed or f"project:checkout:checkout:{day.isoformat()}"
    forecast = history_forecast(
        [v for _d, v in samples], remaining, start=day, seed=seed, unit=unit
    )
    return ScopeDay(
        day=day,
        seed=seed,
        stages=stages,
        titles={key: f"{key} work" for key in stages},
        types={key: "Story" for key in stages},
        points=dict(points or {}),
        samples=samples,
        remaining=remaining,
        unit=unit,
        p50=forecast.p50,
        p85=forecast.p85,
    )


WINDOW = [1.0, 0.0, 0.0, 1.0, 0.0, 2.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0]


def _before_after() -> tuple[ScopeDay, ScopeDay]:
    before_stages = {f"CHK-{n}": S.IN_DEVELOPMENT for n in range(1, 9)}
    before_stages["CHK-9"] = S.PRODUCTION
    after_stages = dict(before_stages)
    after_stages["CHK-1"] = S.PRODUCTION  # finished
    after_stages["CHK-20"] = S.RAISED  # added
    after_stages["CHK-21"] = S.RAISED  # added
    del after_stages["CHK-5"]  # removed
    after_stages["CHK-9"] = S.IN_TESTING  # reopened
    before = _day(THURSDAY, before_stages, _samples(THURSDAY, WINDOW))
    after = _day(FRIDAY, after_stages, _samples(FRIDAY, WINDOW[1:] + [1.0]))
    return before, after


# --- The replay ------------------------------------------------------------------------


def test_the_steps_add_up_to_the_real_move_on_both_dates() -> None:
    before, after = _before_after()

    attribution = decompose(before, after)

    assert attribution.taken_apart
    assert [step.driver for step in attribution.steps] == list(DAY_DRIVERS)
    assert before.p85 and after.p85 and before.p50 and after.p50
    assert sum(step.shift.p85 for step in attribution.steps) == working_days_moved(
        before.p85, after.p85
    )
    assert sum(step.shift.p50 for step in attribution.steps) == working_days_moved(
        before.p50, after.p50
    )


@pytest.mark.parametrize("seed", range(12))
def test_the_steps_add_up_for_any_change_of_scope_and_window(seed: int) -> None:
    rng = random.Random(seed)
    before_stages = {
        f"CHK-{n}": rng.choice([S.RAISED, S.IN_DEVELOPMENT, S.PRODUCTION]) for n in range(1, 15)
    }
    after_stages = {
        key: rng.choice([stage, S.PRODUCTION, S.IN_TESTING])
        for key, stage in before_stages.items()
        if rng.random() > 0.15
    }
    for n in range(20, 20 + rng.randint(0, 4)):
        after_stages[f"CHK-{n}"] = S.RAISED
    window = [float(rng.randint(0, 3)) for _ in range(14)]
    before = _day(THURSDAY, before_stages, _samples(THURSDAY, window))
    after = _day(FRIDAY, after_stages, _samples(FRIDAY, window[1:] + [float(rng.randint(0, 2))]))

    attribution = decompose(before, after, runs=300)

    if not attribution.taken_apart:
        return
    assert before.p85 and after.p85
    assert sum(step.shift.p85 for step in attribution.steps) == working_days_moved(
        before.p85, after.p85
    )


def test_the_partition_names_what_finished_joined_left_and_reopened() -> None:
    before, after = _before_after()

    attribution = decompose(before, after)

    assert [item.key for item in attribution.finished] == ["CHK-1"]
    assert [item.key for item in attribution.added] == ["CHK-20", "CHK-21"]
    assert [item.key for item in attribution.removed] == ["CHK-5"]
    assert [item.key for item in attribution.reopened] == ["CHK-9"]
    # Counting requirements, the added scope is shared equally.
    shares = {item.share.p85 for item in attribution.added}
    assert len(shares) == 1
    assert sum(item.share.p85 for item in attribution.added) == pytest.approx(
        attribution.step(Driver.SCOPE_ADDED).p85, abs=0.01
    )
    assert all(item.since == THURSDAY and item.day == FRIDAY for item in attribution.added)


def test_a_day_with_nothing_changed_moves_by_time_and_the_window_only() -> None:
    stages = {"CHK-1": S.IN_DEVELOPMENT, "CHK-2": S.RAISED}
    before = _day(THURSDAY, stages, _samples(THURSDAY, WINDOW))
    after = _day(FRIDAY, dict(stages), _samples(FRIDAY, WINDOW[1:] + [0.0]))

    attribution = decompose(before, after)

    for driver in (
        Driver.WORK_FINISHED,
        Driver.SCOPE_ADDED,
        Driver.SCOPE_REMOVED,
        Driver.REOPENED,
        Driver.RE_ESTIMATED,
    ):
        assert attribution.step(driver).p85 == 0
    # Nothing finished: a day passed and the forecast starts a working day later.
    assert attribution.step(Driver.TIME_PASSED).p85 >= 0


def test_points_share_added_scope_by_size_and_re_estimates_are_their_own_driver() -> None:
    before_stages = {"CHK-1": S.IN_DEVELOPMENT, "CHK-2": S.IN_DEVELOPMENT}
    after_stages = {**before_stages, "CHK-20": S.RAISED, "CHK-21": S.RAISED}
    window = [3.0, 0.0, 5.0, 0.0, 2.0, 0.0, 3.0, 0.0, 0.0, 5.0, 2.0, 0.0]
    before = _day(
        THURSDAY, before_stages, _samples(THURSDAY, window), points={"CHK-1": 5, "CHK-2": 3}
    )
    after = _day(
        FRIDAY,
        after_stages,
        _samples(FRIDAY, window[1:] + [0.0]),
        points={"CHK-1": 8, "CHK-2": 3, "CHK-20": 13, "CHK-21": 1},
    )

    attribution = decompose(before, after)

    shares = {item.key: item.share.p85 for item in attribution.added}
    added = attribution.step(Driver.SCOPE_ADDED).p85
    assert shares["CHK-20"] == pytest.approx(added * 13 / 14, abs=0.01)
    assert shares["CHK-21"] == pytest.approx(added * 1 / 14, abs=0.01)
    assert [(item.key, item.before, item.after) for item in attribution.re_estimated] == [
        ("CHK-1", 5, 8)
    ]
    assert before.p85 and after.p85
    assert sum(step.shift.p85 for step in attribution.steps) == working_days_moved(
        before.p85, after.p85
    )


def test_a_day_without_a_forecast_on_either_side_is_listed_not_taken_apart() -> None:
    stages = {"CHK-1": S.IN_DEVELOPMENT}
    short = _day(THURSDAY, stages, _samples(THURSDAY, [1.0, 0.0]))
    after = _day(FRIDAY, {**stages, "CHK-20": S.RAISED}, _samples(FRIDAY, WINDOW))

    attribution = decompose(short, after)

    assert attribution.not_taken_apart is NotTakenApart.NO_FORECAST_BEFORE
    assert attribution.steps == ()
    assert [item.key for item in attribution.added] == ["CHK-20"]


def test_a_change_of_unit_is_not_taken_apart() -> None:
    stages = {"CHK-1": S.IN_DEVELOPMENT}
    before = _day(THURSDAY, stages, _samples(THURSDAY, WINDOW), points={"CHK-1": 3})
    after = _day(FRIDAY, dict(stages), _samples(FRIDAY, WINDOW))

    assert decompose(before, after).not_taken_apart is NotTakenApart.UNIT_CHANGED


def test_the_payload_reads_back_as_it_was_written() -> None:
    before, after = _before_after()
    attribution = decompose(before, after)

    payload = attribution_payload(attribution)

    assert attribution_from_payload(payload) == attribution
    assert attribution_from_payload({**payload, "version": 99}) is None
    assert attribution_from_payload(None) is None
    # Keys, titles, types, numbers and dates only.
    assert set(payload["added"][0]) == {"key", "title", "type", "points", "p50", "p85"}  # type: ignore[index]


# --- Working days -----------------------------------------------------------------------


def test_working_days_moved_counts_working_days_and_adds_up() -> None:
    saturday = date(2026, 10, 10)
    assert working_days_moved(THURSDAY, FRIDAY) == 1
    assert working_days_moved(FRIDAY, MONDAY) == 1
    assert working_days_moved(FRIDAY, saturday) == 0
    assert working_days_moved(MONDAY, THURSDAY) == -2
    days = [THURSDAY + timedelta(days=n) for n in range(0, 40, 3)]
    for first, middle, last in zip(days, days[1:], days[2:], strict=False):
        assert working_days_moved(first, middle) + working_days_moved(
            middle, last
        ) == working_days_moved(first, last)
    assert add_working_days(THURSDAY, 2) == MONDAY


# --- A period ---------------------------------------------------------------------------


def _chain() -> list[KeptDay]:
    """Three days in a row, each taken apart against the one before."""
    stages = {f"CHK-{n}": S.IN_DEVELOPMENT for n in range(1, 6)}
    wednesday = date(2026, 10, 7)
    days = [
        _day(wednesday, stages, _samples(wednesday, WINDOW)),
        _day(THURSDAY, {**stages, "CHK-20": S.RAISED}, _samples(THURSDAY, WINDOW[1:] + [1.0])),
        _day(
            FRIDAY,
            {**stages, "CHK-20": S.RAISED, "CHK-1": S.PRODUCTION},
            _samples(FRIDAY, WINDOW[2:] + [1.0, 1.0]),
        ),
    ]
    kept = [KeptDay(day=days[0].day, p50=days[0].p50, p85=days[0].p85, attribution=None)]
    for before, after in zip(days, days[1:], strict=False):
        kept.append(
            KeptDay(
                day=after.day, p50=after.p50, p85=after.p85, attribution=decompose(before, after)
            )
        )
    return kept


def test_a_period_sums_its_days_to_the_move_from_its_first_to_its_last() -> None:
    kept = _chain()

    period = summed(kept)

    assert period is not None and period.moved is not None
    assert (period.start, period.end) == (date(2026, 10, 7), FRIDAY)
    assert kept[0].p85 and kept[-1].p85
    assert period.moved.p85 == working_days_moved(kept[0].p85, kept[-1].p85)
    assert sum(driver.shift.p85 for driver in period.drivers) == pytest.approx(period.moved.p85)
    assert Driver.NOT_ATTRIBUTED not in {driver.driver for driver in period.drivers}
    assert [item.key for item in period.added] == ["CHK-20"]
    assert [item.key for item in period.finished] == ["CHK-1"]


def test_a_day_with_no_breakdown_leaves_its_part_not_attributed() -> None:
    kept = _chain()
    kept[1] = KeptDay(day=kept[1].day, p50=kept[1].p50, p85=kept[1].p85, attribution=None)

    period = summed(kept)

    assert period is not None and period.moved is not None
    assert period.unknown_days == (THURSDAY,)
    assert sum(driver.shift.p85 for driver in period.drivers) == pytest.approx(period.moved.p85)


def test_a_period_that_began_without_a_forecast_reads_from_its_first_one() -> None:
    kept = _chain()
    kept[0] = KeptDay(day=kept[0].day, p50=None, p85=None, attribution=None)

    period = summed(kept)

    assert period is not None
    assert period.forecast_began == THURSDAY and period.start == THURSDAY
    # The day before the forecast still lists its scope.
    assert [item.key for item in period.added] == ["CHK-20"]


# --- What kind of addition --------------------------------------------------------------


RULE = PlanningRule(
    change_label="CR",
    planned_on=date(2026, 10, 1),
    grace_working_days=2,
    baseline_keys=frozenset({"CHK-1"}),
)


@pytest.mark.parametrize(
    ("key", "issue_type", "labels", "joined_on", "existed", "group"),
    [
        ("CHK-20", "Story", ("cr",), date(2026, 10, 8), False, ScopeGroup.CHANGE_REQUEST),
        ("CHK-21", "Story", (), date(2026, 10, 8), False, ScopeGroup.NO_LABEL),
        ("CHK-22", "Bug", (), date(2026, 10, 8), False, ScopeGroup.BUG),
        ("CHK-23", "Story", (), date(2026, 10, 8), True, ScopeGroup.MOVED_IN),
        # Entered within the two grace working days after planning (Thu 1 Oct → Mon 5 Oct).
        ("CHK-24", "Story", (), date(2026, 10, 5), False, ScopeGroup.PLANNED),
        ("CHK-1", "Story", (), date(2026, 10, 9), False, ScopeGroup.PLANNED),
        ("CHK-25", "Story", (), date(2026, 9, 28), False, ScopeGroup.BEFORE_PLANNING),
    ],
)
def test_an_addition_is_grouped_by_label_type_origin_and_planning(
    key: str,
    issue_type: str,
    labels: tuple[str, ...],
    joined_on: date,
    existed: bool,
    group: ScopeGroup,
) -> None:
    assert (
        scope_group(
            key=key,
            issue_type=issue_type,
            labels=labels,
            joined_on=joined_on,
            existed_before=existed,
            rule=RULE,
        )
        is group
    )


def test_without_a_baseline_an_addition_is_before_planning_unless_labelled() -> None:
    rule = PlanningRule(change_label="CHANGE")
    assert (
        scope_group(
            key="CHK-1",
            issue_type="Story",
            labels=("change",),
            joined_on=MONDAY,
            existed_before=False,
            rule=rule,
        )
        is ScopeGroup.CHANGE_REQUEST
    )
    assert (
        scope_group(
            key="CHK-1",
            issue_type="Story",
            labels=(),
            joined_on=MONDAY,
            existed_before=False,
            rule=rule,
        )
        is ScopeGroup.BEFORE_PLANNING
    )


# --- The words --------------------------------------------------------------------------


def test_the_words_read_a_move_and_its_drivers_worst_first() -> None:
    kept = _chain()
    period = summed(kept)
    assert period is not None
    groups = {"CHK-20": ScopeGroup.NO_LABEL}

    line = answer_line(
        period,
        groups,
        measure=Measure.P85,
        change_label="CR",
        period_words="since planning (7 Oct)",
    )

    assert line.startswith("The 85% date ")
    assert "since planning (7 Oct)" in line
    assert "+3 from 1 requirement added (1 without the CR label)" in line
    assert signed(3) == "+3" and signed(-2) == "−2" and signed(0.5) == "+0.5"
    assert moved_words(1) == "moved 1 working day later"
    assert moved_words(-4) == "moved 4 working days earlier"
    assert moved_words(0) == "did not move"


def test_the_day_line_names_the_keys_and_is_silent_when_the_date_held() -> None:
    kept = _chain()
    period = summed(kept[1:])
    assert period is not None and period.moved is not None

    line = day_line(period, {"CHK-20": ScopeGroup.NO_LABEL}, change_label="CR")

    # Friday: CHK-1 finished.
    assert line is not None and line.startswith("The 85% date moved 4 working days earlier: ")
    assert "CHK-1 finished" in line
    stood = summed(
        [kept[0], KeptDay(day=FRIDAY, p50=kept[0].p50, p85=kept[0].p85, attribution=None)]
    )
    assert stood is not None
    assert day_line(stood, {}, change_label="CR") is None
