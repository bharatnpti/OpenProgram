"""The day report's facts, structured, for the console to draw.

The console draws the day report as pictures (a date bar, a stage strip, gate
rings, who acts) beside the plain text that is sent. Every picture is drawn from
these facts, which the report builder takes from the same computation as the
report's own lines, so the two cannot disagree. A fact the report's text does
not state is left out (None, or not listed): a picture never says more than the
message, so nothing on the console's Daily is news to anyone the message reaches.

Nothing here is sent. The sent text, the schedule and every channel's format
read only the report's sections.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.domain.delivery import DeliveryStage
from core.domain.escalation_matrix import NeedType
from core.domain.forecast import Verdict


@dataclass(frozen=True, kw_only=True)
class NoteFacts:
    """The day's note, as In short opens with it."""

    #: The writer's display name; empty when no member is named.
    author: str
    text: str


@dataclass(frozen=True, kw_only=True)
class DateFacts:
    """The delivery date as In short and Most important state it."""

    verdict: Verdict
    target: date | None
    #: "committed" or "jira_release"; None without a target.
    target_source: str | None
    #: Who committed the date; only when the report names them.
    committed_by: str | None
    #: How often and how far the date moved; only when the report says so.
    times_moved: int
    moved_days: int | None
    #: The forecast's 50% date, only when the report states it; the 85% date
    #: whenever In short does.
    p50: date | None
    p85: date | None
    #: Working days of history and how many a forecast needs, when the report
    #: says the history is too short.
    history_days: int | None
    history_needed: int | None
    #: Why there is no forecast, in the report's words, when it says so.
    no_forecast_reason: str | None
    #: The team's latest date and its requirement.
    team_latest: date | None
    team_latest_key: str | None


@dataclass(frozen=True, kw_only=True)
class StageCount:
    stage: DeliveryStage
    count: int
    #: The count on the previous snapshot day; None without one.
    previous: int | None


@dataclass(frozen=True, kw_only=True)
class StageMoveFacts:
    key: str
    #: The title as the report words it.
    title: str
    #: None for a requirement new today.
    from_stage: DeliveryStage | None
    #: None for one that left the scope.
    to_stage: DeliveryStage | None


@dataclass(frozen=True, kw_only=True)
class ProgressFacts:
    """Where we stand: progress, stage counts and what moved since the previous day."""

    percent: float | None
    #: The previous snapshot's day; None on the first day.
    since: date | None
    total: int
    #: Every stage with today's count, in delivery order; empty when nothing is counted.
    stages: tuple[StageCount, ...]
    #: The moves the report lists, in its order.
    moves: tuple[StageMoveFacts, ...]
    #: Moves the report counts but does not list ("and N more").
    more_moves: int
    #: The other lines under "What changed": a scope change, a moved delivery
    #: date, or "Nothing changed stage." and the first day's line.
    other_changes: tuple[str, ...]
    #: Lines under Progress the stage strip does not draw (unnamed statuses).
    notes: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class GateFacts:
    """One gate's requirements by state, each counted once."""

    name: str
    guards_stage: DeliveryStage
    total: int
    passed: int
    #: Reached the guarded stage without passing: "moved on without it".
    bypassed: int
    #: Of the rest, by state.
    failed: int
    open: int
    missing: int


@dataclass(frozen=True, kw_only=True)
class BypassFacts:
    """A requirement that reached a stage without passing the gates before it."""

    key: str
    stage: DeliveryStage
    gates: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ImportantFacts:
    """Most important, by what each line is about."""

    #: What the delivery lines say that the date bar draws: "committed",
    #: "no_date", "jira", "forecast", "history" or "team".
    drawn: tuple[str, ...]
    #: Every requirement that went around a gate, not only the lines the message keeps.
    bypassed: tuple[BypassFacts, ...]
    #: Red risks the message lists; each is also a fix under What we need.
    risks: int
    #: The lines no picture draws, in the report's words.
    lines: tuple[str, ...]
    #: Blocking release readiness gaps the message names (its lines are in ``lines``).
    readiness_gaps: int = 0


@dataclass(frozen=True, kw_only=True)
class AskFacts:
    need: NeedType
    text: str
    #: Who raised it or what it waits on.
    detail: str
    #: None when OpenProgram cannot tell how long it waited.
    waited_days: int | None
    issue_key: str | None
    escalated_to: str | None
    escalation_label: str | None
    #: One of the asks In short names under "Needed most".
    needed_most: bool
    #: It is a question the report lists under Open questions.
    open_question: bool


@dataclass(frozen=True, kw_only=True)
class OwnerAsks:
    """Everything needed from one person, as What we need groups it."""

    heading: str
    #: False for the group of asks nobody is named for yet.
    named: bool
    asks: tuple[AskFacts, ...]


@dataclass(frozen=True, kw_only=True)
class DayReportFacts:
    note: NoteFacts | None
    #: None when the report says nothing about the delivery date.
    delivery: DateFacts | None
    progress: ProgressFacts
    gates: tuple[GateFacts, ...]
    important: ImportantFacts
    asks: tuple[OwnerAsks, ...]
