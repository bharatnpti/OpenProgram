"""The planning baseline: the requirements agreed when planning was complete.

A product owner or a manager marks planning complete for a project, or for one
release of it. That freezes the requirement set as it stood: each key with its
stage, story points, due date and type. Later scope is read against it, so a
requirement that joined after it is added scope, and one carrying the
baseline's change label is an agreed change. One baseline is active per scope;
marking planning complete again adds a new one and keeps the earlier ones, so
"since the first baseline" still reads.

Planning can also be marked complete on an earlier day. The stored snapshot of
that day (or of the last day before it that kept one, within a week) is frozen.
Before snapshots were kept, the issues in scope today that the tracker says were
created by then stand in, and the baseline says it was reconstructed.

The change label is the tenant's own word for a change agreed after planning;
"CR" is only the default, a generic one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from core.domain.delivery import DeliveryStage
from core.domain.errors import OpenProgramError

#: The label a requirement added after planning carries when the change was agreed,
#: unless the person marking planning complete names the tenant's own.
DEFAULT_CHANGE_LABEL = "CR"
#: Working days after the baseline in which a requirement still counts as planned.
DEFAULT_GRACE_WORKING_DAYS = 2
MAX_GRACE_WORKING_DAYS = 20
MAX_CHANGE_LABEL_LENGTH = 64
MAX_BASELINE_NOTE_LENGTH = 300
#: How far back planning may be marked complete.
MAX_PLANNED_DAYS_AGO = 365
#: A day with no snapshot (a weekend) reads the last earlier one kept within this many days.
SNAPSHOT_LOOKBACK_DAYS = 7


class BaselineScopeKind(StrEnum):
    PROJECT = "project"
    RELEASE = "release"


class BaselineKind(StrEnum):
    #: Frozen from the requirements as they stood when planning was marked complete.
    SNAPSHOT = "snapshot"
    #: Frozen from an earlier day: that day's stored snapshot, or the issues created by then.
    DATE = "date"


class BaselineError(OpenProgramError):
    """A planning baseline that cannot be set."""


@dataclass(frozen=True, kw_only=True)
class BaselineItem:
    """What a requirement was when planning was complete; None where it is not known."""

    stage: DeliveryStage | None
    points: float | None = None
    due: date | None = None
    type: str | None = None


@dataclass(frozen=True, kw_only=True)
class PlanningBaseline:
    tenant_id: str
    baseline_id: str
    scope_kind: BaselineScopeKind
    #: The project's id, or the release's.
    scope_id: str
    project_id: str
    kind: BaselineKind
    #: The day planning was complete.
    planned_on: date
    items: Mapping[str, BaselineItem]
    #: True when no snapshot of the day was kept and the items were rebuilt.
    reconstructed: bool
    #: The snapshot the items were read from, when it is not the day itself.
    snapshot_day: date | None
    change_label: str
    grace_working_days: int
    set_by: str
    set_at: datetime
    note: str = ""
    #: When a newer baseline replaced it; None while it is the scope's active one.
    superseded_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.superseded_at is None

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(self.items)

    @property
    def open_keys(self) -> frozenset[str]:
        return frozenset(
            key for key, item in self.items.items() if item.stage is not DeliveryStage.PRODUCTION
        )

    @property
    def points(self) -> float | None:
        """The story points frozen, when every requirement carried them."""
        values = [item.points for item in self.items.values()]
        if not values or any(value is None for value in values):
            return None
        return float(sum(value for value in values if value is not None))


@dataclass(frozen=True, kw_only=True)
class ScopeSinceBaseline:
    """How the scope today differs from a baseline: keys only, never who."""

    #: In scope now and not in the baseline, in key order.
    added: tuple[str, ...]
    #: Of those, the ones carrying the baseline's change label.
    added_with_label: tuple[str, ...]
    #: In the baseline and no longer in scope, in key order.
    removed: tuple[str, ...]


def since_baseline(
    baseline: PlanningBaseline,
    current: Iterable[str],
    labels: Mapping[str, Iterable[str]],
) -> ScopeSinceBaseline:
    """The scope ``current`` against ``baseline``; ``labels`` are each key's tracker labels."""
    now = frozenset(current)
    added = sorted(now - baseline.keys, key=key_order)
    wanted = baseline.change_label.casefold()
    return ScopeSinceBaseline(
        added=tuple(added),
        added_with_label=tuple(
            key for key in added if any(label.casefold() == wanted for label in labels.get(key, ()))
        ),
        removed=tuple(sorted(baseline.keys - now, key=key_order)),
    )


def validated_change_label(label: str | None) -> str:
    """The change label as a tracker label: one word; the default when none is given."""
    clean = (label or "").strip()
    if not clean:
        return DEFAULT_CHANGE_LABEL
    if any(character.isspace() for character in clean):
        raise BaselineError("A change label is one Jira label, with no spaces.")
    if len(clean) > MAX_CHANGE_LABEL_LENGTH:
        raise BaselineError(f"A change label is at most {MAX_CHANGE_LABEL_LENGTH} characters.")
    return clean


def validated_grace(days: int) -> int:
    if days < 0 or days > MAX_GRACE_WORKING_DAYS:
        raise BaselineError(
            f"The grace period is 0 to {MAX_GRACE_WORKING_DAYS} working days after planning."
        )
    return days


def validated_baseline_note(note: str) -> str:
    clean = " ".join(note.split())
    if len(clean) > MAX_BASELINE_NOTE_LENGTH:
        raise BaselineError(f"A note is at most {MAX_BASELINE_NOTE_LENGTH} characters.")
    return clean


def validated_planned_on(day: date | None, today: date) -> date:
    """The day planning was complete: today unless an earlier day is given."""
    if day is None:
        return today
    if day > today:
        raise BaselineError("Planning can be marked complete today or on an earlier day.")
    if day < today - timedelta(days=MAX_PLANNED_DAYS_AGO):
        raise BaselineError("Planning can be marked complete at most a year back.")
    return day


def key_order(key: str) -> tuple[str, int, str]:
    """CHK-2 before CHK-10: an issue key's project, then its number."""
    head, _, tail = key.rpartition("-")
    return (head, int(tail), key) if tail.isdigit() else (key, 0, key)
