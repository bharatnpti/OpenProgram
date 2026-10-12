"""Why the date moved: one day's change of the history forecast, taken apart.

Two days of a scope in a row that kept a snapshot, ``a`` and ``b``, each have a
history forecast ``F(R, W, start, seed)``: the remaining work ``R`` replayed over
the window's samples ``W`` from ``start``, under the day's seed. The move from
a's date to b's is replayed one input at a time, every replay under b's seed
(common random numbers), in this fixed order:

=====  ==============================  =====================================
step   replay                          driver
=====  ==============================  =====================================
c0     F(R_a, W_a, a, seed_a)          the date on day a, as kept
c0'    F(R_a, W_a, a, seed_b)          rounding: the seed alone
c1     F(R_a, W_a, b, seed_b)          time passed
c2     c1 with R - finished            work finished
c3     c2 with + added                 scope added, split per item
c4     c3 with - removed               scope removed
c5     c4 with + reopened              reopened
c6     c5 with re-estimates            re-estimated (story points only)
c7     F(R_b, W_b, b, seed_b)          throughput: the date on day b, as kept
=====  ==============================  =====================================

Each step is the working days between two replays in a row, so the steps add
up to the real move exactly, for the 50% and the 85% date alike. A period sums
its days. Scope added, removed and reopened are shared among their items:
equally when counting requirements, by story points when the forecast counts
points. A day either side of which there was no forecast is not taken apart;
its items are still listed, as the scope ledger.

Everything here is about work items and the forecast's inputs. Nothing names
or counts a person.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum

from core.domain.delivery import DeliveryStage
from core.domain.forecast import SIMULATION_RUNS, history_forecast, is_working_day

#: The attribution's payload shape in forecast_days.attribution.
ATTRIBUTION_VERSION = 1
POINTS_UNIT = "story points"


class Driver(StrEnum):
    ROUNDING = "rounding"
    TIME_PASSED = "time_passed"
    WORK_FINISHED = "work_finished"
    SCOPE_ADDED = "scope_added"
    SCOPE_REMOVED = "scope_removed"
    REOPENED = "reopened"
    RE_ESTIMATED = "re_estimated"
    THROUGHPUT = "throughput"
    #: A period only: days that kept no breakdown, so their part of the move is not known.
    NOT_ATTRIBUTED = "not_attributed"


#: The replay order, which every day's steps follow.
DAY_DRIVERS: tuple[Driver, ...] = (
    Driver.ROUNDING,
    Driver.TIME_PASSED,
    Driver.WORK_FINISHED,
    Driver.SCOPE_ADDED,
    Driver.SCOPE_REMOVED,
    Driver.REOPENED,
    Driver.RE_ESTIMATED,
    Driver.THROUGHPUT,
)


class Measure(StrEnum):
    """Which forecast date a move is read on: the 85% date, as the verdict reads it."""

    P85 = "p85"
    P50 = "p50"


class NotTakenApart(StrEnum):
    """Why a day's move has no breakdown."""

    NO_FORECAST_BEFORE = "no_forecast_before"
    NO_FORECAST_AFTER = "no_forecast_after"
    UNIT_CHANGED = "unit_changed"
    #: The earlier day's window no longer replays to a forecast (its snapshots changed since).
    REPLAY_DIFFERS = "replay_differs"


class ScopeGroup(StrEnum):
    """What kind of addition a requirement that joined the scope was."""

    #: It carries the change label: a change agreed after planning.
    CHANGE_REQUEST = "change_request"
    #: Joined after planning (and its grace days) without the change label.
    NO_LABEL = "no_label"
    #: A bug, counted as a requirement.
    BUG = "bug"
    #: It existed before it joined: moved in from another release or project.
    MOVED_IN = "moved_in"
    #: In the planning baseline, or entered within its grace days.
    PLANNED = "planned"
    #: Joined while no planning baseline was set, or before it.
    BEFORE_PLANNING = "before_planning"


@dataclass(frozen=True, kw_only=True)
class ScopeDay:
    """What one scope's forecast rested on, on one day with a snapshot."""

    day: date
    seed: str
    #: Each requirement in scope and its stage that day.
    stages: Mapping[str, DeliveryStage]
    titles: Mapping[str, str] = field(default_factory=dict)
    types: Mapping[str, str | None] = field(default_factory=dict)
    #: Story points per requirement, where known.
    points: Mapping[str, float | None] = field(default_factory=dict)
    #: Requirements (or points) finished on each working day of the window, oldest first.
    samples: tuple[tuple[date, float], ...] = ()
    remaining: float = 0.0
    unit: str = "requirements"
    min_sample_days: int = 10
    #: The forecast kept for the day (or computed when none was kept).
    p50: date | None = None
    p85: date | None = None

    @property
    def open_keys(self) -> frozenset[str]:
        return frozenset(
            key for key, stage in self.stages.items() if stage is not DeliveryStage.PRODUCTION
        )

    @property
    def done_keys(self) -> frozenset[str]:
        return frozenset(
            key for key, stage in self.stages.items() if stage is DeliveryStage.PRODUCTION
        )


@dataclass(frozen=True, kw_only=True)
class Shift:
    """Working days a date moved: later is positive."""

    p50: float = 0.0
    p85: float = 0.0

    def of(self, measure: Measure) -> float:
        return self.p85 if measure is Measure.P85 else self.p50

    def __add__(self, other: Shift) -> Shift:
        return Shift(p50=self.p50 + other.p50, p85=self.p85 + other.p85)

    def __sub__(self, other: Shift) -> Shift:
        return Shift(p50=self.p50 - other.p50, p85=self.p85 - other.p85)

    @property
    def zero(self) -> bool:
        return abs(self.p50) < 1e-9 and abs(self.p85) < 1e-9


@dataclass(frozen=True, kw_only=True)
class Item:
    """A requirement in one part of the scope's change, with its share of the move."""

    key: str
    title: str = ""
    type: str | None = None
    points: float | None = None
    share: Shift = Shift()
    #: The day it moved: the later day of the two compared.
    day: date | None = None
    #: The earlier day of the two: the last day the scope was seen without the move.
    since: date | None = None


@dataclass(frozen=True, kw_only=True)
class Reestimate:
    key: str
    title: str = ""
    before: float | None = None
    after: float | None = None
    day: date | None = None


@dataclass(frozen=True, kw_only=True)
class Step:
    driver: Driver
    shift: Shift


@dataclass(frozen=True, kw_only=True)
class Throughput:
    """How the window changed between the two days: which finishing days left and joined it."""

    left: tuple[tuple[date, float], ...] = ()
    joined: tuple[tuple[date, float], ...] = ()
    average_before: float | None = None
    average_after: float | None = None


@dataclass(frozen=True, kw_only=True)
class DayAttribution:
    """One day's move of the forecast, taken apart, and the scope's change behind it."""

    from_day: date
    to_day: date
    unit: str
    from_p50: date | None
    from_p85: date | None
    to_p50: date | None
    to_p85: date | None
    #: In DAY_DRIVERS order; empty when the move is not taken apart.
    steps: tuple[Step, ...] = ()
    not_taken_apart: NotTakenApart | None = None
    finished: tuple[Item, ...] = ()
    added: tuple[Item, ...] = ()
    removed: tuple[Item, ...] = ()
    reopened: tuple[Item, ...] = ()
    re_estimated: tuple[Reestimate, ...] = ()
    throughput: Throughput | None = None

    @property
    def taken_apart(self) -> bool:
        return self.not_taken_apart is None

    def step(self, driver: Driver) -> Shift:
        return next((item.shift for item in self.steps if item.driver is driver), Shift())


def working_day_index(day: date) -> int:
    """Working days from a fixed Monday up to ``day``; a weekend counts as its Friday.

    The difference of two indexes is the working days between the dates, signed,
    and differences add up: a run of steps sums to its ends' difference.
    """
    elapsed = day.toordinal() - 1  # date(1, 1, 1) is a Monday
    weeks, rest = divmod(elapsed, 7)
    return weeks * 5 + min(rest + 1, 5)


def working_days_moved(before: date, after: date) -> int:
    """Signed working days from ``before`` to ``after``: later is positive."""
    return working_day_index(after) - working_day_index(before)


def add_working_days(day: date, count: int) -> date:
    """The working day ``count`` working days after ``day``."""
    current = day
    added = 0
    while added < count:
        current += timedelta(days=1)
        if is_working_day(current):
            added += 1
    return current


def decompose(before: ScopeDay, after: ScopeDay, *, runs: int = SIMULATION_RUNS) -> DayAttribution:
    """Take the move from ``before``'s forecast to ``after``'s apart, by the fixed order."""
    points = after.unit == POINTS_UNIT
    finished = sorted(
        (k for k in before.open_keys if after.stages.get(k) is DeliveryStage.PRODUCTION),
        key=key_order,
    )
    added = sorted(
        (k for k in after.open_keys if k not in before.stages),
        key=key_order,
    )
    removed = sorted((k for k in before.open_keys if k not in after.stages), key=key_order)
    reopened = sorted(
        (k for k in before.done_keys if k in after.open_keys),
        key=key_order,
    )
    still_open = before.open_keys & after.open_keys
    re_estimated = (
        sorted(
            (k for k in still_open if _size(before, k, True) != _size(after, k, True)),
            key=key_order,
        )
        if points and before.unit == POINTS_UNIT
        else []
    )
    base = DayAttribution(
        from_day=before.day,
        to_day=after.day,
        unit=after.unit,
        from_p50=before.p50,
        from_p85=before.p85,
        to_p50=after.p50,
        to_p85=after.p85,
        finished=tuple(_item(before, key, before.day, after.day) for key in finished),
        added=tuple(_item(after, key, before.day, after.day) for key in added),
        removed=tuple(_item(before, key, before.day, after.day) for key in removed),
        reopened=tuple(_item(after, key, before.day, after.day) for key in reopened),
        re_estimated=tuple(
            Reestimate(
                key=key,
                title=after.titles.get(key, ""),
                before=before.points.get(key),
                after=after.points.get(key),
                day=after.day,
            )
            for key in re_estimated
        ),
        throughput=_throughput(before, after),
    )
    first50, first85, last50, last85 = before.p50, before.p85, after.p50, after.p85
    if first50 is None or first85 is None:
        return replace(base, not_taken_apart=NotTakenApart.NO_FORECAST_BEFORE)
    if last50 is None or last85 is None:
        return replace(base, not_taken_apart=NotTakenApart.NO_FORECAST_AFTER)
    if before.unit != after.unit:
        return replace(base, not_taken_apart=NotTakenApart.UNIT_CHANGED)

    samples = [value for _day, value in before.samples]

    def replay(remaining: float, start: date) -> tuple[date, date] | None:
        forecast = history_forecast(
            samples,
            remaining,
            start=start,
            seed=after.seed,
            unit=before.unit,
            runs=runs,
            min_sample_days=before.min_sample_days,
        )
        if forecast.p50 is None or forecast.p85 is None:
            return None
        return forecast.p50, forecast.p85

    def size(state: ScopeDay, keys: Iterable[str]) -> float:
        return sum(_size(state, key, points) for key in keys)

    remaining_finished = before.remaining - size(before, finished)
    remaining_added = remaining_finished + size(after, added)
    remaining_removed = remaining_added - size(before, removed)
    remaining_reopened = remaining_removed + size(after, reopened)
    plan: list[tuple[Driver, float, date]] = [
        (Driver.ROUNDING, before.remaining, before.day),
        (Driver.TIME_PASSED, before.remaining, after.day),
        (Driver.WORK_FINISHED, remaining_finished, after.day),
        (Driver.SCOPE_ADDED, remaining_added, after.day),
        (Driver.SCOPE_REMOVED, remaining_removed, after.day),
        (Driver.REOPENED, remaining_reopened, after.day),
        # The day's own remaining work: re-estimates, and anything the item sizes miss.
        (Driver.RE_ESTIMATED, after.remaining, after.day),
    ]
    dates: list[tuple[date, date]] = [(first50, first85)]
    cache: dict[tuple[float, date], tuple[date, date]] = {}
    for _driver, remaining, start in plan:
        key = (round(remaining, 6), start)
        if key not in cache:
            result = replay(remaining, start)
            if result is None:
                return replace(base, not_taken_apart=NotTakenApart.REPLAY_DIFFERS)
            cache[key] = result
        dates.append(cache[key])
    dates.append((last50, last85))
    drivers = [driver for driver, _remaining, _start in plan] + [Driver.THROUGHPUT]
    steps = tuple(
        Step(
            driver=driver,
            shift=Shift(
                p50=float(working_days_moved(dates[index][0], dates[index + 1][0])),
                p85=float(working_days_moved(dates[index][1], dates[index + 1][1])),
            ),
        )
        for index, driver in enumerate(drivers)
    )
    by_driver = {step.driver: step.shift for step in steps}
    return replace(
        base,
        steps=steps,
        added=_shared(base.added, by_driver[Driver.SCOPE_ADDED], points),
        removed=_shared(base.removed, by_driver[Driver.SCOPE_REMOVED], points),
        reopened=_shared(base.reopened, by_driver[Driver.REOPENED], points),
    )


def _size(state: ScopeDay, key: str, points: bool) -> float:
    if not points:
        return 1.0
    value = state.points.get(key)
    return float(value) if value is not None else 0.0


def _item(state: ScopeDay, key: str, since: date, day: date) -> Item:
    return Item(
        key=key,
        title=state.titles.get(key, ""),
        type=state.types.get(key),
        points=state.points.get(key),
        day=day,
        since=since,
    )


def _shared(items: tuple[Item, ...], shift: Shift, points: bool) -> tuple[Item, ...]:
    """The step's working days shared among its items: equally, or by story points."""
    if not items:
        return items
    weights = [float(item.points or 0.0) if points else 1.0 for item in items]
    total = sum(weights)
    if total <= 0:
        weights = [1.0] * len(items)
        total = float(len(items))
    return tuple(
        replace(
            item,
            share=Shift(
                p50=round(shift.p50 * weight / total, 3),
                p85=round(shift.p85 * weight / total, 3),
            ),
        )
        for item, weight in zip(items, weights, strict=True)
    )


def _throughput(before: ScopeDay, after: ScopeDay) -> Throughput:
    before_days = {day for day, _value in before.samples}
    after_days = {day for day, _value in after.samples}
    return Throughput(
        left=tuple(sample for sample in before.samples if sample[0] not in after_days),
        joined=tuple(sample for sample in after.samples if sample[0] not in before_days),
        average_before=_average(before.samples),
        average_after=_average(after.samples),
    )


def _average(samples: Sequence[tuple[date, float]]) -> float | None:
    if not samples:
        return None
    return round(sum(value for _day, value in samples) / len(samples), 3)


# --- The payload kept in forecast_days.attribution ------------------------------------


def attribution_payload(attribution: DayAttribution) -> dict[str, object]:
    """The attribution as the JSON forecast_days keeps: keys, titles, types, numbers, dates."""
    return {
        "version": ATTRIBUTION_VERSION,
        "from_day": attribution.from_day.isoformat(),
        "to_day": attribution.to_day.isoformat(),
        "unit": attribution.unit,
        "from": {"p50": _iso(attribution.from_p50), "p85": _iso(attribution.from_p85)},
        "to": {"p50": _iso(attribution.to_p50), "p85": _iso(attribution.to_p85)},
        "not_taken_apart": (
            attribution.not_taken_apart.value if attribution.not_taken_apart else None
        ),
        "steps": [
            {"driver": step.driver.value, "p50": step.shift.p50, "p85": step.shift.p85}
            for step in attribution.steps
        ],
        "finished": [_item_payload(item) for item in attribution.finished],
        "added": [_item_payload(item) for item in attribution.added],
        "removed": [_item_payload(item) for item in attribution.removed],
        "reopened": [_item_payload(item) for item in attribution.reopened],
        "re_estimated": [
            {"key": item.key, "title": item.title, "from": item.before, "to": item.after}
            for item in attribution.re_estimated
        ],
        "throughput": (
            {
                "left": [[day.isoformat(), value] for day, value in attribution.throughput.left],
                "joined": [
                    [day.isoformat(), value] for day, value in attribution.throughput.joined
                ],
                "average_before": attribution.throughput.average_before,
                "average_after": attribution.throughput.average_after,
            }
            if attribution.throughput is not None
            else None
        ),
    }


def attribution_from_payload(payload: Mapping[str, object] | None) -> DayAttribution | None:
    """The attribution a forecast_days row keeps; None for none or another version."""
    if not payload or payload.get("version") != ATTRIBUTION_VERSION:
        return None
    from_day = _date(payload.get("from_day"))
    to_day = _date(payload.get("to_day"))
    if from_day is None or to_day is None:
        return None
    start = _mapping(payload.get("from"))
    end = _mapping(payload.get("to"))
    reason = payload.get("not_taken_apart")
    throughput = _mapping(payload.get("throughput"))
    return DayAttribution(
        from_day=from_day,
        to_day=to_day,
        unit=str(payload.get("unit") or "requirements"),
        from_p50=_date(start.get("p50")),
        from_p85=_date(start.get("p85")),
        to_p50=_date(end.get("p50")),
        to_p85=_date(end.get("p85")),
        steps=tuple(
            Step(
                driver=Driver(str(step.get("driver"))),
                shift=Shift(p50=_float(step.get("p50")), p85=_float(step.get("p85"))),
            )
            for step in map(_mapping, _list(payload.get("steps")))
            if step.get("driver") in Driver._value2member_map_
        ),
        not_taken_apart=(
            NotTakenApart(reason)
            if isinstance(reason, str) and reason in NotTakenApart._value2member_map_
            else None
        ),
        finished=_items(payload.get("finished"), from_day, to_day),
        added=_items(payload.get("added"), from_day, to_day),
        removed=_items(payload.get("removed"), from_day, to_day),
        reopened=_items(payload.get("reopened"), from_day, to_day),
        re_estimated=tuple(
            Reestimate(
                key=str(item.get("key")),
                title=str(item.get("title") or ""),
                before=_optional_float(item.get("from")),
                after=_optional_float(item.get("to")),
                day=to_day,
            )
            for item in map(_mapping, _list(payload.get("re_estimated")))
            if item.get("key")
        ),
        throughput=(
            Throughput(
                left=_samples(throughput.get("left")),
                joined=_samples(throughput.get("joined")),
                average_before=_optional_float(throughput.get("average_before")),
                average_after=_optional_float(throughput.get("average_after")),
            )
            if throughput
            else None
        ),
    )


def _item_payload(item: Item) -> dict[str, object]:
    return {
        "key": item.key,
        "title": item.title,
        "type": item.type,
        "points": item.points,
        "p50": item.share.p50,
        "p85": item.share.p85,
    }


def _items(value: object, since: date, day: date) -> tuple[Item, ...]:
    return tuple(
        Item(
            key=str(item.get("key")),
            title=str(item.get("title") or ""),
            type=_text(item.get("type")),
            points=_optional_float(item.get("points")),
            share=Shift(p50=_float(item.get("p50")), p85=_float(item.get("p85"))),
            day=day,
            since=since,
        )
        for item in map(_mapping, _list(value))
        if item.get("key")
    )


def _samples(value: object) -> tuple[tuple[date, float], ...]:
    found: list[tuple[date, float]] = []
    for pair in _list(value):
        if isinstance(pair, list | tuple) and len(pair) == 2:
            day = _date(pair[0])
            if day is not None:
                found.append((day, _float(pair[1])))
    return tuple(found)


# --- A period: the days summed --------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class KeptDay:
    """One kept forecast_days row: the day's forecast and its breakdown, if any."""

    day: date
    p50: date | None
    p85: date | None
    attribution: DayAttribution | None
    reconstructed: bool = False


@dataclass(frozen=True, kw_only=True)
class DriverTotal:
    driver: Driver
    shift: Shift


@dataclass(frozen=True, kw_only=True)
class PeriodAttribution:
    """The days of a period summed: from the first day's forecast to the last's."""

    #: The day the period is read from (its forecast is "then"), and the last day.
    start: date
    end: date
    from_p50: date | None
    from_p85: date | None
    to_p50: date | None
    to_p85: date | None
    #: Working days the forecast moved; None while either end has no forecast.
    moved: Shift | None
    unit: str | None
    #: Non-zero drivers in DAY_DRIVERS order, NOT_ATTRIBUTED last.
    drivers: tuple[DriverTotal, ...] = ()
    finished: tuple[Item, ...] = ()
    added: tuple[Item, ...] = ()
    removed: tuple[Item, ...] = ()
    reopened: tuple[Item, ...] = ()
    re_estimated: tuple[Reestimate, ...] = ()
    #: Windows' finishing days that left and joined over the period.
    throughput_left: tuple[tuple[date, float], ...] = ()
    throughput_joined: tuple[tuple[date, float], ...] = ()
    average_before: float | None = None
    average_after: float | None = None
    #: The first day the forecast gave dates, when the period began without one.
    forecast_began: date | None = None
    #: Days in the period whose part of the move is not known (no breakdown kept).
    unknown_days: tuple[date, ...] = ()
    #: Days read from a reconstruction rather than kept on the day.
    reconstructed_days: int = 0

    def driver(self, driver: Driver) -> Shift:
        return next((item.shift for item in self.drivers if item.driver is driver), Shift())


def summed(days: Sequence[KeptDay]) -> PeriodAttribution | None:
    """The period from the first of ``days`` to the last, its days' breakdowns summed.

    The first day is the period's start: its forecast is the date "then". Each
    later day's breakdown runs from the day before it with a snapshot, so the
    days chain; a day that kept none, or does not follow on, leaves its part of
    the move unknown, shown as not attributed so the drivers still add up. A
    period that began without a forecast is read from the first day with one;
    the items of the days before it are still listed.
    """
    ordered = sorted(days, key=lambda item: item.day)
    if not ordered:
        return None
    anchor = next((i for i, row in enumerate(ordered) if row.p85 is not None), None)
    ledger = _Ledger()
    for index in range(1, len(ordered)):
        row, attribution = ordered[index], ordered[index].attribution
        counted = anchor is not None and index > anchor
        if attribution is not None:
            ledger.take_items(attribution)
        if not counted:
            continue
        if (
            attribution is None
            or attribution.from_day != ordered[index - 1].day
            or not attribution.taken_apart
        ):
            ledger.unknown.append(row.day)
            continue
        ledger.take_steps(attribution)
    start = ordered[anchor] if anchor is not None else ordered[0]
    last = ordered[-1]
    moved = _moved(start, last) if anchor is not None else None
    if moved is not None:
        residual = moved - sum(ledger.totals.values(), Shift())
        if not residual.zero:
            ledger.totals[Driver.NOT_ATTRIBUTED] = residual
    return PeriodAttribution(
        start=start.day,
        end=last.day,
        from_p50=start.p50,
        from_p85=start.p85,
        to_p50=last.p50,
        to_p85=last.p85,
        moved=moved,
        unit=ledger.unit,
        drivers=ledger.drivers() if moved is not None else (),
        finished=_sorted_items(ledger.finished),
        added=_sorted_items(ledger.added),
        removed=_sorted_items(ledger.removed),
        reopened=_sorted_items(ledger.reopened),
        re_estimated=tuple(
            sorted(ledger.re_estimated.values(), key=lambda item: key_order(item.key))
        ),
        throughput_left=tuple(sorted(set(ledger.left))),
        throughput_joined=tuple(sorted(set(ledger.joined))),
        average_before=ledger.averages[0][0] if ledger.averages else None,
        average_after=ledger.averages[-1][1] if ledger.averages else None,
        forecast_began=start.day if anchor else None,
        unknown_days=tuple(ledger.unknown),
        reconstructed_days=sum(1 for row in ordered if row.reconstructed),
    )


def _moved(start: KeptDay, last: KeptDay) -> Shift | None:
    if start.p50 is None or start.p85 is None or last.p50 is None or last.p85 is None:
        return None
    return Shift(
        p50=float(working_days_moved(start.p50, last.p50)),
        p85=float(working_days_moved(start.p85, last.p85)),
    )


@dataclass
class _Ledger:
    """What a period's days add up to, kept as they are read."""

    totals: dict[Driver, Shift] = field(default_factory=dict)
    unknown: list[date] = field(default_factory=list)
    finished: dict[str, Item] = field(default_factory=dict)
    added: dict[str, Item] = field(default_factory=dict)
    removed: dict[str, Item] = field(default_factory=dict)
    reopened: dict[str, Item] = field(default_factory=dict)
    re_estimated: dict[str, Reestimate] = field(default_factory=dict)
    left: list[tuple[date, float]] = field(default_factory=list)
    joined: list[tuple[date, float]] = field(default_factory=list)
    averages: list[tuple[float | None, float | None]] = field(default_factory=list)
    unit: str | None = None

    def take_items(self, attribution: DayAttribution) -> None:
        _merge_items(self.finished, attribution.finished)
        _merge_items(self.added, attribution.added)
        _merge_items(self.removed, attribution.removed)
        _merge_items(self.reopened, attribution.reopened)
        for item in attribution.re_estimated:
            earlier = self.re_estimated.get(item.key)
            self.re_estimated[item.key] = (
                replace(item, before=earlier.before) if earlier is not None else item
            )

    def take_steps(self, attribution: DayAttribution) -> None:
        self.unit = attribution.unit
        for step in attribution.steps:
            self.totals[step.driver] = self.totals.get(step.driver, Shift()) + step.shift
        if attribution.throughput is not None:
            self.left.extend(attribution.throughput.left)
            self.joined.extend(attribution.throughput.joined)
            self.averages.append(
                (attribution.throughput.average_before, attribution.throughput.average_after)
            )

    def drivers(self) -> tuple[DriverTotal, ...]:
        return tuple(
            DriverTotal(driver=driver, shift=_rounded(self.totals[driver]))
            for driver in (*DAY_DRIVERS, Driver.NOT_ATTRIBUTED)
            if driver in self.totals and not self.totals[driver].zero
        )


def _merge_items(found: dict[str, Item], items: Iterable[Item]) -> None:
    """Each key once: its shares summed, the first day it moved kept."""
    for item in items:
        earlier = found.get(item.key)
        found[item.key] = (
            replace(earlier, share=earlier.share + item.share) if earlier is not None else item
        )


def _sorted_items(found: Mapping[str, Item]) -> tuple[Item, ...]:
    return tuple(
        replace(item, share=_rounded(item.share))
        for item in sorted(found.values(), key=lambda item: key_order(item.key))
    )


def _rounded(shift: Shift) -> Shift:
    return Shift(p50=round(shift.p50, 2), p85=round(shift.p85, 2))


# --- What kind of addition ------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PlanningRule:
    """The planning baseline an addition is read against; no baseline, no planned day."""

    change_label: str
    planned_on: date | None = None
    grace_working_days: int = 0
    baseline_keys: frozenset[str] = frozenset()


def scope_group(
    *,
    key: str,
    issue_type: str | None,
    labels: Iterable[str],
    joined_on: date,
    existed_before: bool,
    rule: PlanningRule,
) -> ScopeGroup:
    """What an addition was: the change label first, then a bug, then moved in, then planning.

    ``existed_before`` says the requirement was there before it joined the scope:
    another release's, another project's, or not yet counted.
    """
    wanted = rule.change_label.casefold()
    if any(label.casefold() == wanted for label in labels):
        return ScopeGroup.CHANGE_REQUEST
    if issue_type is not None and issue_type.strip().casefold() == "bug":
        return ScopeGroup.BUG
    if existed_before:
        return ScopeGroup.MOVED_IN
    if rule.planned_on is None or joined_on < rule.planned_on:
        return ScopeGroup.BEFORE_PLANNING
    if key in rule.baseline_keys or joined_on <= add_working_days(
        rule.planned_on, rule.grace_working_days
    ):
        return ScopeGroup.PLANNED
    return ScopeGroup.NO_LABEL


def key_order(key: str) -> tuple[str, int, str]:
    """CHK-2 before CHK-10: an issue key's project, then its number."""
    head, _, tail = key.rpartition("-")
    return (head, int(tail), key) if tail.isdigit() else (key, 0, key)


# --- small readers -------------------------------------------------------------------


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day is not None else None


def _date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []


def _float(value: object) -> float:
    found = _optional_float(value)
    return found if found is not None else 0.0


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
