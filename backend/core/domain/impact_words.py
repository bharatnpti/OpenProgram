"""Why the date moved, in words a leader reads in five seconds (decision D78).

Where the date stands comes first ("Likely by 19 Mar, 3 months after the
committed 15 Dec."), then why it moved, in at most three causes:

- **scope added**: requirements added after planning (with or without the
  change label, bugs, moved in), reopened, and re-estimated larger;
- **scope removed**: requirements removed, and re-estimated smaller;
- **team speed**: how fast the team finishes -- the time passed and the work
  finished, the speed of the recent history the forecast reads, and the
  forecast's rounding -- read as one.

The same causes and words serve the Overall sentence and bars, the timeline's
row sentence, the day report's line and Ora's delivery_impact. No word of how
the forecast is computed reaches a leader: never "throughput", "window",
"replay", "p50" or "p85"; the 85% date is "likely by", the 50% date "likely
around". Every cause names work items, never people.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from core.domain.impact import (
    Driver,
    Item,
    Measure,
    PeriodAttribution,
    Reestimate,
    ScopeGroup,
    Shift,
    key_order,
)

#: How many keys a cause names before it counts the rest.
MAX_KEYS_NAMED = 3
MINUS = "−"

#: The three causes a leader reads, and their names on the bars.
SCOPE_ADDED = "scope_added"
SCOPE_REMOVED = "scope_removed"
TEAM_SPEED = "team_speed"
CAUSE_LABELS: Mapping[str, str] = {
    SCOPE_ADDED: "Scope added",
    SCOPE_REMOVED: "Scope removed",
    TEAM_SPEED: "Team speed",
}
#: The drivers team speed reads as one: how fast the team finishes, the recent
#: history's speed, the forecast's rounding, and days whose part is not known.
TEAM_SPEED_DRIVERS: tuple[Driver, ...] = (
    Driver.TIME_PASSED,
    Driver.WORK_FINISHED,
    Driver.THROUGHPUT,
    Driver.ROUNDING,
    Driver.NOT_ATTRIBUTED,
)
#: A cause of less than this on the measure read is not said in a sentence.
SAID_FROM = 0.5


@dataclass(frozen=True, kw_only=True)
class Cause:
    """One of the three causes, on both measures, with the work items behind it."""

    kind: str
    shift: Shift
    #: Added and reopened (scope added), removed (scope removed), finished (team speed).
    items: tuple[Item, ...] = ()
    reopened: tuple[Item, ...] = ()
    re_estimated: tuple[Reestimate, ...] = ()


def causes(period: PeriodAttribution) -> list[Cause]:
    """The period's drivers as the three causes; one with no shift and no items is left out.

    Re-estimates join scope added when they pushed the 85% date later, scope removed
    when they pulled it in. The causes add up to the move exactly.
    """
    re_estimated = period.driver(Driver.RE_ESTIMATED)
    added = period.driver(Driver.SCOPE_ADDED) + period.driver(Driver.REOPENED)
    removed = period.driver(Driver.SCOPE_REMOVED)
    larger = re_estimated.p85 >= 0
    if larger:
        added = added + re_estimated
    else:
        removed = removed + re_estimated
    team = Shift()
    for driver in TEAM_SPEED_DRIVERS:
        team = team + period.driver(driver)
    found = [
        Cause(
            kind=SCOPE_ADDED,
            shift=added,
            items=period.added,
            reopened=period.reopened,
            re_estimated=period.re_estimated if larger else (),
        ),
        Cause(
            kind=SCOPE_REMOVED,
            shift=removed,
            items=period.removed,
            re_estimated=() if larger else period.re_estimated,
        ),
        Cause(kind=TEAM_SPEED, shift=team, items=period.finished),
    ]
    return [
        cause
        for cause in found
        if not cause.shift.zero or cause.items or cause.reopened or cause.re_estimated
    ]


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
    """The date a measure is, in a leader's words: "likely by" (85%), "likely around" (50%)."""
    return "likely by" if measure is Measure.P85 else "likely around"


def likely_words(likely: date | None, committed: date | None, measure: Measure) -> str:
    """Where the date stands: 'Likely by 19 Mar, 3 months after the committed 15 Dec'."""
    if likely is None:
        return "No likely date yet"
    said = f"{measure_words(measure).capitalize()} {_short_day(likely)}"
    if committed is None:
        return f"{said}; no committed date yet"
    gap = (likely - committed).days
    if gap == 0:
        return f"{said}, on the committed date"
    side = "after" if gap > 0 else "before"
    return f"{said}, {span_words(abs(gap))} {side} the committed {_short_day(committed)}"


def span_words(days: int) -> str:
    """Calendar days as a leader says them: '5 days', '3 weeks', '3 months'."""
    if days < 14:
        return f"{days} {'day' if days == 1 else 'days'}"
    if days < 60:
        weeks = round(days / 7)
        return f"{weeks} {'week' if weeks == 1 else 'weeks'}"
    months = round(days / 30.44)
    return f"{months} {'month' if months == 1 else 'months'}"


def cause_note(
    cause: Cause,
    groups: Mapping[str, ScopeGroup],
    *,
    change_label: str,
    one_day: bool = False,
) -> str:
    """A cause's subline: '1 change request, 1 without the CR label', '2 requirements
    finished'; for one day, its keys: 'CHK-20, without the CR label'."""
    if cause.kind == SCOPE_ADDED:
        said = _added_words(cause.items, groups, change_label, one_day)
        if cause.reopened:
            count = _keys(cause.reopened) if one_day else str(len(cause.reopened))
            said.append(f"{count} reopened")
        if cause.re_estimated:
            said.append(_estimate_words(cause.re_estimated, "larger", one_day))
        return "; ".join(said) if one_day else ", ".join(said)
    if cause.kind == SCOPE_REMOVED:
        said = []
        if cause.items:
            said.append(
                f"{_keys(cause.items)} removed"
                if one_day
                else f"{len(cause.items)} {_noun(len(cause.items))} removed"
            )
        if cause.re_estimated:
            said.append(_estimate_words(cause.re_estimated, "smaller", one_day))
        return ", ".join(said)
    if not cause.items:
        return "nothing finished"
    if one_day:
        return f"{_keys(cause.items)} finished"
    return f"{len(cause.items)} {_noun(len(cause.items))} finished"


def cause_phrase(
    cause: Cause,
    measure: Measure,
    *,
    groups: Mapping[str, ScopeGroup],
    change_label: str,
    with_unit: bool,
    one_day: bool = False,
) -> str:
    """One cause in a sentence: 'added scope pushed it back 24 days', 'the team finishing
    faster pulled it in 80'; for one day with its keys: 'added scope (CHK-20, without the
    CR label) pushed it back 2 days', 'nothing finished pushed it back 1'."""
    value = cause.shift.of(measure)
    count = _number(abs(value))
    unit = f" {'day' if count == '1' else 'days'}" if with_unit else ""
    verb = f"pushed it back {count}{unit}" if value > 0 else f"pulled it in {count}{unit}"
    if cause.kind == TEAM_SPEED:
        if one_day:
            subject = "nothing finished" if not cause.items else f"finishing {_keys(cause.items)}"
        else:
            subject = "slower finishing" if value > 0 else "the team finishing faster"
        return f"{subject} {verb}"
    subject = "added scope" if cause.kind == SCOPE_ADDED else "removed scope"
    if one_day:
        note = cause_note(cause, groups, change_label=change_label, one_day=True)
        if note:
            subject = f"{subject} ({note})"
    return f"{subject} {verb}"


def said_causes(period: PeriodAttribution, measure: Measure) -> list[Cause]:
    """The causes a sentence says: half a working day or more, the latest-pushing first."""
    return sorted(
        (cause for cause in causes(period) if abs(cause.shift.of(measure)) >= SAID_FROM),
        key=lambda cause: -cause.shift.of(measure),
    )


def answer_line(
    period: PeriodAttribution,
    groups: Mapping[str, ScopeGroup],
    *,
    measure: Measure,
    change_label: str,
    period_words: str,
    committed: date | None,
) -> str:
    """'Likely by 19 Mar, 3 months after the committed 15 Dec. Since planning (5 Oct) it
    moved 54 working days earlier: added scope pushed it back 24 days, the team finishing
    faster pulled it in 78.'"""
    likely = period.to_p85 if measure is Measure.P85 else period.to_p50
    stands = likely_words(likely, committed, measure) + "."
    moved = period.moved
    if moved is None:
        return f"{stands} No likely date {period_words} to compare with."
    line = f"{stands} {_capital(period_words)} it {moved_words(moved.of(measure))}"
    listed = [
        cause_phrase(cause, measure, groups=groups, change_label=change_label, with_unit=index == 0)
        for index, cause in enumerate(said_causes(period, measure))
    ]
    return line + (": " + ", ".join(listed) if listed else "") + "."


def dates_line(
    period: PeriodAttribution | None,
    *,
    measure: Measure,
    period_words: str,
    committed: date | None,
) -> str:
    """For a reader of the dates only: where it stands and how far it moved, no causes."""
    if period is None:
        return f"No likely date {period_words} to compare with."
    likely = period.to_p85 if measure is Measure.P85 else period.to_p50
    stands = likely_words(likely, committed, measure) + "."
    if period.moved is None:
        return f"{stands} No likely date {period_words} to compare with."
    return f"{stands} {_capital(period_words)} it {moved_words(period.moved.of(measure))}."


def day_line(
    period: PeriodAttribution,
    groups: Mapping[str, ScopeGroup],
    *,
    change_label: str,
) -> str | None:
    """The day report's line, when the likely date moved since the previous report:
    'The likely date moved 3 working days later: added scope (CHK-20, without the CR
    label) pushed it back 2 days, nothing finished pushed it back 1.'"""
    moved = period.moved
    if moved is None or abs(moved.p85) < SAID_FROM:
        return None
    line = f"The likely date {moved_words(moved.p85)}"
    listed = [
        cause_phrase(
            cause,
            Measure.P85,
            groups=groups,
            change_label=change_label,
            with_unit=index == 0,
            one_day=True,
        )
        for index, cause in enumerate(said_causes(period, Measure.P85))
    ]
    return line + (": " + ", ".join(listed) if listed else "") + "."


def _added_words(
    items: Sequence[Item], groups: Mapping[str, ScopeGroup], change_label: str, one_day: bool
) -> list[str]:
    """'1 change request', '1 without the CR label' for a period; 'CHK-20, without the CR
    label' for a day."""
    by_group: dict[ScopeGroup, list[Item]] = {}
    for item in items:
        by_group.setdefault(groups.get(item.key, ScopeGroup.BEFORE_PLANNING), []).append(item)
    order = [group for group in ScopeGroup if group in by_group]
    if one_day:
        return [
            f"{_keys(by_group[group])}, {group_words(group, change_label, len(by_group[group]))}"
            for group in order
        ]
    counts = Counter({group: len(found) for group, found in by_group.items()})
    return [f"{counts[group]} {group_words(group, change_label, counts[group])}" for group in order]


def _estimate_words(items: Sequence[Reestimate], size: str, one_day: bool) -> str:
    if one_day:
        keys = _joined(sorted((item.key for item in items), key=key_order)[:MAX_KEYS_NAMED])
        return f"{keys} re-estimated {size}"
    return f"{len(items)} re-estimated {size}"


def _noun(count: int) -> str:
    return "requirement" if count == 1 else "requirements"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


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
