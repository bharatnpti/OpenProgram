"""Delivery dates: what was committed, and whether the work will make it.

A date is committed for a project, for one pod's part of a project, or for a
release (a Jira fix version or label within a project). Every change is kept,
so a date that moved shows as moved. A release with no committed date falls
back on its Jira release date.

Two forecasts sit side by side, and their disagreement is a signal in itself:

- **History**: how many requirements reached production on each recent working
  day, replayed many times over the work still open (a Monte Carlo run with a
  fixed seed, so the same day gives the same answer). It says by when the
  work is 50% and 85% likely to be done, once it has the tenant's minimum of
  working days to replay (ten unless an admin or the deployment sets another).
- **The team**: the latest date the open requirements carry, each one's check-in
  ETA where someone gave one, else its Jira due date. Open requirements with
  neither are counted as unknown.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from core.domain.delivery import DeliveryStage, RequirementsSnapshot
from core.domain.errors import OpenProgramError

#: Working days of history a forecast needs before it gives dates, unless the
#: deployment (OPENPROGRAM_FORECAST_MIN_HISTORY_DAYS) or an admin sets another.
MIN_SAMPLE_DAYS = 10
#: The fewest an admin may ask for: with one or two days the 50% and 85% dates
#: replay the same one or two days, so the range they draw says nothing.
LOWEST_MIN_SAMPLE_DAYS = 3
#: The most: about three months, whose window still fits the 90 days a read replays.
HIGHEST_MIN_SAMPLE_DAYS = 60
#: The shortest window the samples are read from, in calendar days. It grows
#: with the minimum (history_window_days), so the window can always hold it.
HISTORY_DAYS = 30
SIMULATION_RUNS = 2000
HORIZON_WORKING_DAYS = 520
MAX_NOTE_LENGTH = 300


class CommitmentScopeKind(StrEnum):
    PROJECT = "project"
    POD = "pod"
    RELEASE = "release"


class ReleaseMatchKind(StrEnum):
    FIX_VERSION = "fix_version"
    LABEL = "label"


class Verdict(StrEnum):
    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    OFF_TRACK = "off_track"
    DONE = "done"
    NO_DATE = "no_date"
    NOT_ENOUGH_DATA = "not_enough_data"


class CommitmentError(OpenProgramError):
    """A delivery date or release that cannot be saved."""


class ForecastSettingsError(OpenProgramError):
    """A forecast setting that cannot be saved."""


@dataclass(frozen=True, kw_only=True)
class CommitmentScope:
    kind: CommitmentScopeKind
    #: The project, the pod, or the release.
    id: str
    #: The project the scope belongs to (the project itself for a project).
    project_id: str

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.project_id}:{self.id}"


@dataclass(frozen=True, kw_only=True)
class DateChange:
    tenant_id: str
    scope: CommitmentScope
    #: None clears the date.
    target_date: date | None
    changed_at: datetime
    changed_by: str
    note: str = ""


@dataclass(frozen=True, kw_only=True)
class Commitment:
    scope: CommitmentScope
    target_date: date | None
    #: The first date ever committed for the scope.
    original_date: date | None
    changes: tuple[DateChange, ...]

    @property
    def moved_days(self) -> int | None:
        """How far the date has moved from the first one; None when it never had two."""
        if self.target_date is None or self.original_date is None:
            return None
        return (self.target_date - self.original_date).days

    @property
    def times_moved(self) -> int:
        dated = [change for change in self.changes if change.target_date is not None]
        return sum(
            1
            for earlier, later in zip(dated, dated[1:], strict=False)
            if later.target_date != earlier.target_date
        )


def commitment_from_changes(scope: CommitmentScope, changes: Sequence[DateChange]) -> Commitment:
    ordered = tuple(sorted(changes, key=lambda change: change.changed_at))
    dated = [change.target_date for change in ordered if change.target_date is not None]
    return Commitment(
        scope=scope,
        target_date=ordered[-1].target_date if ordered else None,
        original_date=dated[0] if dated else None,
        changes=ordered,
    )


@dataclass(frozen=True, kw_only=True)
class ReleaseMatch:
    kind: ReleaseMatchKind
    value: str


@dataclass(frozen=True, kw_only=True)
class Release:
    tenant_id: str
    release_id: str
    project_id: str
    name: str
    match: ReleaseMatch
    updated_at: datetime
    updated_by: str

    def includes(self, metadata: Mapping[str, object]) -> bool:
        """Whether an issue with this metadata belongs to the release."""
        key = "fix_versions" if self.match.kind is ReleaseMatchKind.FIX_VERSION else "labels"
        wanted = self.match.value.strip().casefold()
        return any(item.casefold() == wanted for item in split_names(metadata.get(key)))


def split_names(value: object) -> tuple[str, ...]:
    """The names a comma-joined metadata value holds ("R1, R2")."""
    if not isinstance(value, str):
        return ()
    return tuple(name.strip() for name in value.split(",") if name.strip())


def fix_version_dates(value: object) -> dict[str, date]:
    """Each fix version's release date, from the JSON the issue sync keeps."""
    if not isinstance(value, str) or not value:
        return {}
    try:
        raw = json.loads(value)
    except ValueError:
        return {}
    dates: dict[str, date] = {}
    if isinstance(raw, dict):
        for name, text in raw.items():
            try:
                dates[str(name)] = date.fromisoformat(str(text))
            except ValueError:
                continue
    return dates


# --- Forecasting -------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ForecastSettings:
    """What a tenant's admin set for its forecasts."""

    tenant_id: str
    #: Working days of history before a forecast gives dates; None follows the
    #: deployment's default (OPENPROGRAM_FORECAST_MIN_HISTORY_DAYS).
    min_sample_days: int | None
    updated_at: datetime
    updated_by: str


@dataclass(frozen=True, kw_only=True)
class HistoryForecast:
    p50: date | None
    p85: date | None
    #: Open requirements (or story points) still to reach production.
    remaining: float
    unit: str
    sample_days: int
    completed_in_sample: float
    #: Why there is no forecast, when there is none.
    reason: str | None = None
    #: The working days of history the forecast needs before it gives dates.
    needed_days: int = MIN_SAMPLE_DAYS


@dataclass(frozen=True, kw_only=True)
class TeamForecast:
    latest: date | None
    #: The requirement whose date is the latest.
    latest_key: str | None
    dated: int
    undated: int


@dataclass(frozen=True, kw_only=True)
class OpenItem:
    key: str
    #: The check-in ETA's last day, when someone gave one.
    eta: date | None
    #: The Jira due date.
    due: date | None

    @property
    def date(self) -> date | None:
        return self.eta or self.due


def is_working_day(day: date) -> bool:
    return day.weekday() < 5


def next_working_day(day: date) -> date:
    day += timedelta(days=1)
    while not is_working_day(day):
        day += timedelta(days=1)
    return day


def daily_completions(
    snapshots: Sequence[RequirementsSnapshot],
    *,
    keys: frozenset[str] | None = None,
    by_points: bool = False,
) -> list[float]:
    """Requirements (or points) reaching production on each working day with two snapshots.

    A day counts only when the working day before it has a snapshot too, so a
    gap in the history is not read as a day nothing was finished. ``keys``
    limits the count to one pod's or one release's requirements.
    """
    by_day = {snapshot.day: snapshot for snapshot in snapshots}
    samples: list[float] = []
    for snapshot in sorted(snapshots, key=lambda item: item.day):
        if not is_working_day(snapshot.day):
            continue
        previous = by_day.get(_previous_working_day(snapshot.day))
        if previous is None:
            continue
        if by_points:
            samples.append(max(0.0, snapshot.points_done - previous.points_done))
            continue
        samples.append(
            float(
                sum(
                    1
                    for key, stage in snapshot.items.items()
                    if stage is DeliveryStage.PRODUCTION
                    and (keys is None or key in keys)
                    and previous.items.get(key) is not DeliveryStage.PRODUCTION
                )
            )
        )
    return samples


def history_forecast(
    samples: Sequence[float],
    remaining: float,
    *,
    start: date,
    seed: str,
    unit: str = "requirements",
    runs: int = SIMULATION_RUNS,
    min_sample_days: int = MIN_SAMPLE_DAYS,
) -> HistoryForecast:
    """When the open work is 50% and 85% likely to be done, replaying past days.

    Under ``min_sample_days`` samples there is no forecast, only the reason.
    """
    completed = float(sum(samples))
    if remaining <= 0:
        return HistoryForecast(
            p50=start,
            p85=start,
            remaining=0,
            unit=unit,
            sample_days=len(samples),
            completed_in_sample=completed,
            needed_days=min_sample_days,
        )
    if len(samples) < min_sample_days:
        return HistoryForecast(
            p50=None,
            p85=None,
            remaining=remaining,
            unit=unit,
            sample_days=len(samples),
            completed_in_sample=completed,
            reason=_short_history_reason(len(samples), min_sample_days),
            needed_days=min_sample_days,
        )
    if completed <= 0:
        return HistoryForecast(
            p50=None,
            p85=None,
            remaining=remaining,
            unit=unit,
            sample_days=len(samples),
            completed_in_sample=0,
            reason=f"Nothing reached production in the last {len(samples)} working days.",
            needed_days=min_sample_days,
        )
    rng = random.Random(seed)
    finishes: list[date] = []
    for _ in range(runs):
        day = start
        done = 0.0
        for _step in range(HORIZON_WORKING_DAYS):
            day = next_working_day(day)
            done += rng.choice(samples)
            if done >= remaining:
                break
        finishes.append(day)
    finishes.sort()
    return HistoryForecast(
        p50=finishes[int(0.50 * (len(finishes) - 1))],
        p85=finishes[int(0.85 * (len(finishes) - 1))],
        remaining=remaining,
        unit=unit,
        sample_days=len(samples),
        completed_in_sample=completed,
        needed_days=min_sample_days,
    )


def team_forecast(items: Iterable[OpenItem]) -> TeamForecast:
    listed = list(items)
    dated = [item for item in listed if item.date is not None]
    undated = [item for item in listed if item.date is None]
    latest = max(dated, key=lambda item: (item.date, item.key), default=None)
    return TeamForecast(
        latest=latest.date if latest is not None else None,
        latest_key=latest.key if latest is not None else None,
        dated=len(dated),
        undated=len(undated),
    )


def verdict(target: date | None, history: HistoryForecast, team: TeamForecast) -> Verdict:
    """On track, at risk or off track against the target, history first, the team second."""
    if history.remaining <= 0:
        return Verdict.DONE
    if target is None:
        return Verdict.NO_DATE
    if history.p50 is not None and history.p85 is not None:
        if history.p85 <= target:
            return Verdict.ON_TRACK
        if history.p50 <= target:
            return Verdict.AT_RISK
        return Verdict.OFF_TRACK
    if team.latest is None:
        return Verdict.NOT_ENOUGH_DATA
    if team.latest > target:
        return Verdict.OFF_TRACK
    return Verdict.AT_RISK if team.undated > 0 else Verdict.ON_TRACK


def validated_target(target: date | None, today: date) -> date | None:
    if target is not None and target < today - timedelta(days=365):
        raise CommitmentError("A delivery date more than a year in the past is not a plan.")
    return target


def validated_note(note: str) -> str:
    clean = " ".join(note.split())
    if len(clean) > MAX_NOTE_LENGTH:
        raise CommitmentError(f"A note is at most {MAX_NOTE_LENGTH} characters.")
    return clean


def validated_min_sample_days(days: int) -> int:
    """The working days of history a forecast waits for, within what a forecast can use."""
    if days < LOWEST_MIN_SAMPLE_DAYS:
        raise ForecastSettingsError(
            f"A forecast needs at least {LOWEST_MIN_SAMPLE_DAYS} working days of history: "
            "with fewer, its 50% and 85% dates replay the same one or two days."
        )
    if days > HIGHEST_MIN_SAMPLE_DAYS:
        raise ForecastSettingsError(
            f"A forecast can wait for at most {HIGHEST_MIN_SAMPLE_DAYS} working days of "
            "history, about three months."
        )
    return days


def history_window_days(min_sample_days: int) -> int:
    """The calendar days a forecast reads its samples from: 30, or more when the minimum needs it.

    A working day is a sample only when the working day before it has a snapshot
    too, so the window must hold one working day more than the minimum, wherever
    the weekends fall. Any ``window`` days in a row do, so a forecast (which reads
    the window before its day, and the day) and a timeline of ``window`` days up
    to its day can both reach the minimum. Ten keeps the window at 30 days.
    """
    window = HISTORY_DAYS
    while _fewest_working_days(window) < min_sample_days + 1:
        window += 1
    return window


def _fewest_working_days(days: int) -> int:
    """The fewest working days any ``days`` calendar days in a row hold: a run from a Saturday."""
    weeks, rest = divmod(days, 7)
    return weeks * 5 + max(0, rest - 2)


def _short_history_reason(days: int, needed: int) -> str:
    """Why history cannot forecast yet, worded for no days and for one."""
    if days == 0:
        return f"No history yet: a forecast needs {needed} working days of daily snapshots."
    unit = "working day" if days == 1 else "working days"
    return f"Only {days} {unit} of history; a forecast needs {needed}."


def _previous_working_day(day: date) -> date:
    day -= timedelta(days=1)
    while not is_working_day(day):
        day -= timedelta(days=1)
    return day
