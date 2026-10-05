"""Why each colour on the portfolio is what it is, said so a director reads it at a glance.

The heat map's ``why`` names a cell's drivers in one line for every reader of
the map. Exec Today needs them shorter and plainer: a reason a tile carries
under its colour ("3 of 4 unanswered today", "Blocker on SHOP-8 (Ada)"), every
reason behind it for the tooltip, one sentence for the program's headline,
and the few items a director should act on. They all come from here, from one
reading of the day, so the tiles, the headline and the signals name the same
drivers:

- the rollup's typed factors (blockers, tasks, drift, target dates), never
  their wording;
- each person's own status source (partial, inferred, stale, unknown);
- who was asked the day's check-in and who answered (counts and times only);
- the open risk and drift findings, for the headline and the signals.

A person is named, never shown by an id, and a person's own cell does not
name them. People and issue keys are named when there are three or fewer.
Silence is never green: a node with nothing reporting says why it has no
status. Pure: no I/O; ``PersonaViewService`` reads the day and calls in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from enum import StrEnum

from core.domain.graph import EdgeKind, EntityRef, GraphEdge, GraphNode, NodeKind
from core.domain.risk import DriftFinding, DriftFindingKind, RiskFinding, RiskRuleId
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from core.domain.status import CheckInDay, StatusSource

# What a reason may call a task or work item by: its tracker key.
_ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9]+-\d+")
# How many people or keys a reason names before it only counts them.
_NAMED = 3
# The headline stays readable at a glance; past this it drops the names.
HEADLINE_CHARS = 150
# How many signals Exec Today lists.
MAX_SIGNALS = 5
_TEAM_KINDS = frozenset({NodeKind.POD, NodeKind.PROJECT, NodeKind.PROGRAM})
_TILE_KINDS = frozenset({NodeKind.POD, NodeKind.PROJECT, NodeKind.WORKSTREAM})
_SEVERITY = {Rag.RED: 3, Rag.AMBER: 2, Rag.UNKNOWN: 1, Rag.GREEN: 0}


class PersonState(StrEnum):
    """What one person's update for the day was, as the reasons say it."""

    CONFIRMED = "confirmed"
    PARTIAL = "partial"
    #: Asked the day's check-in and not answered it.
    UNANSWERED = "unanswered"
    INFERRED = "inferred"
    STALE = "stale"
    MISSING = "missing"


class DriverKind(StrEnum):
    """What can make a node amber or red, in the order a reason leads with it."""

    BLOCKED_TASK = "blocked_task"
    BLOCKER = "blocker"
    DRIFT = "drift"
    UNANSWERED = "unanswered"
    PARTIAL = "partial"
    INFERRED = "inferred"
    STALE = "stale"
    MISSING = "missing"
    ATTENTION_TASK = "attention_task"
    TARGET_DATE = "target_date"


_DRIVER_ORDER = tuple(DriverKind)
_PEOPLE_DRIVERS: Mapping[PersonState, DriverKind] = {
    PersonState.UNANSWERED: DriverKind.UNANSWERED,
    PersonState.PARTIAL: DriverKind.PARTIAL,
    PersonState.INFERRED: DriverKind.INFERRED,
    PersonState.STALE: DriverKind.STALE,
    PersonState.MISSING: DriverKind.MISSING,
}
_PEOPLE_KINDS = frozenset(_PEOPLE_DRIVERS.values())


@dataclass(frozen=True, kw_only=True)
class TeamGraph:
    """Who and what sits under each node on the day, as the reasons count them.

    Read from the tenant's nodes and edges as they stand on the day, never by
    walking a program tree: ``contains`` says who is in a pod and which pods a
    project or program holds, ``assigned_to`` which pods serve a workstream.
    """

    labels: Mapping[str, str]
    kinds: Mapping[str, NodeKind]
    children: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    assigned_pods: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    pods_of: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_graph(
        cls, nodes: Iterable[GraphNode], edges: Iterable[GraphEdge], as_of: date
    ) -> TeamGraph:
        node_list = list(nodes)
        kinds = {node.id: node.kind for node in node_list}
        children: dict[str, set[str]] = {}
        assigned: dict[str, set[str]] = {}
        pods_of: dict[str, set[str]] = {}
        for edge in edges:
            if not edge.is_active_on(as_of):
                continue
            source, target = kinds.get(edge.from_node_id), kinds.get(edge.to_node_id)
            if edge.kind is EdgeKind.CONTAINS:
                children.setdefault(edge.from_node_id, set()).add(edge.to_node_id)
                if source is NodeKind.POD and target is NodeKind.DEVELOPER:
                    pods_of.setdefault(edge.to_node_id, set()).add(edge.from_node_id)
            elif (
                edge.kind is EdgeKind.ASSIGNED_TO
                and source is NodeKind.POD
                and target is NodeKind.WORKSTREAM
            ):
                assigned.setdefault(edge.to_node_id, set()).add(edge.from_node_id)
        return cls(
            labels={node.id: _label(node) for node in node_list},
            kinds=kinds,
            children={key: tuple(sorted(value)) for key, value in children.items()},
            assigned_pods={key: tuple(sorted(value)) for key, value in assigned.items()},
            pods_of={key: tuple(sorted(value)) for key, value in pods_of.items()},
        )

    def under(self, node_id: str, kind: NodeKind) -> frozenset[str]:
        """Every node of ``kind`` beneath ``node_id`` along ``contains``."""
        found: set[str] = set()
        seen = {node_id}
        queue = list(self.children.get(node_id, ()))
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            if self.kinds.get(current) is kind:
                found.add(current)
            queue.extend(self.children.get(current, ()))
        return frozenset(found)

    def holds(self, node_id: str, *kinds: NodeKind) -> tuple[str, ...]:
        """The nodes of ``kinds`` that ``node_id`` holds directly."""
        return tuple(
            child for child in self.children.get(node_id, ()) if self.kinds.get(child) in kinds
        )

    def label(self, node_id: str | None) -> str | None:
        """A node's name or issue key; None rather than its raw id."""
        if node_id is None:
            return None
        label = self.labels.get(node_id)
        if label is None and _ISSUE_KEY.fullmatch(node_id):
            return node_id
        return label


@dataclass(frozen=True, kw_only=True)
class CellReasons:
    """A cell's short reason, for under its colour, and every reason, for its tooltip."""

    reason: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class AttentionLink:
    """Where a signal leads: a delivery page (program, project, workstream, pod) or Signals."""

    kind: str
    id: str | None = None


@dataclass(frozen=True, kw_only=True)
class AttentionSignal:
    kind: str
    severity: Rag
    title: str
    age_days: int
    link: AttentionLink


@dataclass(frozen=True, kw_only=True)
class CheckinCount:
    """The day's check-in for the people in the teams: asked, answered, and when."""

    people: int
    asked: int
    answered: int
    first_asked_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class AttentionView:
    as_of: date
    program_id: str | None
    rag: Rag
    headline: str
    detail: str | None
    checkins: CheckinCount
    signals: tuple[AttentionSignal, ...]


@dataclass(frozen=True, kw_only=True)
class AttentionDay:
    """One day as the reasons read it."""

    as_of: date
    #: The reader's today: a reason says "today" only about that day.
    today: date
    statuses: Mapping[tuple[NodeKind, str], NodeStatus]
    graph: TeamGraph
    checkins: Mapping[str, CheckInDay] = field(default_factory=dict)
    #: Each open blocker's person, read off the people's own statuses.
    owners: Mapping[str, str] = field(default_factory=dict)
    tracker: str = "the issue tracker"
    vcs: str = "Git"

    @classmethod
    def build(
        cls,
        *,
        as_of: date,
        today: date,
        statuses: Iterable[NodeStatus],
        graph: TeamGraph,
        checkins: Iterable[CheckInDay] = (),
        tracker: str = "the issue tracker",
        vcs: str = "Git",
    ) -> AttentionDay:
        by_ref = {(status.entity_ref.kind, status.entity_ref.id): status for status in statuses}
        owners: dict[str, str] = {}
        for (kind, node_id), status in by_ref.items():
            if kind is NodeKind.DEVELOPER:
                for factor in status.factors:
                    if factor.kind is FactorKind.BLOCKER:
                        owners.setdefault(_blocker_key(factor), node_id)
        return cls(
            as_of=as_of,
            today=today,
            statuses=by_ref,
            graph=graph,
            checkins={checkin.developer_id: checkin for checkin in checkins},
            owners=owners,
            tracker=tracker,
            vcs=vcs,
        )

    @property
    def is_today(self) -> bool:
        return self.as_of >= self.today

    def person_source(self, person_id: str) -> StatusSource:
        status = self.statuses.get((NodeKind.DEVELOPER, person_id))
        return status.source if status is not None else StatusSource.UNKNOWN

    def person_state(self, person_id: str) -> PersonState:
        """A person's update for the day, by their own status source, never its wording.

        Asked the day's check-in and silent is "unanswered", whatever status
        that silence left behind: inferred, stale or unknown.
        """
        source = self.person_source(person_id)
        if source is StatusSource.CONFIRMED:
            return PersonState.CONFIRMED
        if source is StatusSource.PARTIAL:
            return PersonState.PARTIAL
        if self.awaiting_reply(person_id):
            return PersonState.UNANSWERED
        if source is StatusSource.INFERRED:
            return PersonState.INFERRED
        if source is StatusSource.STALE:
            return PersonState.STALE
        return PersonState.MISSING

    def awaiting_reply(self, person_id: str) -> bool:
        """Asked the day's check-in and not answered it (yet)."""
        checkin = self.checkins.get(person_id)
        return checkin is not None and not checkin.answered

    def name(self, person_id: str | None) -> str | None:
        return self.graph.label(person_id)


# ---- drivers -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class _Driver:
    """One thing behind a colour: a blocker, a person's update, a drift signal."""

    kind: DriverKind
    severity: Rag
    #: Identity across the cells it reaches (a blocker id, a person, an issue).
    key: str
    person_id: str | None = None
    #: The issue key or name it is on.
    item: str | None = None
    #: The factor's own sentence, for the tooltip.
    text: str | None = None
    #: The pods it belongs to, for a signal's link.
    pod_ids: tuple[str, ...] = ()


def _drivers(day: AttentionDay, status: NodeStatus) -> list[_Driver]:
    """Every driver a node's factors and its people's updates carry, worst first."""
    cell = status.entity_ref
    found: dict[tuple[DriverKind, str], _Driver] = {}
    for factor in status.factors:
        driver = _factor_driver(day, factor, cell)
        if driver is not None:
            found.setdefault((driver.kind, driver.key), driver)
    for person_id in _people_of(day, cell):
        kind = _PEOPLE_DRIVERS.get(day.person_state(person_id))
        if kind is None:
            continue
        unknown = day.person_source(person_id) is StatusSource.UNKNOWN
        found.setdefault(
            (kind, person_id),
            _Driver(
                kind=kind,
                severity=Rag.UNKNOWN if unknown else Rag.AMBER,
                key=person_id,
                person_id=person_id,
                pod_ids=day.graph.pods_of.get(person_id, ()),
            ),
        )
    return _ranked(found.values())


def _factor_driver(day: AttentionDay, factor: RollupFactor, cell: EntityRef) -> _Driver | None:
    """The driver one rollup factor stands for, by its typed kind; None for a green line."""
    if factor.contributes is Rag.GREEN:
        return None
    item = day.graph.label(factor.work_item_ref.id) if factor.work_item_ref else None
    if factor.kind is FactorKind.BLOCKER:
        key = _blocker_key(factor)
        owner = day.owners.get(key)
        if owner is None and factor.source_ref.kind is NodeKind.DEVELOPER:
            owner = factor.source_ref.id
        if owner is None and cell.kind is NodeKind.DEVELOPER:
            owner = cell.id
        return _Driver(
            kind=DriverKind.BLOCKER,
            severity=factor.contributes,
            key=key,
            person_id=owner,
            item=item,
            pod_ids=factor.applies_to_pod_ids,
        )
    if factor.kind is FactorKind.TASK:
        red = factor.contributes is Rag.RED
        return _Driver(
            kind=DriverKind.BLOCKED_TASK if red else DriverKind.ATTENTION_TASK,
            severity=factor.contributes,
            key=factor.source_ref.id,
            item=day.graph.label(factor.source_ref.id) or "a task",
        )
    if factor.kind is FactorKind.DRIFT:
        issue = factor.work_item_ref.id if factor.work_item_ref else factor.description
        return _Driver(
            kind=DriverKind.DRIFT,
            severity=Rag.AMBER,
            key=f"{factor.source_ref.id}:{issue}",
            person_id=factor.source_ref.id,
            item=item,
            text=factor.description,
            pod_ids=factor.applies_to_pod_ids,
        )
    if factor.kind is FactorKind.TARGET_DATE:
        return _Driver(
            kind=DriverKind.TARGET_DATE,
            severity=factor.contributes,
            key=factor.description,
            text=factor.description,
        )
    # A person's own status factor is read from their status source instead.
    return None


def _ranked(drivers: Iterable[_Driver]) -> list[_Driver]:
    return sorted(
        drivers,
        key=lambda driver: (-_SEVERITY[driver.severity], _DRIVER_ORDER.index(driver.kind)),
    )


def _people_of(day: AttentionDay, cell: EntityRef) -> tuple[str, ...]:
    """The people whose updates count for a node: itself, or everyone in it."""
    if cell.kind is NodeKind.DEVELOPER:
        return (cell.id,)
    if cell.kind in _TEAM_KINDS:
        return tuple(sorted(day.graph.under(cell.id, NodeKind.DEVELOPER)))
    return ()


def _grouped(drivers: Iterable[_Driver]) -> dict[DriverKind, list[_Driver]]:
    grouped: dict[DriverKind, list[_Driver]] = {}
    for driver in drivers:
        grouped.setdefault(driver.kind, []).append(driver)
    return grouped


# ---- cell reasons ------------------------------------------------------------


def cell_reasons(
    day: AttentionDay, status: NodeStatus, *, outside_teams: bool = False
) -> CellReasons:
    """The short reason under a heat-map cell's colour, and every reason, for its tooltip."""
    kind = status.entity_ref.kind
    if kind is NodeKind.DEVELOPER:
        return _person_reasons(day, status, outside_teams=outside_teams)
    if kind is NodeKind.WORKSTREAM:
        return _workstream_reasons(day, status)
    if kind in _TEAM_KINDS:
        return _team_reasons(day, status)
    lines = tuple(factor.description for factor in status.factors)
    if status.rag is Rag.UNKNOWN:
        return CellReasons(reason="No status of its own", reasons=lines)
    return CellReasons(reason=lines[0].rstrip(".") if lines else status.rag.value, reasons=lines)


def _team_reasons(day: AttentionDay, status: NodeStatus) -> CellReasons:
    cell = status.entity_ref
    people = _people_of(day, cell)
    if status.rag is Rag.UNKNOWN:
        if not people:
            noun = cell.kind.value
            reason = "No one in this pod" if cell.kind is NodeKind.POD else "No one mapped to it"
            return CellReasons(
                reason=reason,
                reasons=(f"Nobody is in this {noun}, so nothing reports a status for it.",),
            )
        return CellReasons(
            reason="No status reported yet",
            reasons=(
                f"None of its {_count(len(people), ('person', 'people'))} has a status for "
                f"{_day_words(day)}.",
            ),
        )
    if status.rag is Rag.GREEN:
        waiting = [person for person in people if day.awaiting_reply(person)]
        if waiting and day.is_today:
            return CellReasons(
                reason=f"{len(waiting)} of {len(people)} not answered yet",
                reasons=(
                    f"Green on the last updates; {len(waiting)} of {len(people)} haven't "
                    f"answered {_checkin_words(day)} yet.",
                    *_dependency_lines(status),
                ),
            )
        reason = f"All {len(people)} confirmed" if len(people) > 1 else "Confirmed, no blockers"
        return CellReasons(
            reason=reason,
            reasons=(
                f"{_capital(_everyone(len(people)))} confirmed with no open blockers.",
                *_dependency_lines(status),
            ),
        )
    drivers = _drivers(day, status)
    if not drivers:
        return _unrecorded(status)
    return CellReasons(
        reason=_short(day, drivers, cell, people),
        reasons=_lines(day, drivers, cell, people) + _dependency_lines(status),
    )


def _workstream_reasons(day: AttentionDay, status: NodeStatus) -> CellReasons:
    cell = status.entity_ref
    if status.rag is Rag.UNKNOWN:
        tasks = day.graph.holds(cell.id, NodeKind.TASK, NodeKind.WORK_ITEM)
        if tasks:
            return CellReasons(
                reason="No task reports health",
                reasons=(
                    f"Its {_count(len(tasks), ('task is', 'tasks are'))} to do, in progress or "
                    "done; none is blocked, at risk or carries a status.",
                ),
            )
        pods = [
            name
            for pod in day.graph.assigned_pods.get(cell.id, ())
            if (name := day.graph.label(pod))
        ]
        whose = "its" if len(pods) == 1 else "their"
        serving = (
            f"Assigned to {_join(pods)}, but none of {whose} tickets is linked to it."
            if pods
            else "No pod is assigned to it either."
        )
        return CellReasons(
            reason="No tasks linked",
            reasons=(
                "No tasks are linked to this workstream, so nothing reports a status for it.",
                serving,
            ),
        )
    if status.rag is Rag.GREEN:
        return CellReasons(
            reason="Tasks on track",
            reasons=("Its tasks show progress with no blockers.", *_dependency_lines(status)),
        )
    drivers = _drivers(day, status)
    if not drivers:
        return _unrecorded(status)
    return CellReasons(
        reason=_short(day, drivers, cell, ()), reasons=_lines(day, drivers, cell, ())
    )


def _person_reasons(day: AttentionDay, status: NodeStatus, *, outside_teams: bool) -> CellReasons:
    cell = status.entity_ref
    state = day.person_state(cell.id)
    own = _person_state_line(day, state)
    note = ("In no pod, so no team colour counts this check-in.",) if outside_teams else ()
    drivers = [driver for driver in _drivers(day, status) if driver.kind not in _PEOPLE_KINDS]
    if drivers:
        lines = _lines(day, drivers, cell, ())
        return CellReasons(reason=_short(day, drivers, cell, ()), reasons=(*lines, own, *note))
    if state is PersonState.CONFIRMED and day.awaiting_reply(cell.id) and day.is_today:
        reason = "Not answered today yet"
    else:
        reason = {
            PersonState.CONFIRMED: "Confirmed, no blockers",
            PersonState.PARTIAL: "Partial: blockers or ETA open",
            PersonState.UNANSWERED: (
                "Check-in unanswered today" if day.is_today else "Check-in unanswered"
            ),
            PersonState.INFERRED: "Inferred, not confirmed",
            PersonState.STALE: "Last update carried over",
            PersonState.MISSING: "No status reported yet",
        }[state]
    return CellReasons(reason=reason, reasons=(own, *note))


def _person_state_line(day: AttentionDay, state: PersonState) -> str:
    return {
        PersonState.CONFIRMED: "Confirmed the check-in with no open blockers.",
        PersonState.PARTIAL: "Answered in part: blockers or the ETA are not confirmed.",
        PersonState.UNANSWERED: (
            f"Hasn't answered {_checkin_words(day)}, so the status is {_after_silence(day)}."
        ),
        PersonState.INFERRED: (
            f"No confirmed reply; the status is inferred from {day.tracker} and {day.vcs}."
        ),
        PersonState.STALE: "No reply; the last update is carried over from an earlier day.",
        PersonState.MISSING: "No status is recorded yet.",
    }[state]


def _after_silence(day: AttentionDay) -> str:
    return f"inferred from {day.tracker} and {day.vcs} until there is a reply"


def _unrecorded(status: NodeStatus) -> CellReasons:
    return CellReasons(
        reason="Reasons not recorded",
        reasons=tuple(
            factor.description for factor in status.factors if factor.contributes is not Rag.GREEN
        ),
    )


def _dependency_lines(status: NodeStatus) -> tuple[str, ...]:
    """The green lines still worth saying: who waits on this node's work."""
    return tuple(
        factor.description for factor in status.factors if factor.kind is FactorKind.DEPENDENCY
    )


def _short(
    day: AttentionDay, drivers: Sequence[_Driver], cell: EntityRef, people: Sequence[str]
) -> str:
    """The reason a tile carries under its colour: its worst driver, in a few words."""
    lead = drivers[0]
    same = [driver for driver in drivers if driver.kind is lead.kind]
    own = cell.id if cell.kind is NodeKind.DEVELOPER else None
    if lead.kind is DriverKind.BLOCKED_TASK:
        return f"{lead.item} blocked" if len(same) == 1 else f"{len(same)} tasks blocked"
    if lead.kind is DriverKind.BLOCKER:
        if len(same) == 1:
            owner = None if lead.person_id == own else _short_name(day, lead.person_id)
            on = f"Blocker on {lead.item}" if lead.item else "Open blocker"
            return f"{on} ({owner})" if owner else on
        owners = _distinct(_short_name(day, driver.person_id) for driver in same)
        if 0 < len(owners) <= 2:
            return f"{len(same)} blockers ({', '.join(owners)})"
        return f"{len(same)} open blockers"
    if lead.kind is DriverKind.DRIFT:
        items = _distinct(driver.item for driver in same)
        if len(items) == 1:
            return f"Signals disagree on {items[0]}"
        return f"Signals disagree on {_count(len(items) or len(same), ('issue', 'issues'))}"
    if lead.kind in _PEOPLE_KINDS:
        return _people_short(day, lead.kind, same, people)
    if lead.kind is DriverKind.ATTENTION_TASK:
        if len(same) == 1:
            return f"{lead.item} needs attention"
        return f"{len(same)} tasks need attention"
    return "Target date near" if len(same) == 1 else f"{len(same)} target dates near"


def _people_short(
    day: AttentionDay, kind: DriverKind, same: Sequence[_Driver], people: Sequence[str]
) -> str:
    n, total = len(same), len(people)
    of = f"{n} of {total}" if total else str(n)
    if kind is DriverKind.UNANSWERED:
        return f"{of} unanswered{' today' if day.is_today else ''}"
    if kind is DriverKind.PARTIAL:
        name = _short_name(day, same[0].person_id) if n == 1 else None
        return f"Partial update ({name})" if name else f"{of} updates partial"
    if kind is DriverKind.INFERRED:
        return f"{of} inferred, not confirmed"
    if kind is DriverKind.STALE:
        return f"{of} updates carried over"
    return f"{of} with no status"


def _lines(
    day: AttentionDay, drivers: Sequence[_Driver], cell: EntityRef, people: Sequence[str]
) -> tuple[str, ...]:
    """Every driver as one sentence, worst first, for the tooltip."""
    own = cell.id if cell.kind is NodeKind.DEVELOPER else None
    lines: list[str] = []
    for kind, same in _grouped(drivers).items():
        if kind is DriverKind.BLOCKER:
            for driver in same:
                owner = None if driver.person_id == own else day.name(driver.person_id)
                where = f" on {driver.item}" if driver.item else ""
                who = f" ({owner})" if owner else ""
                critical = " It is critical." if driver.severity is Rag.RED else ""
                lines.append(f"Open blocker{where}{who}.{critical}")
        elif kind is DriverKind.BLOCKED_TASK:
            lines.append(f"Blocked: {_join_items(_distinct(d.item for d in same))}.")
        elif kind is DriverKind.ATTENTION_TASK:
            lines.append(f"Needs attention: {_join_items(_distinct(d.item for d in same))}.")
        elif kind is DriverKind.DRIFT:
            for driver in same:
                owner = None if driver.person_id == own else day.name(driver.person_id)
                text = (driver.text or "Signals disagree.").strip().rstrip(".")
                lines.append(f"{text} ({owner})." if owner else f"{text}.")
        elif kind is DriverKind.TARGET_DATE:
            lines.extend(driver.text or "A target date is near." for driver in same)
        else:
            lines.append(_people_line(day, kind, same, people, own))
    return tuple(lines)


def _people_line(
    day: AttentionDay,
    kind: DriverKind,
    same: Sequence[_Driver],
    people: Sequence[str],
    own: str | None,
) -> str:
    names = [
        name for driver in same if driver.person_id != own and (name := day.name(driver.person_id))
    ]
    n, total = len(same), len(people)
    # A tooltip has room for everyone; only the headline stops at three.
    who = f": {_join(names)}" if names else ""
    of = f"{n} of {total}" if total else str(n)
    if kind is DriverKind.UNANSWERED:
        verb = "haven't answered" if day.is_today else "didn't answer"
        return (
            f"{of} {verb} {_checkin_words(day)}{who}. Their statuses are inferred from "
            f"{day.tracker} and {day.vcs} until they reply."
        )
    if kind is DriverKind.PARTIAL:
        verb = "is" if n == 1 else "are"
        listed = f" ({_join(names)})" if names else ""
        return f"{of} updates {verb} partial{listed}: blockers or the ETA not confirmed."
    if kind is DriverKind.INFERRED:
        return f"{of} statuses are inferred from {day.tracker} and {day.vcs}, not confirmed{who}."
    if kind is DriverKind.STALE:
        return f"{of} updates are carried over from an earlier day{who}."
    return f"{of} have no status yet{who}."


# ---- headline and signals ----------------------------------------------------


def attention_view(
    day: AttentionDay,
    program_id: str | None,
    *,
    risks: Sequence[RiskFinding] = (),
    drift: Sequence[DriftFinding] = (),
    blocker_ages: Mapping[str, int] | None = None,
    zone: tzinfo = UTC,
    clock: bool = True,
) -> AttentionView:
    """The headline, its second line and the top signals for a program on one day.

    ``clock`` False leaves the times out ("0 of 10" without "asked 13:16"),
    for a stored brief: the page shows the time on its reader's clock.

    The colour is the worst of the program's pods, projects and workstreams,
    as the tiles show it. The headline's cause is the driver behind the most
    amber or red tiles (red drivers first under a red headline); the second
    line names the next ones, then the open risks and drift the tiles do not
    carry. People outside the teams count in none of it.
    """
    tiles = _tiles(day, program_id)
    rag = _worst(tile.rag for tile in tiles)
    people = _team_people(day, program_id)
    count = _checkin_count(day, people)
    if not clock:
        count = CheckinCount(
            people=count.people, asked=count.asked, answered=count.answered, first_asked_at=None
        )
    program = day.statuses.get((NodeKind.PROGRAM, program_id)) if program_id else None
    drivers = _program_drivers(day, program, tiles)
    headline, detail = _headline(day, rag, drivers, tiles, count, people, risks, drift, zone)
    signals = _signals(
        day, program_id, drivers, count, people, risks, drift, blocker_ages or {}, zone
    )
    return AttentionView(
        as_of=day.as_of,
        program_id=program_id,
        rag=rag,
        headline=headline,
        detail=detail,
        checkins=count,
        signals=signals,
    )


def _tiles(day: AttentionDay, program_id: str | None) -> list[NodeStatus]:
    """The team tiles: the program's pods, projects and workstreams."""
    within: frozenset[str] | None = None
    if program_id is not None:
        within = frozenset(
            node for kind in _TILE_KINDS for node in day.graph.under(program_id, kind)
        )
    return [
        status
        for (kind, node_id), status in day.statuses.items()
        if kind in _TILE_KINDS and (within is None or node_id in within)
    ]


def _team_people(day: AttentionDay, program_id: str | None) -> tuple[str, ...]:
    if program_id is not None:
        return tuple(sorted(day.graph.under(program_id, NodeKind.DEVELOPER)))
    return tuple(sorted(day.graph.pods_of))


def _checkin_count(day: AttentionDay, people: Sequence[str]) -> CheckinCount:
    asked = [day.checkins[person] for person in people if person in day.checkins]
    return CheckinCount(
        people=len(people),
        asked=len(asked),
        answered=sum(1 for checkin in asked if checkin.answered),
        first_asked_at=min((checkin.first_asked_at for checkin in asked), default=None),
    )


def _program_drivers(
    day: AttentionDay, program: NodeStatus | None, tiles: Sequence[NodeStatus]
) -> list[_Driver]:
    """Every driver that reaches the program or its amber and red tiles, each once."""
    found: dict[tuple[DriverKind, str], _Driver] = {}
    sources = [program] if program is not None and program.rag in (Rag.AMBER, Rag.RED) else []
    sources.extend(tile for tile in tiles if tile.rag in (Rag.AMBER, Rag.RED))
    for status in sources:
        for driver in _drivers(day, status):
            found.setdefault((driver.kind, driver.key), driver)
    return _ranked(found.values())


def _headline(
    day: AttentionDay,
    rag: Rag,
    drivers: Sequence[_Driver],
    tiles: Sequence[NodeStatus],
    count: CheckinCount,
    people: Sequence[str],
    risks: Sequence[RiskFinding],
    drift: Sequence[DriftFinding],
    zone: tzinfo,
) -> tuple[str, str | None]:
    if rag is Rag.UNKNOWN:
        if not tiles:
            return "No status yet: nothing in the program is set up to report.", None
        return f"No status yet: nothing in the program has reported for {_day_words(day)}.", None
    findings = _finding_clauses(day, risks, drift, drivers)
    if rag is Rag.GREEN:
        waiting = count.asked - count.answered
        if waiting and day.is_today:
            sentence = (
                f"Green on the last updates: {waiting} of {count.asked} haven't answered "
                f"{_checkin_words(day)} yet{_asked_suffix(count, zone)}."
            )
        else:
            sentence = f"Green: {_everyone(len(people))} confirmed with no open blockers."
        return sentence, _also(findings)
    groups = _ranked_groups(day, drivers, tiles, rag)
    if not groups:
        return f"{_capital(rag.value)}: what drives this is not recorded yet.", _also(findings)
    clauses = [_clause(day, kind, same, count, people, zone) for kind, same in groups]
    sentence = f"{_capital(rag.value)}: {clauses[0]}."
    if len(sentence) > HEADLINE_CHARS:
        kind, same = groups[0]
        sentence = (
            f"{_capital(rag.value)}: {_clause(day, kind, same, count, people, zone, names=False)}."
        )
    rest = [_clause(day, kind, same, count, people, zone, names=False) for kind, same in groups[1:]]
    return sentence, _also([*rest, *findings])


def _also(clauses: Sequence[str]) -> str | None:
    return f"Also: {'; '.join(clauses[:3])}." if clauses else None


def _ranked_groups(
    day: AttentionDay, drivers: Sequence[_Driver], tiles: Sequence[NodeStatus], rag: Rag
) -> list[tuple[DriverKind, list[_Driver]]]:
    """Driver kinds, the one behind the most amber or red tiles first.

    Red drivers lead a red headline. Among the rest the kind most tiles share
    is the cause; a tie goes to the order a reason leads with.
    """
    grouped = _grouped(drivers)
    reach: dict[DriverKind, int] = {}
    for tile in tiles:
        if tile.rag in (Rag.AMBER, Rag.RED):
            for kind in {driver.kind for driver in _drivers(day, tile)}:
                reach[kind] = reach.get(kind, 0) + 1

    def rank(kind: DriverKind) -> tuple[int, int, int]:
        red = rag is Rag.RED and any(driver.severity is Rag.RED for driver in grouped[kind])
        return (-int(red), -reach.get(kind, 0), _DRIVER_ORDER.index(kind))

    return [(kind, grouped[kind]) for kind in sorted(grouped, key=rank)]


@dataclass(frozen=True, kw_only=True)
class _Phrase:
    """What a clause about one driver kind may say."""

    day: AttentionDay
    same: Sequence[_Driver]
    count: CheckinCount
    people: Sequence[str]
    zone: tzinfo
    #: Whether people and keys may be named (three or fewer, and room for them).
    names: bool

    @property
    def who(self) -> list[str]:
        return [name for driver in self.same if (name := self.day.name(driver.person_id))]

    @property
    def named(self) -> bool:
        return self.names and len(self.same) <= _NAMED and bool(self.who)

    @property
    def items(self) -> list[str]:
        return _distinct(driver.item for driver in self.same)

    def listed(self, values: Sequence[str]) -> str:
        return f" ({_join(values)})" if self.names and values and len(values) <= _NAMED else ""


def _clause(
    day: AttentionDay,
    kind: DriverKind,
    same: Sequence[_Driver],
    count: CheckinCount,
    people: Sequence[str],
    zone: tzinfo,
    *,
    names: bool = True,
) -> str:
    """One driver kind across the program, as a clause the headline can carry."""
    phrase = _Phrase(day=day, same=same, count=count, people=people, zone=zone, names=names)
    return _CLAUSES[kind](phrase)


def _unanswered(phrase: _Phrase) -> str:
    who = phrase.who if phrase.named else []
    return _unanswered_clause(phrase.day, phrase.same, phrase.count, who, phrase.zone)


def _partial(phrase: _Phrase) -> str:
    n, total = len(phrase.same), len(phrase.people)
    if n == 1 and phrase.named:
        return (
            f"1 of {total} updates is partial: {phrase.who[0]} hasn't confirmed blockers or an ETA"
        )
    listed = f" ({_join(phrase.who)})" if phrase.named else ""
    verb = "is" if n == 1 else "are"
    return f"{n} of {total} updates {verb} partial{listed}, with blockers or ETAs open"


def _blockers(phrase: _Phrase) -> str:
    parts = [
        (phrase.day.name(driver.person_id) or "a team member")
        + (f" on {driver.item}" if driver.item else "")
        for driver in phrase.same
    ]
    if len(phrase.same) == 1:
        lone = phrase.same[0]
        person = phrase.day.name(lone.person_id) or "a team member"
        return (
            f"{person} is blocked on {lone.item}" if lone.item else f"{person} has an open blocker"
        )
    if phrase.names and len(phrase.same) <= _NAMED:
        return f"{len(phrase.same)} open blockers: {_join(parts)}"
    return f"{len(phrase.same)} open blockers, including {parts[0]}"


def _blocked_tasks(phrase: _Phrase) -> str:
    items = phrase.items
    if len(items) == 1:
        return f"{items[0]} is blocked"
    return f"{len(items)} tasks are blocked{phrase.listed(items)}"


def _attention_tasks(phrase: _Phrase) -> str:
    items = phrase.items
    if len(items) == 1:
        return f"{items[0]} needs attention"
    return f"{len(items)} tasks need attention{phrase.listed(items)}"


def _drift(phrase: _Phrase) -> str:
    if len(phrase.same) == 1:
        lone = phrase.same[0]
        person = phrase.day.name(lone.person_id)
        return _drift_words(lone) + (f" ({person})" if person and phrase.names else "")
    items = phrase.items
    issues = _count(len(items) or len(phrase.same), ("issue", "issues"))
    return f"signals disagree on {issues}{phrase.listed(items)}"


def _inferred(phrase: _Phrase) -> str:
    listed = f" ({_join(phrase.who)})" if phrase.named else ""
    statuses = _count(len(phrase.same), ("status is", "statuses are"))
    day = phrase.day
    return f"{statuses} inferred from {day.tracker} and {day.vcs}, not confirmed{listed}"


def _stale(phrase: _Phrase) -> str:
    listed = f" ({_join(phrase.who)})" if phrase.named else ""
    updates = _count(len(phrase.same), ("update is", "updates are"))
    return f"{updates} carried over from an earlier day{listed}"


def _missing(phrase: _Phrase) -> str:
    listed = f" ({_join(phrase.who)})" if phrase.named else ""
    return f"{_count(len(phrase.same), ('person has', 'people have'))} no status yet{listed}"


def _target_date(phrase: _Phrase) -> str:
    text = phrase.same[0].text or "a target date is near"
    return _lower_first(text.strip().rstrip("."))


_CLAUSES = {
    DriverKind.UNANSWERED: _unanswered,
    DriverKind.PARTIAL: _partial,
    DriverKind.BLOCKER: _blockers,
    DriverKind.BLOCKED_TASK: _blocked_tasks,
    DriverKind.ATTENTION_TASK: _attention_tasks,
    DriverKind.DRIFT: _drift,
    DriverKind.INFERRED: _inferred,
    DriverKind.STALE: _stale,
    DriverKind.MISSING: _missing,
    DriverKind.TARGET_DATE: _target_date,
}


_PERSON_SIGNALS = {
    DriverKind.PARTIAL: "{name}'s update is partial: blockers or the ETA not confirmed",
    DriverKind.INFERRED: "{name}'s status is inferred from {tracker}, not confirmed",
    DriverKind.STALE: "{name}'s last update is carried over from an earlier day",
    DriverKind.MISSING: "{name} has no status yet",
}


def _signal_title(
    day: AttentionDay,
    kind: DriverKind,
    same: Sequence[_Driver],
    count: CheckinCount,
    people: Sequence[str],
    zone: tzinfo,
) -> str:
    """A signal's one line: a person by name when it is one person's, else the clause."""
    if kind is DriverKind.UNANSWERED:
        return _unanswered_lead(day, same, count, [], zone)
    template = _PERSON_SIGNALS.get(kind)
    name = day.name(same[0].person_id) if len(same) == 1 else None
    if template is not None and name:
        return template.format(name=name, tracker=day.tracker)
    return _clause(day, kind, same, count, people, zone)


def _unanswered_lead(
    day: AttentionDay,
    same: Sequence[_Driver],
    count: CheckinCount,
    who: Sequence[str],
    zone: tzinfo,
) -> str:
    """How many have not answered the day's check-in, of how many asked, since when."""
    n = len(same)
    asked = max(count.asked, n)
    at = _asked_time(count, zone)
    if n == asked:
        verb = "has answered" if day.is_today else "answered"
        yet = " yet" if day.is_today else ""
        detail = f"0 of {asked}, asked {at}" if at else f"0 of {asked}"
        return f"no one {verb} {_checkin_words(day)}{yet} ({detail})"
    verb = "haven't answered" if day.is_today else "didn't answer"
    listed = f": {_join(who)}" if who else ""
    since = f" (asked {at})" if at else ""
    return f"{n} of {asked} people {verb} {_checkin_words(day)}{since}{listed}"


def _unanswered_clause(
    day: AttentionDay,
    same: Sequence[_Driver],
    count: CheckinCount,
    who: Sequence[str],
    zone: tzinfo,
) -> str:
    n = len(same)
    asked = max(count.asked, n)
    lead = _unanswered_lead(day, same, count, who, zone)
    inferred = sum(
        1 for driver in same if day.person_source(driver.person_id or "") is StatusSource.INFERRED
    )
    if not inferred:
        return lead
    if inferred == n == asked:
        statuses = "today's statuses are" if day.is_today else "that day's statuses were"
        return f"{lead}, so {statuses} inferred from {day.tracker} and {day.vcs}"
    inferred_part = _count(inferred, ("status is", "statuses are"))
    return f"{lead}; {inferred_part} inferred from {day.tracker} and {day.vcs}"


def _finding_clauses(
    day: AttentionDay,
    risks: Sequence[RiskFinding],
    drift: Sequence[DriftFinding],
    drivers: Sequence[_Driver],
) -> list[str]:
    """Open risks and drift the tiles do not already carry, for the second line."""
    carried = {driver.item for driver in drivers if driver.kind is DriverKind.DRIFT and driver.item}
    clauses: list[str] = []
    for drift_finding in drift:
        key = day.graph.label(drift_finding.entity_ref.id)
        if key is None or key not in carried:
            clauses.append(_drift_finding_words(drift_finding, key, day))
    for risk in risks:
        key = day.graph.label(risk.entity_ref.id)
        if key is None or key not in carried:
            clauses.append(_risk_words(risk, key))
    return clauses


def _signals(
    day: AttentionDay,
    program_id: str | None,
    drivers: Sequence[_Driver],
    count: CheckinCount,
    people: Sequence[str],
    risks: Sequence[RiskFinding],
    drift: Sequence[DriftFinding],
    blocker_ages: Mapping[str, int],
    zone: tzinfo,
) -> tuple[AttentionSignal, ...]:
    """Up to five things a director should act on: worst first, then oldest.

    Open blockers, blocked tasks, the day's unanswered check-ins and the
    partial, inferred, stale or missing updates behind amber tiles, the open
    drift and risk findings, and tasks or target dates needing attention.
    """
    program = (
        AttentionLink(kind="program", id=program_id)
        if program_id
        else AttentionLink(kind="signals")
    )
    grouped = _grouped(drivers)
    found: list[AttentionSignal] = []
    for driver in grouped.get(DriverKind.BLOCKER, []):
        owner = day.name(driver.person_id) or "A team member"
        on = f" on {driver.item}" if driver.item else ""
        age = blocker_ages.get(driver.key, 0)
        since = f" for {_count(age, ('day', 'days'))}" if age else ""
        found.append(
            AttentionSignal(
                kind="blocker",
                severity=driver.severity,
                title=f"{owner} is blocked{on}{since}",
                age_days=age,
                link=_pod_link(driver.pod_ids) or program,
            )
        )
    for kind in (
        DriverKind.BLOCKED_TASK,
        DriverKind.UNANSWERED,
        DriverKind.PARTIAL,
        DriverKind.INFERRED,
        DriverKind.STALE,
        DriverKind.MISSING,
        DriverKind.ATTENTION_TASK,
        DriverKind.TARGET_DATE,
    ):
        same = grouped.get(kind, [])
        if not same:
            continue
        link = program
        if kind in _PEOPLE_KINDS and len(same) == 1:
            link = _pod_link(same[0].pod_ids) or program
        found.append(
            AttentionSignal(
                kind=kind.value,
                severity=_worst(driver.severity for driver in same),
                title=_capital(_signal_title(day, kind, same, count, people, zone)),
                age_days=0,
                link=link,
            )
        )
    found.extend(_finding_signals(day, grouped.get(DriverKind.DRIFT, []), risks, drift))
    found.sort(key=lambda signal: (-_SEVERITY[signal.severity], -signal.age_days))
    return tuple(found[:MAX_SIGNALS])


def _finding_signals(
    day: AttentionDay,
    drift_drivers: Sequence[_Driver],
    risks: Sequence[RiskFinding],
    drift: Sequence[DriftFinding],
) -> list[AttentionSignal]:
    """The open drift and risk findings, and drift a cell carries that no finding names."""
    signals_page = AttentionLink(kind="signals")
    found: list[AttentionSignal] = []
    signalled: set[str] = set()
    for drift_finding in drift:
        key = day.graph.label(drift_finding.entity_ref.id)
        if key:
            signalled.add(key)
        found.append(
            AttentionSignal(
                kind=f"drift:{drift_finding.kind.value}",
                severity=drift_finding.severity,
                title=_capital(_drift_finding_words(drift_finding, key, day)),
                age_days=max((day.as_of - drift_finding.detected_at.date()).days, 0),
                link=signals_page,
            )
        )
    for risk in risks:
        key = day.graph.label(risk.entity_ref.id)
        if key:
            signalled.add(key)
        found.append(
            AttentionSignal(
                kind=f"risk:{risk.rule_id.value}",
                severity=risk.severity,
                title=_capital(_risk_words(risk, key)),
                age_days=risk.age_days,
                link=signals_page,
            )
        )
    for driver in drift_drivers:
        if driver.item and driver.item in signalled:
            continue
        person = day.name(driver.person_id)
        found.append(
            AttentionSignal(
                kind="drift",
                severity=Rag.AMBER,
                title=_capital(_drift_words(driver)) + (f" ({person})" if person else ""),
                age_days=0,
                link=signals_page,
            )
        )
    return found


# ---- wording -----------------------------------------------------------------


def _drift_words(driver: _Driver) -> str:
    """A drift factor's own reason as a clause, from its typed kind and issue."""
    text = (driver.text or "").strip().rstrip(".")
    lead = "Signals disagree:"
    if text.startswith(lead):
        return f"signals disagree: {text[len(lead) :].strip()}"
    if driver.item:
        return f"signals disagree on {driver.item}"
    return "signals disagree"


def _drift_finding_words(finding: DriftFinding, key: str | None, day: AttentionDay) -> str:
    issue = key or "an issue"
    words = {
        DriftFindingKind.MERGED_ISSUE_OPEN: f"{issue} is merged but still open in {day.tracker}",
        DriftFindingKind.SAID_IN_REVIEW_NO_MR: (
            f"{issue} is said to be in review with no open merge request"
        ),
        DriftFindingKind.ETA_DISAGREEMENT: f"the ETAs given for {issue} do not overlap",
        DriftFindingKind.SAID_DONE_NO_PR: f"{issue} is marked done with no merge request",
        DriftFindingKind.CLAIMED_PROGRESS_NO_ACTIVITY: (
            f"{issue} is reported in progress with no {day.vcs} activity"
        ),
    }.get(finding.kind)
    return words or _lower_first(finding.reason.strip().rstrip("."))


def _risk_words(finding: RiskFinding, key: str | None) -> str:
    if finding.rule_id is RiskRuleId.PR_AGE:
        on = f" for {key}" if key else ""
        return f"a merge request{on} has been open {_count(finding.age_days, ('day', 'days'))}"
    return _lower_first(finding.reason.strip().rstrip("."))


def _checkin_words(day: AttentionDay) -> str:
    if day.is_today:
        return "today's check-in"
    return f"the {day.as_of.day} {day.as_of:%b} check-in"


def _day_words(day: AttentionDay) -> str:
    return "today" if day.is_today else f"{day.as_of:%a} {day.as_of.day} {day.as_of:%b}"


def _asked_time(count: CheckinCount, zone: tzinfo) -> str | None:
    """When the day's check-in was first asked, on the reader's clock: "13:16 IST"."""
    at = count.first_asked_at
    if at is None:
        return None
    local = (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(zone)
    return f"{local:%H:%M} {local.tzname() or 'UTC'}"


def _asked_suffix(count: CheckinCount, zone: tzinfo) -> str:
    at = _asked_time(count, zone)
    return f" (asked {at})" if at else ""


def _pod_link(pod_ids: Sequence[str]) -> AttentionLink | None:
    return AttentionLink(kind="pod", id=sorted(pod_ids)[0]) if pod_ids else None


def _short_name(day: AttentionDay, person_id: str | None) -> str | None:
    """A person's first name, unless someone else in the tenant shares it."""
    name = day.name(person_id)
    if not name:
        return None
    first = name.split()[0]
    shared = sum(
        1
        for node_id, label in day.graph.labels.items()
        if day.graph.kinds.get(node_id) is NodeKind.DEVELOPER and label.split()[:1] == [first]
    )
    return first if shared <= 1 else name


def _worst(rags: Iterable[Rag]) -> Rag:
    values = set(rags)
    for rag in (Rag.RED, Rag.AMBER, Rag.GREEN):
        if rag in values:
            return rag
    return Rag.UNKNOWN


def _blocker_key(factor: RollupFactor) -> str:
    """A blocker's identity across the cells it reaches, as the rollup counts it."""
    return factor.blocker_id or factor.description


def _label(node: GraphNode) -> str:
    """A person by name; a task or work item by its issue key where it has one."""
    if node.kind in (NodeKind.TASK, NodeKind.WORK_ITEM):
        key = node.metadata.get("key")
        if isinstance(key, str) and key.strip():
            return key.strip()
        if _ISSUE_KEY.fullmatch(node.id):
            return node.id
    return node.name


def _distinct(values: Iterable[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _count(n: int, nouns: tuple[str, str]) -> str:
    return f"{n} {nouns[0] if n == 1 else nouns[1]}"


def _everyone(n: int) -> str:
    return "the one person" if n == 1 else f"all {n} people"


def _join(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _join_items(items: Sequence[str]) -> str:
    if len(items) <= _NAMED:
        return _join(items)
    return f"{', '.join(items[:_NAMED])} and {len(items) - _NAMED} more"


def _capital(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}"


def _lower_first(text: str) -> str:
    return f"{text[:1].lower()}{text[1:]}"
