"""Why the date moved, in words: the drivers a leader reads, worst first.

Time passed and work finished read together as **pace**. Scope added is said
by its groups (change requests, additions without the change label, bugs,
moved in). Every part names work items, never people.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from core.domain.impact import (
    POINTS_UNIT,
    Driver,
    Item,
    Measure,
    PeriodAttribution,
    ScopeGroup,
    key_order,
)

#: How many keys a part names before it counts the rest.
MAX_KEYS_NAMED = 3
MINUS = "−"

#: Each driver as the waterfall's bar reads it.
DRIVER_LABELS: Mapping[Driver, str] = {
    # What is left of the forecast's rounding after it joins the largest driver: its hint.
    Driver.ROUNDING: "rounding",
    Driver.TIME_PASSED: "time passed",
    Driver.WORK_FINISHED: "work finished",
    Driver.SCOPE_ADDED: "scope added",
    Driver.SCOPE_REMOVED: "scope removed",
    Driver.REOPENED: "reopened",
    Driver.RE_ESTIMATED: "re-estimated",
    Driver.THROUGHPUT: "throughput",
    Driver.NOT_ATTRIBUTED: "days with no breakdown kept",
}

#: What leaders read: pace stands for time passed and work finished together.
PACE = "pace"
#: A rounding remainder too large to fold into another driver, in a sentence.
OTHER_WORDS = "other (rounding)"


def group_words(group: ScopeGroup, change_label: str, count: int = 1) -> str:
    """One addition group, worded: "2 change requests", "no change label"."""
    many = count != 1
    return {
        ScopeGroup.CHANGE_REQUEST: "change requests" if many else "change request",
        ScopeGroup.NO_LABEL: f"without the {change_label} label",
        ScopeGroup.BUG: "bugs" if many else "bug",
        ScopeGroup.MOVED_IN: "moved in",
        ScopeGroup.PLANNED: "planned",
        ScopeGroup.BEFORE_PLANNING: "before planning was complete",
    }[group]


def signed(value: float) -> str:
    """+3, −2, +0.5: working days, later positive."""
    number = _number(abs(value))
    if value > 0:
        return f"+{number}"
    if value < 0:
        return f"{MINUS}{number}"
    return "0"


def moved_words(value: float) -> str:
    """'moved 3 working days later', 'moved 1 working day earlier', 'did not move'."""
    if abs(value) < 0.5:
        return "did not move"
    days = _number(abs(value))
    unit = "working day" if days == "1" else "working days"
    return f"moved {days} {unit} {'later' if value > 0 else 'earlier'}"


def measure_words(measure: Measure) -> str:
    return "85% date" if measure is Measure.P85 else "50% date"


@dataclass(frozen=True, kw_only=True)
class Part:
    """One driver as a leader reads it: its working days and what it was."""

    kind: str
    value: float
    words: str


def parts(
    period: PeriodAttribution,
    groups: Mapping[str, ScopeGroup],
    *,
    measure: Measure,
    change_label: str,
    one_day: bool = False,
) -> list[Part]:
    """The period's drivers, pace first combined, worst (latest) first; zero ones left out.

    ``groups`` gives each added key's group. ``one_day`` words pace as a day
    does ("nothing finished") rather than as a period does ("slower finishing").
    """
    found: list[Part] = []
    pace = period.driver(Driver.TIME_PASSED).of(measure) + period.driver(Driver.WORK_FINISHED).of(
        measure
    )
    if abs(pace) >= 0.5:
        found.append(Part(kind=PACE, value=pace, words=_pace_words(period, pace, one_day)))
    added = period.driver(Driver.SCOPE_ADDED).of(measure)
    if abs(added) >= 0.5:
        found.append(
            Part(
                kind=Driver.SCOPE_ADDED.value,
                value=added,
                words=_added_words(period.added, groups, change_label, one_day),
            )
        )
    for driver, items, verb in (
        (Driver.SCOPE_REMOVED, period.removed, "removed"),
        (Driver.REOPENED, period.reopened, "reopened"),
    ):
        value = period.driver(driver).of(measure)
        if abs(value) >= 0.5:
            found.append(
                Part(kind=driver.value, value=value, words=_keys_words(items, verb, period.unit))
            )
    value = period.driver(Driver.RE_ESTIMATED).of(measure)
    if abs(value) >= 0.5:
        found.append(
            Part(
                kind=Driver.RE_ESTIMATED.value,
                value=value,
                words=_reestimate_words(period),
            )
        )
    for driver in (Driver.THROUGHPUT, Driver.ROUNDING, Driver.NOT_ATTRIBUTED):
        value = period.driver(driver).of(measure)
        if abs(value) >= 0.5:
            words = (
                throughput_words(period)
                if driver is Driver.THROUGHPUT
                else OTHER_WORDS
                if driver is Driver.ROUNDING
                else DRIVER_LABELS[driver]
            )
            found.append(Part(kind=driver.value, value=value, words=words))
    return sorted(found, key=lambda part: -part.value)


def throughput_words(period: PeriodAttribution) -> str:
    """'throughput (9 Oct joined the window with 0 finished; 18 Sep left it with 3; the
    average fell from 0.6 to 0.5 a day)'."""
    points = " points" if period.unit == POINTS_UNIT else ""
    said: list[str] = []
    joined, left = period.throughput_joined, period.throughput_left
    if joined:
        said.append(
            f"{_window_days(joined)} joined the window with "
            f"{_number(sum(value for _day, value in joined))}{points} finished"
        )
    if left:
        said.append(
            f"{_window_days(left)} left it with {_number(sum(value for _day, value in left))}"
        )
    before, after = period.average_before, period.average_after
    if before is not None and after is not None and round(before, 1) != round(after, 1):
        trend = "fell" if after < before else "rose"
        said.append(f"the average {trend} from {before:.1f} to {after:.1f} a day")
    return f"throughput ({'; '.join(said)})" if said else "throughput"


def _window_days(samples: Sequence[tuple[date, float]]) -> str:
    return _short_day(samples[0][0]) if len(samples) == 1 else f"{len(samples)} days"


def answer_line(
    period: PeriodAttribution,
    groups: Mapping[str, ScopeGroup],
    *,
    measure: Measure,
    change_label: str,
    period_words: str,
) -> str:
    """'The 85% date moved 9 working days later since planning: +6 from 4 requirements
    added (2 without the CR label), +3 from slower finishing.'"""
    moved = period.moved
    name = measure_words(measure)
    if moved is None:
        return f"No forecast to compare {period_words}: the {name} is not known at both ends."
    value = moved.of(measure)
    line = f"The {name} {moved_words(value)} {period_words}"
    listed = parts(period, groups, measure=measure, change_label=change_label)
    if not listed:
        return line + "."
    return (
        line + ": " + ", ".join(f"{signed(part.value)} from {part.words}" for part in listed) + "."
    )


def day_line(
    period: PeriodAttribution,
    groups: Mapping[str, ScopeGroup],
    *,
    change_label: str,
) -> str | None:
    """The day report's line, when the 85% date moved since the previous report:
    'The 85% date moved 3 working days later: +2 scope added (CHK-20, without the CR
    label), +1 nothing finished.'"""
    moved = period.moved
    if moved is None or abs(moved.p85) < 0.5:
        return None
    line = f"The 85% date {moved_words(moved.p85)}"
    listed = parts(period, groups, measure=Measure.P85, change_label=change_label, one_day=True)
    if not listed:
        return line + "."
    return line + ": " + ", ".join(f"{signed(part.value)} {part.words}" for part in listed) + "."


def _pace_words(period: PeriodAttribution, pace: float, one_day: bool) -> str:
    count = len(period.finished)
    if one_day:
        if count == 0:
            return "nothing finished"
        return f"{_keys(period.finished)} finished"
    trend = "slower finishing" if pace > 0 else "faster finishing"
    return f"{trend} ({count or 'nothing'} finished)"


def _added_words(
    items: Sequence[Item], groups: Mapping[str, ScopeGroup], change_label: str, one_day: bool
) -> str:
    """'scope added (CHK-20, without the CR label)' for a day; '4 requirements added
    (2 change requests, 2 without the CR label)' for a period."""
    by_group: dict[ScopeGroup, list[Item]] = {}
    for item in items:
        by_group.setdefault(groups.get(item.key, ScopeGroup.BEFORE_PLANNING), []).append(item)
    order = [group for group in ScopeGroup if group in by_group]
    if one_day:
        listed = "; ".join(
            f"{_keys(by_group[group])}, {group_words(group, change_label, len(by_group[group]))}"
            for group in order
        )
        return f"scope added ({listed})" if listed else "scope added"
    noun = "requirement" if len(items) == 1 else "requirements"
    counts = Counter({group: len(found) for group, found in by_group.items()})
    details = ", ".join(
        f"{counts[group]} {group_words(group, change_label, counts[group])}" for group in order
    )
    return f"{len(items)} {noun} added ({details})" if details else f"{len(items)} {noun} added"


def _keys_words(items: Sequence[Item], verb: str, unit: str | None) -> str:
    if not items:
        return verb
    if len(items) <= MAX_KEYS_NAMED:
        return f"{_keys(items)} {verb}"
    return f"{len(items)} requirements {verb}"


def _reestimate_words(period: PeriodAttribution) -> str:
    listed = [
        f"{item.key} {_number(item.before or 0)} → {_number(item.after or 0)} points"
        for item in period.re_estimated[:MAX_KEYS_NAMED]
    ]
    return "re-estimates (" + ", ".join(listed) + ")" if listed else "re-estimates"


def _keys(items: Sequence[Item]) -> str:
    keys = sorted((item.key for item in items), key=key_order)
    if len(keys) <= MAX_KEYS_NAMED:
        return _joined(keys)
    return f"{', '.join(keys[:MAX_KEYS_NAMED])} and {len(keys) - MAX_KEYS_NAMED} more"


def _joined(words: Sequence[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _number(value: float) -> str:
    rounded = round(value, 1)
    return f"{rounded:g}" if rounded != int(rounded) else str(int(rounded))


def _short_day(day: date) -> str:
    return f"{day.day} {day:%b}"
