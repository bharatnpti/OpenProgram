"""The timeline per project and pod, as rules: which row comes first, the axis, the words.

A timeline row runs from its planning baseline (or, without one, its first kept
forecast day) to its committed date. It carries progress, the 50% to 85% band,
the team's latest date, the committed date with where it stood before, and the
impact markers on the day each thing happened. This module holds what is pure
about it: the order rows are listed in (worst verdict first), the axis every
row is drawn on, and the one sentence per row that the picture's text
alternative speaks. Nothing here names or counts a person.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from core.domain.forecast import Verdict
from core.domain.impact import POINTS_UNIT, working_days_moved

#: The verdicts, worst first: an off-track pod is read before one on track.
VERDICT_ORDER: Mapping[Verdict | None, int] = {
    Verdict.OFF_TRACK: 0,
    Verdict.AT_RISK: 1,
    Verdict.NO_DATE: 2,
    Verdict.NOT_ENOUGH_DATA: 3,
    None: 4,
    Verdict.ON_TRACK: 5,
    Verdict.DONE: 6,
}
#: The axis runs this many days past the last date it draws, so an end mark has room.
AXIS_PAD_DAYS = 7
#: The shortest axis: four weeks, so a short history still reads as time.
MIN_AXIS_DAYS = 28
#: The longest axis back from the day read.
MAX_AXIS_BACK_DAYS = 365


@dataclass(frozen=True, kw_only=True)
class Axis:
    start: date
    end: date
    today: date


def axis(starts: Iterable[date], dates: Iterable[date | None], today: date) -> Axis:
    """The days every row is drawn on: from the earliest row's start to past its last date."""
    first = min((day for day in starts), default=today - timedelta(days=MIN_AXIS_DAYS // 2))
    first = max(min(first, today), today - timedelta(days=MAX_AXIS_BACK_DAYS))
    last = max((day for day in dates if day is not None), default=today)
    end = max(last, today) + timedelta(days=AXIS_PAD_DAYS)
    if (end - first).days < MIN_AXIS_DAYS:
        end = first + timedelta(days=MIN_AXIS_DAYS)
    return Axis(start=first, end=end, today=today)


def worst_first(verdict: Verdict | None, name: str) -> tuple[int, str]:
    """A pod's place among the project's pods: worst verdict first, then by name."""
    return (VERDICT_ORDER.get(verdict, 4), name.casefold())


@dataclass(frozen=True, kw_only=True)
class SentenceParts:
    """What one row's sentence says, from what the row carries."""

    name: str
    #: Done and total, in the unit the progress is counted in; None for a dates-only row.
    done: float | None = None
    total: float | None = None
    unit: str = "requirements"
    verdict: Verdict | None = None
    p85: date | None = None
    #: Why there is no forecast, in the server's words ("4 of 10 working days of history").
    forecast_reason: str | None = None
    committed: date | None = None
    #: The committed dates in order, the current one last.
    committed_history: Sequence[date] = ()
    #: The scope since planning, for a whole row; None for a dates-only row.
    planned: bool | None = None
    added: int = 0
    added_without_label: int = 0
    change_label: str = "CR"


def row_sentence(parts: SentenceParts) -> str:
    """The row in one sentence, for the picture's text alternative and the table.

    "Checkout Revamp: 8 of 22 done; the 85% date, 2 Dec, is 10 working days
    after the committed 18 Nov; 4 requirements added since planning, 2 without
    the CR label."
    """
    clauses: list[str] = []
    if parts.done is not None and parts.total is not None:
        clauses.append(_progress(parts))
    clauses.append(_dates(parts))
    moved = _committed_moves(parts.committed_history)
    if moved:
        clauses.append(moved)
    if parts.planned is not None:
        clauses.append(_scope(parts))
    return f"{parts.name}: {'; '.join(clause for clause in clauses if clause)}."


def _progress(parts: SentenceParts) -> str:
    done, total = parts.done or 0, parts.total or 0
    if total <= 0:
        return "nothing in scope yet"
    if parts.verdict is Verdict.DONE or done >= total:
        return f"all {_number(total)} {_unit(parts.unit, total)} done"
    if parts.unit == POINTS_UNIT:
        return f"{_number(done)} of {_number(total)} story points done"
    return f"{_number(done)} of {_number(total)} done"


def _dates(parts: SentenceParts) -> str:
    committed = parts.committed
    if parts.p85 is None:
        reason = parts.forecast_reason or "no forecast yet"
        tail = f", committed {_short(committed)}" if committed else ", no committed date"
        return f"{_lower_first(reason)}{tail}"
    if committed is None:
        return f"the 85% date is {_short(parts.p85)}, with no committed date"
    gap = working_days_moved(committed, parts.p85)
    if gap == 0:
        return f"the 85% date is the committed date, {_short(committed)}"
    days = f"{abs(gap)} working {'day' if abs(gap) == 1 else 'days'}"
    side = "after" if gap > 0 else "before"
    return f"the 85% date, {_short(parts.p85)}, is {days} {side} the committed {_short(committed)}"


def _committed_moves(history: Sequence[date]) -> str | None:
    """'the committed date moved twice, 4 Nov → 18 Nov': only when it moved."""
    moves = sum(1 for before, after in zip(history, history[1:], strict=False) if before != after)
    if moves == 0:
        return None
    times = {1: "once", 2: "twice"}.get(moves, f"{moves} times")
    return f"the committed date moved {times}, {_short(history[0])} → {_short(history[-1])}"


def _scope(parts: SentenceParts) -> str:
    if not parts.planned:
        return "planning is not marked complete yet"
    if parts.added == 0:
        return "nothing added since planning"
    noun = "requirement" if parts.added == 1 else "requirements"
    said = f"{parts.added} {noun} added since planning"
    if parts.added_without_label:
        said += f", {parts.added_without_label} without the {parts.change_label} label"
    return said


def no_forecast_reason(sample_days: int | None, needed_days: int | None) -> str:
    """'No forecast yet: 4 of 10 working days of history', from the kept row's numbers."""
    if sample_days is None or needed_days is None:
        return "No forecast kept yet"
    return f"No forecast yet: {sample_days} of {needed_days} working days of history"


def _unit(unit: str, count: float) -> str:
    if unit == POINTS_UNIT:
        return "story points"
    return "requirement" if count == 1 else "requirements"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _short(day: date | None) -> str:
    return f"{day.day} {day:%b}" if day is not None else "no date"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text
