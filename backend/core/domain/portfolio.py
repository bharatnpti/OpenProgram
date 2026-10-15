"""Will we make the date? across the portfolio: one line per project, in plain words.

Pure: the order projects are listed in (worst first), the filters, the one
reason line a project's card carries, and today's health as words (one
colour per project, the date's verdict; health is said, never a second
colour). Every reason counts work items; none names or counts a person.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from core.domain.forecast import Verdict
from core.domain.impact import working_days_moved

#: The verdict's words, as the console's pill says them (frontend-v3 VERDICT_LABELS).
VERDICT_WORDS: dict[Verdict, str] = {
    Verdict.ON_TRACK: "On track",
    Verdict.AT_RISK: "At risk",
    Verdict.OFF_TRACK: "Off track",
    Verdict.DONE: "Done",
    Verdict.NO_DATE: "No committed date",
    Verdict.NOT_ENOUGH_DATA: "Not enough history to forecast",
}
#: An added requirement moves the date at least this much before it is a cause.
SAID_FROM_DAYS = 1.0


class VerdictFilter(StrEnum):
    """The filter chips: a project's verdict, as the reader picks it."""

    LATE = "late"
    AT_RISK = "at_risk"
    NO_DATE = "no_date"
    ON_TRACK = "on_track"
    DONE = "done"
    NOT_ENOUGH_DATA = "not_enough_data"


FILTER_VERDICTS: dict[VerdictFilter, Verdict] = {
    VerdictFilter.LATE: Verdict.OFF_TRACK,
    VerdictFilter.AT_RISK: Verdict.AT_RISK,
    VerdictFilter.NO_DATE: Verdict.NO_DATE,
    VerdictFilter.ON_TRACK: Verdict.ON_TRACK,
    VerdictFilter.DONE: Verdict.DONE,
    VerdictFilter.NOT_ENOUGH_DATA: Verdict.NOT_ENOUGH_DATA,
}


class PortfolioSort(StrEnum):
    #: Off track, at risk, the ones that cannot be judged, on track, done; then
    #: the most days late, the earliest committed date and the name.
    WORST = "worst"
    DAYS_LATE = "days_late"
    COMMITTED = "committed"
    NAME = "name"


def verdict_weight(verdict: Verdict | None, *, total: int) -> int:
    """Worst first, as the console's date strips rank (dateStripWords.verdictWeight)."""
    if verdict is None:
        return 2
    if total == 0 and verdict is not Verdict.DONE:
        return 1
    return {
        Verdict.OFF_TRACK: 5,
        Verdict.AT_RISK: 4,
        Verdict.NOT_ENOUGH_DATA: 3,
        Verdict.NO_DATE: 2,
        Verdict.ON_TRACK: 0,
        Verdict.DONE: -1,
    }[verdict]


def working_days_late(target: date | None, likely: date | None) -> int | None:
    """Working days the 85% date lies after the target (negative: before it)."""
    if target is None or likely is None:
        return None
    return working_days_moved(target, likely)


@dataclass(frozen=True, kw_only=True)
class AgainstPlan:
    """The scope today against the planning baseline: keys counted, never who."""

    planned_on: date
    planned: int
    added: int
    #: Of those added, the ones without the baseline's change label (tenant data).
    without_label: int
    change_label: str
    removed: int = 0
    #: Working days the additions since planning moved the 85% date; None when no
    #: kept day took them apart.
    added_days: float | None = None

    @property
    def without_label_percent(self) -> int | None:
        return round(100 * self.without_label / self.added) if self.added else None


@dataclass(frozen=True, kw_only=True)
class ReasonInputs:
    verdict: Verdict | None
    target: date | None
    #: "committed" or "jira_release".
    target_source: str | None
    p50: date | None
    p85: date | None
    team_date: date | None
    team_key: str | None
    sample_days: int
    min_sample_days: int
    plan: AgainstPlan | None
    #: The day read: dates in its year are said without one.
    as_of: date


def reason_line(inputs: ReasonInputs) -> str:
    """Why the project has its verdict, in one line that counts work items.

    E.g. "Off track because 12 requirements were added after planning (5 Oct), 9
    without the CR label, pushing it back 18 working days." Scope added leads
    only when the kept days say it moved the date; else the dates say why.
    """
    verdict = inputs.verdict
    day = _days_in(inputs.as_of)
    if verdict is None:
        return "No forecast is kept for it yet: the hourly run has not reached it."
    if verdict is Verdict.DONE:
        return "Done: every requirement is in production."
    if verdict is Verdict.NOT_ENOUGH_DATA:
        return (
            "Not enough history to forecast yet: "
            f"{inputs.sample_days} of {inputs.min_sample_days} working days."
        )
    if verdict is Verdict.NO_DATE:
        likely = f"; likely by {day(inputs.p85)} (85%)" if inputs.p85 else ""
        return f"No delivery date is committed yet{likely}."
    if verdict is Verdict.ON_TRACK:
        return _on_track(inputs)
    return _behind(inputs, verdict)


def _against(inputs: ReasonInputs) -> str:
    return "Jira's release date" if inputs.target_source == "jira_release" else "the committed date"


def _on_track(inputs: ReasonInputs) -> str:
    day, target, against = _days_in(inputs.as_of), inputs.target, _against(inputs)
    if inputs.p85 is not None and target is not None:
        ahead = -(working_days_late(target, inputs.p85) or 0)
        when = f"{_working_days(ahead)} before" if ahead > 0 else "on"
        return f"On track: 85% likely by {day(inputs.p85)}, {when} {against} {day(target)}."
    if inputs.team_date is not None and target is not None:
        return (
            f"On track: the team's own date {day(inputs.team_date)} is before "
            f"{against} {day(target)}."
        )
    return "On track."


def _behind(inputs: ReasonInputs, verdict: Verdict) -> str:
    """At risk or off track: scope added after planning when it moved the date, else the dates."""
    day, target, against = _days_in(inputs.as_of), inputs.target, _against(inputs)
    label = VERDICT_WORDS[verdict]
    plan = inputs.plan
    if plan is not None and plan.added and (plan.added_days or 0) >= SAID_FROM_DAYS:
        without = (
            f", {plan.without_label} without the {plan.change_label} label"
            if plan.without_label
            else ""
        )
        pushed = f", pushing it back {_working_days(round(plan.added_days or 0))}"
        return (
            f"{label} because {_requirements(plan.added)} added after planning "
            f"({day(plan.planned_on)}){without}{pushed}."
        )
    if inputs.p50 is not None and inputs.p85 is not None and target is not None:
        if verdict is Verdict.AT_RISK:
            return (
                f"At risk: 50% likely by {day(inputs.p50)}, but 85% only by "
                f"{day(inputs.p85)}, after {against} {day(target)}."
            )
        late = working_days_late(target, inputs.p50) or 0
        return (
            f"Off track: even the 50% date, {day(inputs.p50)}, is "
            f"{_working_days(late)} after {against} {day(target)}."
        )
    if inputs.team_date is not None and target is not None and inputs.team_date > target:
        keyed = f" ({inputs.team_key})" if inputs.team_key else ""
        return (
            f"{label}: the team's own date {day(inputs.team_date)}{keyed} is after "
            f"{against} {day(target)}."
        )
    return f"{label}."


@dataclass(frozen=True, kw_only=True)
class HealthCounts:
    """Today's health of a project, counted: work items and replies, never who."""

    reported: bool
    blockers: int = 0
    blocked_tasks: int = 0
    disagree: int = 0
    asked: int = 0
    unanswered: int = 0


def health_words(counts: HealthCounts) -> str:
    """Today's health in words, for after the reason ("1 open blocker, 2 of 8 not replied")."""
    if not counts.reported and not counts.asked:
        return "no status reported yet"
    parts: list[str] = []
    if counts.blockers:
        parts.append(_count(counts.blockers, "open blocker", "open blockers"))
    if counts.blocked_tasks:
        parts.append(_count(counts.blocked_tasks, "blocked task", "blocked tasks"))
    if counts.disagree:
        parts.append(f"signals disagree on {_count(counts.disagree, 'issue', 'issues')}")
    if not parts:
        parts.append("no open blockers")
    if counts.asked:
        parts.append(
            f"{counts.unanswered} of {counts.asked} not replied"
            if counts.unanswered
            else "all replied"
        )
    return ", ".join(parts)


def sort_key(
    *,
    sort: PortfolioSort,
    verdict: Verdict | None,
    total: int,
    days_late: int | None,
    target: date | None,
    name: str,
) -> tuple[object, ...]:
    """One order for the portfolio: nothing in it is about a person."""
    weight = -verdict_weight(verdict, total=total)
    late = -(days_late if days_late is not None else -10_000)
    committed = target or date.max
    by_name = name.casefold()
    if sort is PortfolioSort.NAME:
        return (by_name,)
    if sort is PortfolioSort.COMMITTED:
        return (committed, weight, by_name)
    if sort is PortfolioSort.DAYS_LATE:
        return (late, weight, committed, by_name)
    return (weight, late, committed, by_name)


def verdict_counts(verdicts: Iterable[Verdict | None]) -> dict[str, int]:
    """How many projects have each verdict, for the count tiles; "none" has no kept row."""
    counts = {verdict.value: 0 for verdict in Verdict} | {"none": 0}
    for verdict in verdicts:
        counts[verdict.value if verdict is not None else "none"] += 1
    return counts


def _days_in(as_of: date) -> _DayWords:
    return _DayWords(as_of.year)


@dataclass(frozen=True)
class _DayWords:
    year: int

    def __call__(self, day: date | None) -> str:
        if day is None:
            return "no date"
        words = f"{day.day} {day:%b}"
        return words if day.year == self.year else f"{words} {day.year}"


def _working_days(count: int) -> str:
    return _count(abs(count), "working day", "working days")


def _requirements(count: int) -> str:
    return f"{count} requirement was" if count == 1 else f"{count} requirements were"


def _count(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"
