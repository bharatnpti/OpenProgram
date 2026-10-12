"""What moved on a person's work in a window, read from the tracker's history and Git.

The digest is what a silent day is inferred from: per task, the stage moves,
resolutions, due date and estimate moves, assignments and release changes the
tracker's history records (``issue_change`` facts), and the merge requests and
commits that name the task. Changes the person made on someone else's issue are
"worked on"; a task under way with no movement for a while says so.

Pure: it reads facts it is given, never a repository. Nothing in it is free
text. A line names keys, stage and status names, release names, dates and
numbers; a merge request by its number, a commit only as a count naming a task.
It is about tasks: it never counts or compares people.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

from core.domain.delivery import (
    DEFAULT_STAGE_MAPPING,
    STAGE_LABELS,
    STAGE_ORDER,
    DeliveryStage,
    StageMapping,
)
from core.domain.graph import FactEvent, JsonScalar

#: The fact sources the digest reads; the application layer owns the constants
#: and passes facts of these sources in.
ISSUE_CHANGE_SOURCE = "issue_change"
ISSUE_SOURCE = "issue"
MERGE_REQUEST_SOURCE = "vcs_pull_request"
COMMIT_SOURCE = "vcs_commit"
TASK_UPDATE_SOURCE = "task_update"

#: A task under way with no movement for this many days reads "no movement".
QUIET_AFTER_DAYS = 5

#: How many moved tasks a summary names before it says "N more tasks moved".
SUMMARY_TASK_LIMIT = 5
#: How many issues "worked on" names, and how many quiet tasks a summary lists.
SUMMARY_WORKED_ON_LIMIT = 3
SUMMARY_QUIET_LIMIT = 2

# Tracker broad states that are work under way.
_UNDER_WAY_STATES = frozenset({"in_progress", "blocked"})
# A key as a branch, a merge request title or a commit message carries it.
_ISSUE_KEY = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9_]*-\d+)(?![0-9])")


class EventKind(StrEnum):
    MOVED = "moved"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    DUE_DATE = "due_date"
    ESTIMATE = "estimate"
    ASSIGNED_TO = "assigned_to"
    ASSIGNED_AWAY = "assigned_away"
    RELEASE_ADDED = "release_added"
    RELEASE_REMOVED = "release_removed"
    MR_OPENED = "mr_opened"
    MR_MERGED = "mr_merged"
    MR_REVIEWED = "mr_reviewed"
    COMMITS = "commits"


#: Event kinds the tracker records; the rest come from Git.
TRACKER_KINDS = frozenset(
    {
        EventKind.MOVED,
        EventKind.RESOLVED,
        EventKind.REOPENED,
        EventKind.DUE_DATE,
        EventKind.ESTIMATE,
        EventKind.ASSIGNED_TO,
        EventKind.ASSIGNED_AWAY,
        EventKind.RELEASE_ADDED,
        EventKind.RELEASE_REMOVED,
    }
)


@dataclass(frozen=True, kw_only=True)
class FillDaySetting:
    """The tenant's switch "Fill the day from Jira for people who were not asked".

    On by default: ``enabled`` is what an admin saved, with who and when; a
    tenant that never saved one reads ``DEFAULT_FILL_DAY``.
    """

    tenant_id: str
    enabled: bool
    updated_at: datetime | None = None
    updated_by: str | None = None


DEFAULT_FILL_DAY = True


@dataclass(frozen=True, kw_only=True)
class TaskEvent:
    """One thing that happened to a task: its kind, when, and from what to what.

    ``from_value``/``to_value`` are status names, dates (ISO), numbers or
    release names; a merge request's ``reference`` is ``!12``. ``author_member_id``
    is the member who made a tracker change, when the history names one.
    """

    key: str
    kind: EventKind
    at: datetime
    from_value: str | None = None
    to_value: str | None = None
    from_stage: DeliveryStage | None = None
    to_stage: DeliveryStage | None = None
    author_member_id: str | None = None
    reference: str | None = None
    count: int = 1


@dataclass(frozen=True, kw_only=True)
class ObservedTask:
    key: str
    events: tuple[TaskEvent, ...] = ()
    #: Days since the task last moved, for a task under way that has not moved
    #: for at least ``QUIET_AFTER_DAYS``; None otherwise.
    quiet_days: int | None = None


@dataclass(frozen=True, kw_only=True)
class ObservedDay:
    """A person's work as the tracker and Git saw it between ``since`` and ``until``."""

    member_id: str
    since: datetime
    until: datetime
    #: The person's tasks that moved, the one that moved last first.
    tasks: tuple[ObservedTask, ...] = ()
    #: Keys of other people's issues the person changed (groomed, re-estimated).
    worked_on: tuple[str, ...] = ()
    #: The person's tasks under way that have not moved for a while.
    quiet: tuple[ObservedTask, ...] = ()
    #: Merge requests of the person's that name no task: ``!5 merged``.
    unlinked: tuple[TaskEvent, ...] = field(default_factory=tuple)

    @property
    def has_events(self) -> bool:
        """Whether anything moved: a quiet task alone is not evidence of work."""
        return bool(self.tasks or self.worked_on or self.unlinked)

    @property
    def from_tracker(self) -> bool:
        return bool(self.worked_on) or any(
            event.kind in TRACKER_KINDS for task in self.tasks for event in task.events
        )

    @property
    def from_vcs(self) -> bool:
        return bool(self.unlinked) or any(
            event.kind not in TRACKER_KINDS for task in self.tasks for event in task.events
        )


@dataclass(frozen=True, kw_only=True)
class TaskObservation:
    """What the tracker says of one task today, for the line beside what its people said."""

    key: str
    #: The tracker's status name now, and the stage it maps to.
    status: str | None
    stage: DeliveryStage | None
    #: When the task entered that status, by its history; None when not recorded.
    since: datetime | None
    #: The member who made that move, when the history names one.
    moved_by_member_id: str | None
    #: Other changes in the window, worded: "due date moved Oct 10 → Oct 14".
    changes: tuple[str, ...] = ()
    quiet_days: int | None = None

    @property
    def is_empty(self) -> bool:
        return self.since is None and not self.changes and self.quiet_days is None


def digest(
    member_id: str,
    since: datetime,
    until: datetime,
    *,
    issue_changes: Iterable[FactEvent],
    issue_facts: Iterable[FactEvent] = (),
    vcs_facts: Iterable[FactEvent] = (),
    task_updates: Iterable[FactEvent] = (),
    assigned_keys: Collection[str] = (),
    mapping: StageMapping = DEFAULT_STAGE_MAPPING,
    quiet_after_days: int = QUIET_AFTER_DAYS,
) -> ObservedDay:
    """The person's day as the tracker's history and Git saw it, per task.

    ``assigned_keys`` are the tasks the person holds; a task assigned to or
    away from them in the window is theirs for it too. ``issue_changes`` may
    reach back before ``since``: only events inside the window are listed, and
    the earlier ones tell how long a quiet task has not moved. ``issue_facts``
    give a task's broad state (``in_progress``...) and when its status became
    what it is; ``task_updates`` are the person's own task updates, and a task
    they updated is not called quiet.
    """
    assigned = {key.upper() for key in assigned_keys}
    changes = sorted(
        (fact for fact in issue_changes if fact.source == ISSUE_CHANGE_SOURCE),
        key=lambda fact: fact.observed_at,
    )
    window = [fact for fact in changes if since <= fact.observed_at <= until]
    events, worked_on = _tracker_events(member_id, window, assigned, mapping)
    unlinked: list[TaskEvent] = []
    for event in _vcs_events(vcs_facts, since, until):
        if event.key:
            events.setdefault(event.key, []).append(event)
        else:
            unlinked.append(event)

    tasks = [
        ObservedTask(key=key, events=tuple(_merged(sorted(found, key=lambda item: item.at))))
        for key, found in events.items()
    ]
    tasks = [task for task in tasks if task.events]
    tasks.sort(key=lambda task: max(event.at for event in task.events), reverse=True)

    updated = {
        str(fact.payload.get("issue_key") or fact.entity_ref.id).upper()
        for fact in task_updates
        if fact.source == TASK_UPDATE_SOURCE
        and fact.payload.get("developer_id") == member_id
        and since <= fact.observed_at <= until
    }
    moved = {task.key for task in tasks}
    timelines = _issue_timelines(issue_facts)
    quiet = [
        ObservedTask(key=key, quiet_days=days)
        for key in sorted(assigned - moved - updated)
        if (days := _quiet_days(key, changes, timelines.get(key), until, quiet_after_days))
        is not None
    ]
    quiet.sort(key=lambda task: (-(task.quiet_days or 0), task.key))

    return ObservedDay(
        member_id=member_id,
        since=since,
        until=until,
        tasks=tuple(tasks),
        worked_on=tuple(key for key in worked_on if key not in moved),
        quiet=tuple(quiet),
        unlinked=tuple(_merged(unlinked)),
    )


def _tracker_events(
    member_id: str,
    window: list[FactEvent],
    assigned: set[str],
    mapping: StageMapping,
) -> tuple[dict[str, list[TaskEvent]], list[str]]:
    """The window's tracker events per task the person holds, and the others they changed.

    A task assigned to or away from them in the window is theirs for it.
    """
    owned = set(assigned)
    owned.update(
        _key(fact)
        for fact in window
        if fact.payload.get("field") == "assignee"
        and member_id in (fact.payload.get("from_member_id"), fact.payload.get("to_member_id"))
    )
    events: dict[str, list[TaskEvent]] = {}
    last_worked: dict[str, datetime] = {}
    for fact in window:
        key = _key(fact)
        if not key:
            continue
        if key in owned:
            event = _tracker_event(fact, member_id, mapping)
            if event is not None:
                events.setdefault(key, []).append(event)
        elif fact.payload.get("author_member_id") == member_id:
            last_worked[key] = max(fact.observed_at, last_worked.get(key, fact.observed_at))
    # The issue they worked on last comes first, as the moved tasks do.
    worked_on = sorted(last_worked, key=lambda key: last_worked[key], reverse=True)
    return events, worked_on


def _quiet_days(
    key: str,
    changes: Iterable[FactEvent],
    timeline: list[FactEvent] | None,
    until: datetime,
    quiet_after_days: int,
) -> int | None:
    """Days a task under way has not moved, when at least ``quiet_after_days``; else None."""
    if not timeline or timeline[-1].payload.get("state") not in _UNDER_WAY_STATES:
        return None
    last = _last_movement(key, changes, timeline, until)
    if last is None:
        return None
    days = (until - last).days
    return days if days >= quiet_after_days else None


def lines(day: ObservedDay, *, limit: int = SUMMARY_TASK_LIMIT) -> tuple[str, ...]:
    """The digest as lines a person can read, one per task, most recent first.

    "CHK-4 moved to In Review (in development), merge request !12 opened",
    "CHK-7 resolved", "worked on IDP-2", "no movement on CHK-9 for 8 days".
    """
    out = [f"{task.key} {_joined(_event_words(task.events))}" for task in day.tasks[:limit]]
    if len(day.tasks) > limit:
        more = len(day.tasks) - limit
        out.append(f"{more} more {'task' if more == 1 else 'tasks'} moved")
    if day.unlinked:
        out.append(_joined(_event_words(day.unlinked)))
    if day.worked_on:
        named = list(day.worked_on[:SUMMARY_WORKED_ON_LIMIT])
        if len(day.worked_on) > len(named):
            named.append(f"{len(day.worked_on) - len(named)} more")
        out.append(f"worked on {_joined(named)}")
    out.extend(
        f"no movement on {task.key} for {task.quiet_days} days"
        for task in day.quiet[:SUMMARY_QUIET_LIMIT]
    )
    if len(day.quiet) > SUMMARY_QUIET_LIMIT:
        more = len(day.quiet) - SUMMARY_QUIET_LIMIT
        out.append(f"{more} more {'task' if more == 1 else 'tasks'} without movement")
    return tuple(out)


def observe_task(
    key: str,
    facts: Iterable[FactEvent],
    *,
    start: datetime,
    until: datetime,
    mapping: StageMapping = DEFAULT_STAGE_MAPPING,
    names: Mapping[str, str] | None = None,
    quiet_after_days: int = QUIET_AFTER_DAYS,
) -> TaskObservation:
    """What the tracker says of one task: its status since when, and today's other changes.

    ``facts`` are the task's own facts (its issue and ``issue_change`` facts).
    ``start`` opens "today": changes from then to ``until`` are worded. An
    assignment names the member it went to by ``names`` (member id to name),
    and is "reassigned" when they are not a member.
    """
    wanted = key.upper()
    own = sorted(
        (
            fact
            for fact in facts
            if fact.observed_at <= until
            and fact.source in {ISSUE_CHANGE_SOURCE, ISSUE_SOURCE}
            and (_key(fact) or fact.entity_ref.id.upper()) == wanted
        ),
        key=lambda fact: fact.observed_at,
    )
    changes = [fact for fact in own if fact.source == ISSUE_CHANGE_SOURCE]
    timeline = [fact for fact in own if fact.source == ISSUE_SOURCE]
    moves = [fact for fact in changes if fact.payload.get("field") == "status"]
    status = _text(moves[-1].payload.get("to")) if moves else None
    if timeline and _text(timeline[-1].payload.get("status")):
        status = _text(timeline[-1].payload.get("status"))
    since: datetime | None = None
    moved_by: str | None = None
    if moves and status and _same(_text(moves[-1].payload.get("to")), status):
        since = moves[-1].observed_at
        moved_by = _text(moves[-1].payload.get("author_member_id"))
    stage = _stage(status, mapping) if status else None

    today = [fact for fact in changes if start <= fact.observed_at]
    words = [
        word
        for fact in today
        if fact.payload.get("field") != "status"
        and (word := _change_word(fact, mapping, names or {})) is not None
    ]
    return TaskObservation(
        key=wanted,
        status=status,
        stage=stage,
        since=since,
        moved_by_member_id=moved_by,
        changes=tuple(dict.fromkeys(words)),
        quiet_days=(
            None if today else _quiet_days(wanted, changes, timeline, until, quiet_after_days)
        ),
    )


def _change_word(fact: FactEvent, mapping: StageMapping, names: Mapping[str, str]) -> str | None:
    """One change on a task's line; an assignment names the member it went to."""
    if fact.payload.get("field") == "assignee":
        to_member = _text(fact.payload.get("to_member_id"))
        if fact.payload.get("to") is None:
            return "unassigned"
        if to_member is not None and names.get(to_member):
            return f"assigned to {names[to_member]}"
        return "reassigned"
    event = _tracker_event(fact, None, mapping)
    return _event_word(event) if event is not None else None


def stage_words(stage: DeliveryStage | None) -> str | None:
    """ "in development": a stage as a sentence carries it."""
    if stage is None:
        return None
    label = STAGE_LABELS[stage]
    return label[0].lower() + label[1:]


# --- events -------------------------------------------------------------------


def _tracker_event(
    fact: FactEvent, member_id: str | None, mapping: StageMapping
) -> TaskEvent | None:
    """The event a kept field change is, or None for one the digest does not name."""
    payload = fact.payload
    before, after = _text(payload.get("from")), _text(payload.get("to"))
    kind = _change_kind(payload, before, after, member_id)
    if kind is None:
        return None
    moved = kind is EventKind.MOVED
    return TaskEvent(
        key=_key(fact),
        kind=kind,
        at=fact.observed_at,
        from_value=before,
        to_value=after,
        from_stage=_stage(before, mapping) if moved else None,
        to_stage=_stage(after, mapping) if moved else None,
        author_member_id=_text(payload.get("author_member_id")),
    )


def _change_kind(
    payload: Mapping[str, JsonScalar],
    before: str | None,
    after: str | None,
    member_id: str | None,
) -> EventKind | None:
    field_name = payload.get("field")
    if field_name == "status":
        return None if after is None or _same(before, after) else EventKind.MOVED
    if field_name == "resolution":
        if (after is None) == (before is None):
            return None
        return EventKind.RESOLVED if after is not None else EventKind.REOPENED
    if field_name == "fix_version":
        if not (after or before):
            return None
        return EventKind.RELEASE_ADDED if after else EventKind.RELEASE_REMOVED
    if field_name == "assignee":
        if member_id is not None and payload.get("to_member_id") == member_id:
            return EventKind.ASSIGNED_TO
        if member_id is not None and payload.get("from_member_id") == member_id:
            return EventKind.ASSIGNED_AWAY
        return None
    return _VALUE_KINDS.get(str(field_name))


#: Fields whose change is an event from one value to another.
_VALUE_KINDS: Mapping[str, EventKind] = {
    "due_date": EventKind.DUE_DATE,
    "story_points": EventKind.ESTIMATE,
}


def _vcs_events(facts: Iterable[FactEvent], since: datetime, until: datetime) -> list[TaskEvent]:
    """Merge requests opened, reviewed or merged in the window, and commits naming a task."""
    found: dict[tuple[str, str, EventKind], TaskEvent] = {}
    commits: dict[str, TaskEvent] = {}
    for fact in sorted(facts, key=lambda item: item.observed_at):
        payload = fact.payload
        if fact.source == MERGE_REQUEST_SOURCE:
            repo, number = _text(payload.get("repo")), _text(payload.get("id"))
            if repo is None or number is None:
                continue
            keys = _keys_in(payload.get("source_branch"), payload.get("title")) or [""]
            reference = f"{_sigil(payload)}{number}"
            times = (
                (EventKind.MR_OPENED, _when(payload.get("opened_at"))),
                (EventKind.MR_REVIEWED, _when(payload.get("first_review_at"))),
                (EventKind.MR_REVIEWED, _when(payload.get("last_review_at"))),
                (
                    EventKind.MR_MERGED,
                    _when(payload.get("merged_at"))
                    or (fact.observed_at if payload.get("merged") is True else None),
                ),
            )
            for kind, at in times:
                if at is None or not since <= at <= until:
                    continue
                for key in keys:
                    found.setdefault(
                        (key, f"{repo}{reference}", kind),
                        TaskEvent(key=key, kind=kind, at=at, reference=reference),
                    )
        elif fact.source == COMMIT_SOURCE and since <= fact.observed_at <= until:
            for key in _keys_in(payload.get("message")):
                earlier = commits.get(key)
                commits[key] = TaskEvent(
                    key=key,
                    kind=EventKind.COMMITS,
                    at=max(fact.observed_at, earlier.at) if earlier else fact.observed_at,
                    count=(earlier.count + 1) if earlier else 1,
                )
    return [*found.values(), *commits.values()]


def _merged(events: list[TaskEvent]) -> list[TaskEvent]:
    """One event per kind and reference: several moves read as one from first to last.

    A resolution next to a move into the same stage says nothing more; a move
    that ends where it started is no move.
    """
    out: list[TaskEvent] = []
    moves = [event for event in events if event.kind is EventKind.MOVED]
    if moves:
        first, last = moves[0], moves[-1]
        if not _same(first.from_value, last.to_value):
            out.append(
                TaskEvent(
                    key=last.key,
                    kind=EventKind.MOVED,
                    at=last.at,
                    from_value=first.from_value,
                    to_value=last.to_value,
                    from_stage=first.from_stage,
                    to_stage=last.to_stage,
                    author_member_id=last.author_member_id,
                )
            )
    seen: dict[tuple[EventKind, str | None], TaskEvent] = {}
    for event in events:
        if event.kind is EventKind.MOVED:
            continue
        if event.kind in {EventKind.RESOLVED, EventKind.REOPENED} and out:
            continue
        slot = (event.kind, event.reference)
        earlier = seen.get(slot)
        if earlier is not None and event.kind in {EventKind.DUE_DATE, EventKind.ESTIMATE}:
            # The window's first value to its last.
            event = TaskEvent(
                key=event.key,
                kind=event.kind,
                at=event.at,
                from_value=earlier.from_value,
                to_value=event.to_value,
                author_member_id=event.author_member_id,
            )
        seen[slot] = event
    rest = [
        event
        for event in seen.values()
        if not (
            event.kind in {EventKind.DUE_DATE, EventKind.ESTIMATE}
            and _same(event.from_value, event.to_value)
        )
    ]
    out.extend(sorted(rest, key=lambda event: (_KIND_ORDER[event.kind], event.at)))
    return out


_KIND_ORDER: Mapping[EventKind, int] = {kind: index for index, kind in enumerate(EventKind)}


def _event_words(events: Iterable[TaskEvent]) -> list[str]:
    return [_event_word(event) for event in events]


def _event_word(event: TaskEvent) -> str:
    return _EVENT_WORDS[event.kind](event)


def _due_words(event: TaskEvent) -> str:
    if event.to_value is None:
        return f"due date {_day(event.from_value)} removed"
    if event.from_value is None:
        return f"due date set to {_day(event.to_value)}"
    return f"due date moved {_day(event.from_value)} → {_day(event.to_value)}"


def _estimate_words(event: TaskEvent) -> str:
    if event.from_value is None:
        return f"estimated at {_number(event.to_value)}"
    if event.to_value is None:
        return "estimate removed"
    return f"re-estimated {_number(event.from_value)} → {_number(event.to_value)}"


def _commit_words(event: TaskEvent) -> str:
    return "named in 1 commit" if event.count == 1 else f"named in {event.count} commits"


def _move_words(event: TaskEvent) -> str:
    """ "moved to In Review (in development)"; across stages "(in development → in testing)"."""
    to_stage, from_stage = event.to_stage, event.from_stage
    back = (
        to_stage is not None
        and from_stage is not None
        and STAGE_ORDER.index(to_stage) < STAGE_ORDER.index(from_stage)
    )
    verb = "moved back to" if back else "moved to"
    stage = stage_words(to_stage)
    if stage is None:
        return f"{verb} {event.to_value}"
    earlier = stage_words(from_stage)
    if earlier is not None and earlier != stage:
        return f"{verb} {event.to_value} ({earlier} → {stage})"
    return f"{verb} {event.to_value} ({stage})"


_EVENT_WORDS: Mapping[EventKind, Callable[[TaskEvent], str]] = {
    EventKind.MOVED: _move_words,
    EventKind.RESOLVED: lambda _: "resolved",
    EventKind.REOPENED: lambda _: "reopened",
    EventKind.DUE_DATE: _due_words,
    EventKind.ESTIMATE: _estimate_words,
    EventKind.ASSIGNED_TO: lambda _: "assigned to them",
    EventKind.ASSIGNED_AWAY: lambda _: "assigned away from them",
    EventKind.RELEASE_ADDED: lambda event: (
        f"moved from release {event.from_value} to {event.to_value}"
        if event.from_value
        else f"added to release {event.to_value}"
    ),
    EventKind.RELEASE_REMOVED: lambda event: f"taken out of release {event.from_value}",
    EventKind.MR_OPENED: lambda event: f"merge request {event.reference} opened",
    EventKind.MR_REVIEWED: lambda event: f"merge request {event.reference} reviewed",
    EventKind.MR_MERGED: lambda event: f"merge request {event.reference} merged",
    EventKind.COMMITS: _commit_words,
}


# --- helpers ------------------------------------------------------------------


def _issue_timelines(facts: Iterable[FactEvent]) -> dict[str, list[FactEvent]]:
    timelines: dict[str, list[FactEvent]] = {}
    for fact in sorted(facts, key=lambda item: item.observed_at):
        if fact.source != ISSUE_SOURCE:
            continue
        key = _key(fact) or fact.entity_ref.id.upper()
        timelines.setdefault(key, []).append(fact)
    return timelines


def _last_movement(
    key: str, changes: Iterable[FactEvent], timeline: list[FactEvent], until: datetime
) -> datetime | None:
    """When the task last moved: its last kept change, else when its status became what it is."""
    moved = [
        fact.observed_at
        for fact in changes
        if _key(fact) == key
        and fact.observed_at <= until
        and fact.payload.get("field") in _MOVEMENT_FIELDS
    ]
    if moved:
        return max(moved)
    current = [fact for fact in timeline if fact.observed_at <= until]
    if not current:
        return None
    status = current[-1].payload.get("status") or current[-1].payload.get("state")
    since = current[-1].observed_at
    for fact in reversed(current):
        if (fact.payload.get("status") or fact.payload.get("state")) != status:
            break
        since = fact.observed_at
    return since


_MOVEMENT_FIELDS = frozenset(
    {"status", "resolution", "due_date", "story_points", "assignee", "fix_version"}
)


def _stage(status: str | None, mapping: StageMapping) -> DeliveryStage | None:
    if not status or mapping.excludes(status):
        return None
    return mapping.stage_of(status)


def _key(fact: FactEvent) -> str:
    value = fact.payload.get("key")
    return value.upper() if isinstance(value, str) and value else ""


def _keys_in(*values: JsonScalar) -> list[str]:
    text = " ".join(value for value in values if isinstance(value, str))
    return list(dict.fromkeys(match.upper() for match in _ISSUE_KEY.findall(text)))


def _sigil(payload: Mapping[str, JsonScalar]) -> str:
    web_url = payload.get("web_url")
    return "!" if isinstance(web_url, str) and "/merge_requests/" in web_url else "#"


def _text(value: JsonScalar) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _same(left: str | None, right: str | None) -> bool:
    return (left or "").strip().casefold() == (right or "").strip().casefold()


def _when(value: JsonScalar) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _day(value: str | None) -> str:
    """``Oct 10``: the date format status summaries use."""
    if value is None:
        return "?"
    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        return value
    return f"{day:%b} {day.day}"


def _number(value: str | None) -> str:
    if value is None:
        return "?"
    try:
        number = float(value)
    except ValueError:
        return value
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _joined(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def window_start(
    previous_checkin_at: datetime | None,
    last_answer_at: datetime | None,
    *,
    until: datetime,
    fallback: timedelta = timedelta(days=1),
) -> datetime:
    """Where a digest starts: the later of the last answer and the previous check-in time.

    Without either, a day before ``until``.
    """
    candidates = [value for value in (previous_checkin_at, last_answer_at) if value is not None]
    start = max(candidates) if candidates else until - fallback
    return min(start, until)
