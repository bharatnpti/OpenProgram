"""A developer's update of one task, from the console or said in chat.

The console's Today > Your tasks updates one task at a time: its state, the
person's ETA, a blocker added or resolved, a note, and optionally a move of
the tracker issue. Each part lands where its kind already lives, so nothing
new is stored and no reader changes its rules:

- A blocker goes through the blocker lifecycle as a partial statement
  (``ReconcileMode.CHECKIN``): the person's other blockers carry forward
  untouched, and the day's status carries the open set.
- An ETA is an ``eta_stated`` fact (``checkin_drift``), the one a check-in
  records per issue, so the forecast's "The team says" and the ETA drift read
  it as they read a chat ETA. A cleared ETA has null days.
- The state and the note are a ``task_update`` fact on the task. A finalized
  chat check-in writes one per issue it named (``via: "chat"``, state only:
  a fact never carries reply or claim text).
- The day's status: one is written when no reply is on record that day,
  source ``partial`` (a reply, never green), with a fixed lead; an existing
  reply keeps its source and summary.
- The tracker moves only behind the write-back gates
  (``WriteBackService.apply_from_console``), and never takes the ETA.
- The person's trees are rolled up again at once (``PersonRollups``), so their
  pod reads the update without waiting for the hourly rollup.

A task's colour never comes from these facts: ``PersonaViewService`` reads it
from the tracker and the rollup only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import uuid4

from core.application.blocker_lifecycle import (
    BlockerLifecycleService,
    reconciliation_with_updates,
)
from core.application.blocker_resolution import BlockerResolutionService
from core.application.checkin_drift import (
    CHECKIN_DRIFT_FACT_SOURCE,
    ETA_STATED,
    IssueEta,
    eta_stated_fact,
)
from core.application.rollup_service import PersonRollups, refresh_person_rollups
from core.application.status_summaries import TASK_UPDATE_LEAD, day_label
from core.application.writeback_service import (
    ConsoleWriteBack,
    WriteBackService,
    canonical_target_state,
    claim_reads_done,
)
from core.domain.blockers import (
    BlockerReconciliation,
    BlockerReport,
    BlockerSource,
    DeveloperBlocker,
    ReconcileMode,
    blocker_descriptions,
    normalize_blocker_key,
)
from core.domain.errors import GraphNotFound, OpenProgramError
from core.domain.graph import EntityRef, FactEvent, GraphNode, JsonScalar, NodeKind
from core.domain.status import CheckInSignals, DeveloperStatus, IssueClaim, StatusSource
from core.domain.writeback import WriteBackTarget
from core.ports.repositories import GraphRepository, StatusRepository, TimeSeriesRepository

TASK_UPDATE_FACT_SOURCE = "task_update"
TASK_UPDATE = "task_update"
# Where a task update was made.
TaskUpdateVia = Literal["console", "chat"]
CONSOLE: TaskUpdateVia = "console"
CHAT: TaskUpdateVia = "chat"
# The longest note or blocker a console update takes.
TASK_UPDATE_TEXT_MAX = 500
# How far back a task's own facts are read: the persona views' window.
TASK_FACT_LOOKBACK = timedelta(days=30)
# Statuses that record no reply: a console update that day is the first one.
_NO_REPLY_SOURCES = frozenset({StatusSource.INFERRED, StatusSource.STALE, StatusSource.UNKNOWN})
_STATE_WORDS: dict[WriteBackTarget, str] = {
    WriteBackTarget.TODO: "to do",
    WriteBackTarget.IN_PROGRESS: "in progress",
    WriteBackTarget.IN_REVIEW: "in review",
    WriteBackTarget.BLOCKED: "blocked",
    WriteBackTarget.DONE: "done",
}
_TODAY_ONLY = "A task is updated for today only: earlier days stay as they were recorded."
_NOTHING_SENT = (
    "Nothing to update: send a state, an ETA, a note, a blocker to add or one to resolve."
)
_ETA_BEFORE_TODAY = "The ETA can't be before today."
_BLOCKED_NEEDS_A_BLOCKER = (
    "Blocked needs a blocker: add one, or keep one of the task's open blockers."
)
TASK_NOT_ASSIGNED = "task is not assigned to you"


class TaskNotAssigned(OpenProgramError):
    """The task is not in the caller's own program tree (their focus)."""


class TaskUpdateRejected(OpenProgramError):
    """The update breaks one of its rules; the message says which, in plain words."""


@dataclass(frozen=True, kw_only=True)
class TaskUpdate:
    """What a person changed on one task. Only what is sent changes.

    ``eta_sent`` tells "no ETA change" from "clear my ETA": with it set, an
    ``eta`` of None clears the ETA.
    """

    state: WriteBackTarget | None = None
    eta: date | None = None
    eta_sent: bool = False
    note: str | None = None
    add_blocker: str | None = None
    resolve_blocker_ids: tuple[str, ...] = ()
    move_in_tracker: bool = False

    @property
    def changes_nothing(self) -> bool:
        return (
            self.state is None
            and not self.eta_sent
            and self.note is None
            and self.add_blocker is None
            and not self.resolve_blocker_ids
        )


@dataclass(frozen=True, kw_only=True)
class TaskUpdateResult:
    task_id: str
    status: DeveloperStatus
    # None when no tracker move was asked for (no tick, or no state sent).
    tracker: ConsoleWriteBack | None


@dataclass(frozen=True, kw_only=True)
class TaskStatement:
    """The last thing a person said about a task: a state or a note, when and where."""

    state: WriteBackTarget | None
    note: str | None
    at: datetime
    via: TaskUpdateVia


@dataclass(frozen=True, kw_only=True)
class StatedEta:
    """A person's own current ETA for a task: its last day, and the words it was given in."""

    day: date
    label: str


def task_key(task: GraphNode) -> str:
    """The issue key a task's facts use: the tracker key, else the node id."""
    key = task.metadata.get("key")
    return key if isinstance(key, str) and key else task.id


def task_label(task: GraphNode) -> str:
    """How a summary names a task: its tracker key, else its name."""
    key = task.metadata.get("key")
    return key if isinstance(key, str) and key else task.name


def stated_task_state(claim: IssueClaim) -> WriteBackTarget | None:
    """The canonical state a chat claim gives its issue, as the write-back reads it."""
    state = canonical_target_state(claim.claimed_state)
    if state is None and claim_reads_done(claim):
        return WriteBackTarget.DONE
    return state


def task_update_fact(
    *,
    tenant_id: str,
    task_id: str,
    issue_key: str,
    task_label: str,
    developer_id: str,
    developer_name: str,
    as_of: date,
    observed_at: datetime,
    correlation_id: str,
    via: TaskUpdateVia,
    state: WriteBackTarget | None,
    note: str | None = None,
    eta_changed: bool = False,
    eta: date | None = None,
    eta_change_days: int | None = None,
    blocker_added: bool = False,
    blockers_resolved: int = 0,
) -> FactEvent:
    """One update of a task by its person, on the task.

    ``note`` is what the person typed in the console. A chat update never
    carries one: facts never hold reply or claim text, and a chat note would
    reach the persona views through this fact.
    """
    payload: dict[str, JsonScalar] = {
        "kind": TASK_UPDATE,
        "task_id": task_id,
        "issue_key": issue_key,
        "task_label": task_label,
        "developer_id": developer_id,
        "developer_name": developer_name,
        "as_of": as_of.isoformat(),
        "via": via,
        "state": state.value if state is not None else None,
        "note": note,
        "eta_changed": eta_changed,
        "eta_date": eta.isoformat() if eta is not None else None,
        "eta_change_days": eta_change_days,
        "blocker_added": blocker_added,
        "blockers_resolved": blockers_resolved,
    }
    return FactEvent(
        tenant_id=tenant_id,
        source=TASK_UPDATE_FACT_SOURCE,
        entity_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=task_id),
        payload=payload,
        observed_at=observed_at,
        correlation_id=f"{correlation_id}:{TASK_UPDATE}:{issue_key}",
    )


def last_statement(
    facts: Iterable[FactEvent], developer_id: str, as_of: date
) -> TaskStatement | None:
    """The person's latest statement on a task up to ``as_of``: a state or a note.

    An update that only moved the ETA or a blocker states neither, so the
    statement before it still stands.
    """
    latest: TaskStatement | None = None
    for fact in _in_order(facts):
        payload = fact.payload
        if fact.source != TASK_UPDATE_FACT_SOURCE or payload.get("developer_id") != developer_id:
            continue
        if not _on_or_before(payload, as_of):
            continue
        state = _target(payload.get("state"))
        note = _text(payload, "note")
        if state is None and note is None:
            continue
        latest = TaskStatement(
            state=state,
            note=note,
            at=fact.observed_at,
            via=CHAT if payload.get("via") == CHAT else CONSOLE,
        )
    return latest


def own_eta(facts: Iterable[FactEvent], developer_id: str, as_of: date) -> StatedEta | None:
    """The person's own latest ETA for a task up to ``as_of``; None once they cleared it."""
    latest: StatedEta | None = None
    for fact in _in_order(facts):
        payload = fact.payload
        if (
            fact.source != CHECKIN_DRIFT_FACT_SOURCE
            or payload.get("kind") != ETA_STATED
            or payload.get("developer_id") != developer_id
            or not _on_or_before(payload, as_of)
        ):
            continue
        day = _iso_date(payload.get("eta_date"))
        latest = (
            StatedEta(day=day, label=_text(payload, "eta_label") or day_label(day))
            if day is not None
            else None
        )
    return latest


def task_update_summary(updates: Sequence[FactEvent]) -> str:
    """The day's status summary built from its console task updates, under the fixed lead.

    "Updated tasks in OpenProgram: CHK-4 in review, ETA Oct 9; CHK-5 blocker
    added." Each task once, in the order first updated, with its latest state
    and ETA of the day. Notes stay on the task row.
    """
    tasks: dict[str, _DayOfTask] = {}
    for fact in _in_order(updates):
        payload = fact.payload
        task_id = _text(payload, "task_id") or fact.entity_ref.id
        day = tasks.setdefault(task_id, _DayOfTask(label=_text(payload, "task_label") or task_id))
        day.add(payload)
    if not tasks:
        return TASK_UPDATE_LEAD.rstrip(":") + "."
    return f"{TASK_UPDATE_LEAD} {'; '.join(day.phrase() for day in tasks.values())}."


def largest_slip(changes: Iterable[int | None]) -> int | None:
    """The largest ETA change in days: the biggest slip, else the biggest pull-in.

    ``eta_change_days`` is how far an ETA moved, positive when later; a change
    of nothing is no change.
    """
    moved = [days for days in changes if days]
    if not moved:
        return None
    return max(moved, key=lambda days: (days > 0, abs(days)))


async def task_facts(
    time_series: TimeSeriesRepository, task: GraphNode, as_of: date
) -> list[FactEvent]:
    """A task's facts over the lookback window, under its node id and its issue key."""
    since = datetime.combine(as_of - TASK_FACT_LOOKBACK, time.min, tzinfo=UTC)
    facts: list[FactEvent] = []
    for entity_id in dict.fromkeys((task.id, task_key(task))):
        facts += await time_series.list_facts(
            task.tenant_id,
            EntityRef(tenant_id=task.tenant_id, kind=NodeKind.TASK, id=entity_id),
            since,
        )
    return facts


class TaskUpdateService:
    """Apply one person's update of one of their tasks, for today."""

    def __init__(
        self,
        *,
        graph_repository: GraphRepository,
        status_repository: StatusRepository,
        time_series_repository: TimeSeriesRepository,
        blocker_lifecycle: BlockerLifecycleService | None = None,
        blocker_resolution: BlockerResolutionService | None = None,
        write_back: WriteBackService | None = None,
        rollups: PersonRollups | None = None,
        tracker_name: str = "the issue tracker",
        clock: Callable[[], datetime] | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._graph = graph_repository
        self._status = status_repository
        self._facts = time_series_repository
        self._blockers = blocker_lifecycle or BlockerLifecycleService(
            status_repository, graph_repository
        )
        self._resolution = blocker_resolution or BlockerResolutionService(
            graph_repository, status_repository
        )
        self._write_back = write_back
        self._rollups = rollups
        self._tracker_name = tracker_name
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._today = today or date.today

    async def update(
        self,
        tenant_id: str,
        developer_id: str,
        task_id: str,
        as_of: date,
        update: TaskUpdate,
    ) -> TaskUpdateResult:
        """Record the update and return today's status; refuse it whole when a rule fails.

        Every rule is checked before anything is written: today only, at
        least one change, the task in the person's own tree (else
        ``TaskNotAssigned``), an ETA of today or later, each blocker to
        resolve an open blocker of this task, and Blocked with a blocker.
        """
        today = self._today()
        if as_of != today:
            raise TaskUpdateRejected(_TODAY_ONLY)
        if update.changes_nothing:
            raise TaskUpdateRejected(_NOTHING_SENT)
        developer, task = await self._own_task(tenant_id, developer_id, task_id, today)
        if update.eta_sent and update.eta is not None and update.eta < today:
            raise TaskUpdateRejected(_ETA_BEFORE_TODAY)
        existing = await self._status.latest_developer_status(tenant_id, developer_id, today)
        prior = await self._blockers.open_blockers(
            tenant_id, developer_id, today, legacy_status=existing
        )
        on_task = await self._task_blocker_ids(tenant_id, developer_id, today, task, prior)
        resolving = tuple(dict.fromkeys(update.resolve_blocker_ids))
        unknown = [blocker_id for blocker_id in resolving if blocker_id not in on_task]
        if unknown:
            raise TaskUpdateRejected(
                f"Not an open blocker of this task: {', '.join(unknown)}. Reload the task "
                "and try again."
            )
        if (
            update.state is WriteBackTarget.BLOCKED
            and update.add_blocker is None
            and not set(on_task) - set(resolving)
        ):
            raise TaskUpdateRejected(_BLOCKED_NEEDS_A_BLOCKER)

        now = self._clock()
        correlation_id = f"console:{uuid4().hex}"
        key = task_key(task)
        reconciliation, open_after = await self._reconcile_blockers(
            tenant_id=tenant_id,
            developer_id=developer_id,
            today=today,
            task=task,
            prior=prior,
            resolving=resolving,
            add_blocker=update.add_blocker,
            correlation_id=correlation_id,
        )
        slip: int | None = None
        if update.eta_sent:
            previous = own_eta(await task_facts(self._facts, task, today), developer_id, today)
            await self._facts.append_fact(
                eta_stated_fact(
                    tenant_id=tenant_id,
                    issue_key=key,
                    eta=(
                        IssueEta(label=day_label(update.eta), day=update.eta)
                        if update.eta is not None
                        else None
                    ),
                    developer_id=developer_id,
                    developer_name=developer.name,
                    as_of=today,
                    observed_at=now,
                    correlation_id=correlation_id,
                )
            )
            if previous is not None and update.eta is not None:
                slip = (update.eta - previous.day).days or None
        await self._facts.append_fact(
            task_update_fact(
                tenant_id=tenant_id,
                task_id=task.id,
                issue_key=key,
                task_label=task_label(task),
                developer_id=developer_id,
                developer_name=developer.name,
                as_of=today,
                observed_at=now,
                correlation_id=correlation_id,
                via=CONSOLE,
                state=update.state,
                note=update.note,
                eta_changed=update.eta_sent,
                eta=update.eta if update.eta_sent else None,
                eta_change_days=slip,
                blocker_added=update.add_blocker is not None,
                blockers_resolved=len(resolving),
            )
        )
        status = await self._todays_status(tenant_id, developer_id, today, existing, open_after)
        await self._blockers.persist_with_status(status, reconciliation)
        tracker: ConsoleWriteBack | None = None
        if update.move_in_tracker and update.state is not None and self._write_back is not None:
            tracker = await self._write_back.apply_from_console(
                tenant_id=tenant_id,
                developer_id=developer_id,
                correlation_id=correlation_id,
                issue_key=key,
                target=update.state,
                reported_on=today,
                tracker_name=self._tracker_name,
            )
        await refresh_person_rollups(self._rollups, tenant_id, developer_id, today)
        return TaskUpdateResult(task_id=task.id, status=status, tracker=tracker)

    async def _own_task(
        self, tenant_id: str, developer_id: str, task_id: str, as_of: date
    ) -> tuple[GraphNode, GraphNode]:
        """The person and the task, when the task is in their own tree (their focus)."""
        try:
            tree = await self._graph.get_program_tree(tenant_id, developer_id, as_of)
        except GraphNotFound as exc:
            raise TaskNotAssigned(TASK_NOT_ASSIGNED) from exc
        if tree.root.kind is not NodeKind.DEVELOPER:
            raise TaskNotAssigned(TASK_NOT_ASSIGNED)
        for node in tree.nodes:
            if node.kind is NodeKind.TASK and node.id == task_id:
                return tree.root, node
        raise TaskNotAssigned(TASK_NOT_ASSIGNED)

    async def _task_blocker_ids(
        self,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        task: GraphNode,
        prior: Sequence[DeveloperBlocker],
    ) -> tuple[str, ...]:
        """The person's open blockers on this task: recorded on it, or read as on it.

        The second is how the task row shows them (``work_item_id`` of the
        blocker details): a blocker recorded on the issue it waits on counts on
        the person's own issue its text names.
        """
        ids = [blocker.blocker_id for blocker in prior if blocker.work_item_id == task.id]
        for resolved in await self._resolution.open_blockers_for_developer(
            tenant_id, developer_id, as_of
        ):
            if (
                resolved.work_item_ref is not None
                and resolved.work_item_ref.id == task.id
                and resolved.blocker_id not in ids
            ):
                ids.append(resolved.blocker_id)
        return tuple(ids)

    async def _reconcile_blockers(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        today: date,
        task: GraphNode,
        prior: tuple[DeveloperBlocker, ...],
        resolving: tuple[str, ...],
        add_blocker: str | None,
        correlation_id: str,
    ) -> tuple[BlockerReconciliation, tuple[DeveloperBlocker, ...]]:
        """Resolve and add this task's blockers; every other blocker carries forward untouched.

        A partial statement (``ReconcileMode.CHECKIN``) over the blockers in
        play: this task's, the ones being resolved, and the unattributed ones
        (legacy rows included, which must be persisted on the first write).
        The added blocker is matched by wording among those only, then
        attributed to the task. Matching it by the task instead, as a chat
        report with an issue key is, would take the task's existing open
        blocker and reword it: a second blocker added to a task would replace
        the first. Returns the reconciliation and the person's whole open set.
        """
        resolving_ids = set(resolving)
        in_play = tuple(
            blocker
            for blocker in prior
            if blocker.blocker_id in resolving_ids
            or blocker.work_item_id == task.id
            or not blocker.is_attributed
        )
        in_play_ids = {blocker.blocker_id for blocker in in_play}
        untouched = tuple(blocker for blocker in prior if blocker.blocker_id not in in_play_ids)
        reports = tuple(
            BlockerReport(
                description=blocker.description, blocker_id=blocker.blocker_id, resolved=True
            )
            for blocker in in_play
            if blocker.blocker_id in resolving_ids
        )
        if add_blocker is not None:
            reports += (BlockerReport(description=add_blocker),)
        reconciliation = await self._blockers.reconcile(
            tenant_id=tenant_id,
            developer_id=developer_id,
            as_of=today,
            prior=in_play,
            signals=CheckInSignals(
                progress_note="",
                blockers=(add_blocker,) if add_blocker is not None else (),
                blocker_reports=reports,
            ),
            mode=ReconcileMode.CHECKIN,
            source=BlockerSource.CORRECTION,
            source_correlation_id=correlation_id,
        )
        if add_blocker is not None:
            wording = normalize_blocker_key(add_blocker)
            added = next(
                (
                    blocker
                    for blocker in reconciliation.open_after
                    if blocker.normalized_key == wording
                ),
                None,
            )
            if added is not None and added.work_item_id is None:
                reconciliation = reconciliation_with_updates(
                    reconciliation, (replace(added, work_item_id=task.id),)
                )
        return reconciliation, untouched + reconciliation.open_after

    async def _todays_status(
        self,
        tenant_id: str,
        developer_id: str,
        today: date,
        existing: DeveloperStatus | None,
        open_after: tuple[DeveloperBlocker, ...],
    ) -> DeveloperStatus:
        """Today's status after the update.

        No reply on record today (no status, or a non-response one): a partial
        status, a reply that is never green, summarised from today's updates
        under ``TASK_UPDATE_LEAD``. A status of today keeps its source and
        summary, unless it is one of these, whose summary is rebuilt. Either
        way it carries the open blockers, and ``eta_change_days`` becomes the
        largest ETA change of the day's updates.
        """
        updates = await self._todays_updates(tenant_id, developer_id, today)
        slips = [_int(update.payload.get("eta_change_days")) for update in updates]
        blockers = blocker_descriptions(open_after)
        todays = existing if existing is not None and existing.as_of == today else None
        if todays is None or todays.source in _NO_REPLY_SOURCES:
            return DeveloperStatus(
                tenant_id=tenant_id,
                developer_id=developer_id,
                as_of=today,
                source=StatusSource.PARTIAL,
                blockers=blockers,
                summary=task_update_summary(updates),
                eta_change_days=largest_slip(slips),
            )
        return replace(
            todays,
            blockers=blockers,
            summary=(
                task_update_summary(updates)
                if todays.summary.startswith(TASK_UPDATE_LEAD)
                else todays.summary
            ),
            eta_change_days=largest_slip([todays.eta_change_days, *slips]),
        )

    async def _todays_updates(
        self, tenant_id: str, developer_id: str, today: date
    ) -> list[FactEvent]:
        facts = await self._facts.list_recent_facts(
            tenant_id,
            since=datetime.combine(today - timedelta(days=1), time.min, tzinfo=UTC),
            sources=(TASK_UPDATE_FACT_SOURCE,),
            limit=1000,
        )
        return [
            fact
            for fact in _in_order(facts)
            if fact.payload.get("developer_id") == developer_id
            and fact.payload.get("via") == CONSOLE
            and fact.payload.get("as_of") == today.isoformat()
        ]


@dataclass
class _DayOfTask:
    label: str
    state: WriteBackTarget | None = None
    eta_changed: bool = False
    eta: date | None = None
    added: int = 0
    resolved: int = 0

    def add(self, payload: Mapping[str, JsonScalar]) -> None:
        state = _target(payload.get("state"))
        if state is not None:
            self.state = state
        if payload.get("eta_changed") is True:
            self.eta_changed = True
            self.eta = _iso_date(payload.get("eta_date"))
        if payload.get("blocker_added") is True:
            self.added += 1
        self.resolved += _int(payload.get("blockers_resolved")) or 0

    def phrase(self) -> str:
        parts: list[str] = []
        if self.state is not None:
            parts.append(_STATE_WORDS[self.state])
        if self.eta_changed:
            parts.append(f"ETA {day_label(self.eta)}" if self.eta is not None else "ETA cleared")
        if self.added:
            parts.append(_counted(self.added, "blocker added", "blockers added"))
        if self.resolved:
            parts.append(_counted(self.resolved, "blocker resolved", "blockers resolved"))
        return f"{self.label} {', '.join(parts) if parts else 'updated'}"


def _counted(count: int, one: str, many: str) -> str:
    return one if count == 1 else f"{count} {many}"


def _in_order(facts: Iterable[FactEvent]) -> list[FactEvent]:
    return sorted(facts, key=lambda fact: (fact.observed_at, fact.ingested_at))


def _on_or_before(payload: Mapping[str, JsonScalar], as_of: date) -> bool:
    stated = _iso_date(payload.get("as_of"))
    return stated is None or stated <= as_of


def _target(value: object) -> WriteBackTarget | None:
    if not isinstance(value, str):
        return None
    try:
        return WriteBackTarget(value)
    except ValueError:
        return None


def _text(payload: Mapping[str, JsonScalar], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _iso_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
