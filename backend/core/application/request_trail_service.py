"""A request's trail: what happened to an ask between two people, and what is due.

The steps are only what is on record, or what the escalation matrix makes due:

- asked: the request was recorded, and whether the DM to the person asked went
  out (``delivery``; never claimed when it did not);
- acknowledged: the person asked took it on, with the when their reply gave;
- named in the day report: worked out from the project's escalation matrix,
  as the day report works it out each day (``day_report_asks.escalated``). It
  happened when a whole-project day report of the project was sent while the
  ask stood at that level; otherwise it is due, or no report was there to
  carry it. Nobody gets a DM for it: the report names them;
- resolved by someone, or cleared when the merge landed; or closed as a copy.

There are no reminder steps: nothing reminds the person asked yet, apart from
retrying a DM that did not go out. Readers are the asker, the person asked and
the roles that read the requests board; an executive who is neither reads the
trail with no one named, and the board itself as counts only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.day_report_asks import Ask, AskScope, escalated, request_ask
from core.application.day_report_builder import DayReportBuilder
from core.application.escalation_matrix_service import EscalationMatrixService
from core.application.person_names import PersonNames, person_name
from core.domain.auth import Principal, Role
from core.domain.cross_person import (
    CrossPersonDelivery,
    CrossPersonRequest,
    CrossPersonRequestStatus,
)
from core.domain.errors import AuthorizationDenied, GraphNotFound, OpenProgramError
from core.domain.escalation_matrix import EscalationMatrix
from core.domain.graph import EdgeKind, EntityRef, FactEvent, NodeKind
from core.domain.reports import RunStatus
from core.ports.reports import DayReportRepository
from core.ports.repositories import (
    CrossPersonRequestRepository,
    GraphRepository,
    TimeSeriesRepository,
)

#: Said to an executive who asks for the list of requests.
REQUEST_COUNTS_ONLY = (
    "An executive reads the requests as counts: every ask names who is waiting on whom."
)
NOT_A_READER = "Only the people on this request and the roles that read the requests board see it."
#: How far back the resolved list and the count of resolved asks reach.
RESOLVED_WINDOW_DAYS = 30
#: An ask open this long counts as waiting over a week.
LONG_WAIT_DAYS = 7
_FACT_SOURCE = "cross_person_request"
# Runs read per report for the trail: a report goes out once a working day.
_RUNS_READ = 120
_NAME_READERS = (Role.ADMIN, Role.MGR, Role.PO, Role.SM)
_SENT_RUNS = frozenset({RunStatus.SENT, RunStatus.PARTIAL})

TrailStepKind = Literal[
    "unmatched",
    "asked",
    "acknowledged",
    "named",
    "nobody_above",
    "resolved",
    "cleared",
    "closed",
]
#: ``done`` happened; ``due`` the matrix makes it due, and no report has carried it.
TrailState = Literal["done", "due"]
#: For a step the day report carries: it went out, none went out while it stood
#: there, the project has no day report, or the day is still ahead.
ReportState = Literal["sent", "not_sent", "no_report", "upcoming"]
DmState = Literal["sent", "retrying", "not_delivered", "none"]


class RequestNotFound(OpenProgramError):
    """No such request in the tenant."""


def reads_request_names(principal: Principal) -> bool:
    """Whether the caller reads who asked whom: everyone on the board but an executive."""
    return any(principal.has_role(role) for role in _NAME_READERS)


def reads_requests(principal: Principal) -> bool:
    """The requests board's readers: the aggregate readers (an executive as counts)."""
    policy = AuthorizationPolicy()
    return policy.can(principal, Capability.READ_TEAM_AGGREGATE) or policy.can(
        principal, Capability.READ_EXEC_AGGREGATE
    )


@dataclass(frozen=True, kw_only=True)
class TrailStep:
    kind: TrailStepKind
    state: TrailState
    #: When it happened, for a step of a moment (asked, acknowledged, resolved).
    at: datetime | None = None
    #: The day, for a step of a day (named in a day report, nobody above).
    on: date | None = None
    #: Who stands at this step: the person asked, or whom the report names. None
    #: when not known, or for a reader who reads no names.
    who: str | None = None
    #: The matrix level's name for a named step ("Scrum master").
    role: str | None = None
    #: Who made it happen (acknowledged or resolved by); None for OpenProgram itself.
    by: str | None = None
    #: The DM to the person asked, on the asked step.
    dm: DmState | None = None
    dm_attempts: int = 0
    #: When the DM went out, where that was kept.
    dm_at: datetime | None = None
    #: What the acknowledgement said about when, in the reply's words.
    eta: str | None = None
    #: The project whose day report carries a named step.
    project_name: str | None = None
    report: ReportState | None = None
    #: Worked out from the escalation matrix, not recorded as an event.
    computed: bool = False


@dataclass(frozen=True, kw_only=True)
class RequestTrailView:
    request_id: str
    status: CrossPersonRequestStatus
    as_of: date
    names_hidden: bool
    steps: tuple[TrailStep, ...]


@dataclass(frozen=True, kw_only=True)
class RequestCountsView:
    as_of: date
    open: int
    acknowledged: int
    needs_resolution: int
    #: Open or acknowledged for ``LONG_WAIT_DAYS`` days or more.
    waiting_over_a_week: int
    #: Open, with the DM to the person asked not delivered.
    not_delivered: int
    resolved_recently: int
    resolved_window_days: int = RESOLVED_WINDOW_DAYS


class RequestTrailService:
    def __init__(
        self,
        *,
        requests: CrossPersonRequestRepository,
        facts: TimeSeriesRepository,
        graph: GraphRepository,
        builder: DayReportBuilder,
        escalation: EscalationMatrixService,
        reports: DayReportRepository,
        names: PersonNames,
    ) -> None:
        self._requests = requests
        self._facts = facts
        self._graph = graph
        self._builder = builder
        self._escalation = escalation
        self._reports = reports
        self._names = names

    # ---- the board ---------------------------------------------------------------------

    async def counts(self, principal: Principal, day: date) -> RequestCountsView:
        """The board as counts, for every reader of it (an executive's whole view)."""
        if not reads_requests(principal):
            raise AuthorizationDenied(NOT_A_READER)
        tenant_id = principal.tenant_id
        active = await self._requests.list_open(tenant_id)
        resolved = await self._requests.list_resolved_since(
            tenant_id, _window_start(day, RESOLVED_WINDOW_DAYS)
        )
        waiting = [
            request
            for request in active
            if request.status
            in {CrossPersonRequestStatus.OPEN, CrossPersonRequestStatus.ACKNOWLEDGED}
        ]
        return RequestCountsView(
            as_of=day,
            open=sum(1 for r in active if r.status is CrossPersonRequestStatus.OPEN),
            acknowledged=sum(
                1 for r in active if r.status is CrossPersonRequestStatus.ACKNOWLEDGED
            ),
            needs_resolution=sum(
                1 for r in active if r.status is CrossPersonRequestStatus.NEEDS_RESOLUTION
            ),
            waiting_over_a_week=sum(
                1 for r in waiting if (day - r.created_at.date()).days >= LONG_WAIT_DAYS
            ),
            not_delivered=sum(
                1
                for r in active
                if r.status is CrossPersonRequestStatus.OPEN
                and r.delivery is CrossPersonDelivery.NOT_DELIVERED
            ),
            resolved_recently=len(resolved),
        )

    async def resolved(
        self, principal: Principal, day: date, days: int
    ) -> list[CrossPersonRequest]:
        """The asks resolved in the ``days`` days up to ``day``, newest first, for name readers."""
        if not reads_requests(principal):
            raise AuthorizationDenied(NOT_A_READER)
        if not reads_request_names(principal):
            raise AuthorizationDenied(REQUEST_COUNTS_ONLY)
        until = datetime.combine(day + timedelta(days=1), time.min, UTC)
        return [
            request
            for request in await self._requests.list_resolved_since(
                principal.tenant_id, _window_start(day, days)
            )
            if request.updated_at < until
        ]

    # ---- one request's trail -------------------------------------------------------------

    async def trail(self, principal: Principal, request_id: str, day: date) -> RequestTrailView:
        tenant_id = principal.tenant_id
        request = await self._requests.get(tenant_id, request_id)
        if request is None:
            raise RequestNotFound(f"cross-person request {request_id} not found")
        involved = principal.subject in {request.requester_id, request.counterpart_id}
        if not involved and not reads_requests(principal):
            raise AuthorizationDenied(NOT_A_READER)
        hide = not involved and not reads_request_names(principal)
        names = await self._names.resolve(
            tenant_id,
            [person for person in (request.requester_id, request.counterpart_id) if person],
        )
        requester = person_name(names, request.requester_id, None)
        counterpart = (
            person_name(names, request.counterpart_id, request.counterpart_display_name)
            if request.counterpart_id
            else None
        )
        transitions = await self._transitions(request)

        def named(person_id: str | None) -> str | None:
            if hide or person_id is None:
                return None
            if person_id == request.requester_id:
                return requester
            if person_id == request.counterpart_id:
                return counterpart
            return person_name(names, person_id, None)

        steps = _unmatched(request, transitions, hide=hide, by=named(request.requester_id))
        if request.counterpart_id is not None:
            steps.append(self._asked(request, who=named(request.counterpart_id)))
        steps.extend(self._acknowledged(request, transitions, named))
        closed = self._closed(request, transitions, named)
        closed_on = closed.at.date() if closed is not None and closed.at is not None else None
        if request.counterpart_id is not None:
            steps.extend(await self._escalations(request, day, closed_on=closed_on, hide=hide))
        if closed is not None:
            steps.append(closed)
        return RequestTrailView(
            request_id=request.id,
            status=request.status,
            as_of=day,
            names_hidden=hide,
            steps=tuple(sorted(steps, key=lambda step: _step_order(step, request.created_at))),
        )

    def _asked(self, request: CrossPersonRequest, *, who: str | None) -> TrailStep:
        delivery = request.delivery
        dm: DmState = (
            "sent"
            if delivery is CrossPersonDelivery.SENT
            else "retrying"
            if delivery is CrossPersonDelivery.RETRYING
            else "not_delivered"
            if delivery is CrossPersonDelivery.NOT_DELIVERED
            else "none"
        )
        return TrailStep(
            kind="asked",
            state="done",
            at=request.created_at,
            who=who,
            dm=dm,
            dm_attempts=request.notify_attempts,
            # The attempt that went out is the last one claimed; an older row kept no time.
            dm_at=request.notify_last_attempt_at if dm == "sent" else None,
        )

    def _acknowledged(
        self,
        request: CrossPersonRequest,
        transitions: Sequence[FactEvent],
        named: Callable[[str | None], str | None],
    ) -> list[TrailStep]:
        facts = [fact for fact in transitions if fact.payload.get("transition") == "acknowledged"]
        if facts:
            first = facts[0]
            eta = next(
                (
                    value
                    for fact in facts
                    if isinstance(value := fact.payload.get("eta"), str) and value.strip()
                ),
                None,
            )
            changed_by = first.payload.get("changed_by")
            return [
                TrailStep(
                    kind="acknowledged",
                    state="done",
                    at=first.observed_at,
                    by=named(changed_by if isinstance(changed_by, str) else None),
                    eta=eta,
                )
            ]
        if request.status is CrossPersonRequestStatus.ACKNOWLEDGED:
            # Acknowledged before transitions were recorded: when is not known.
            return [TrailStep(kind="acknowledged", state="done", by=named(request.counterpart_id))]
        return []

    def _closed(
        self,
        request: CrossPersonRequest,
        transitions: Sequence[FactEvent],
        named: Callable[[str | None], str | None],
    ) -> TrailStep | None:
        if request.status is CrossPersonRequestStatus.RESOLVED:
            fact = _last(transitions, "resolved")
            if fact is None:
                return TrailStep(kind="resolved", state="done", at=request.updated_at)
            changed_by = fact.payload.get("changed_by")
            if not isinstance(changed_by, str) or not changed_by:
                # Nobody's act in OpenProgram: the merge request it waited on was merged.
                return TrailStep(kind="cleared", state="done", at=fact.observed_at)
            return TrailStep(
                kind="resolved", state="done", at=fact.observed_at, by=named(changed_by)
            )
        if request.status is CrossPersonRequestStatus.DISMISSED:
            fact = _last(transitions, "superseded") or _last(transitions, "dismissed")
            return TrailStep(
                kind="closed",
                state="done",
                at=fact.observed_at if fact is not None else request.updated_at,
            )
        return None

    async def _transitions(self, request: CrossPersonRequest) -> list[FactEvent]:
        """The request's recorded transitions, oldest first, from either person's facts.

        A transition is kept on the person asked, or the requester while nobody
        was matched, so both are read.
        """
        since = request.created_at - timedelta(days=1)
        found: dict[str, FactEvent] = {}
        for person in dict.fromkeys(
            person for person in (request.counterpart_id, request.requester_id) if person
        ):
            ref = EntityRef(tenant_id=request.tenant_id, kind=NodeKind.DEVELOPER, id=person)
            for fact in await self._facts.list_facts(request.tenant_id, ref, since):
                if fact.source == _FACT_SOURCE and fact.payload.get("request_id") == request.id:
                    found.setdefault(fact.correlation_id, fact)
        return sorted(found.values(), key=lambda fact: fact.observed_at)

    # ---- the matrix ------------------------------------------------------------------------

    async def _escalations(
        self, request: CrossPersonRequest, day: date, *, closed_on: date | None, hide: bool
    ) -> list[TrailStep]:
        """Where the day report names the ask, level by level, on each project it belongs to."""
        tenant_id = request.tenant_id
        created = request.created_at.date()
        end = min(day, closed_on) if closed_on is not None else day
        steps: list[TrailStep] = []
        seen: set[tuple[str | None, str | None, date]] = set()
        definitions = await self._reports.list_definitions(tenant_id)
        for project_id in await self._projects_of(request, created):
            try:
                scope = await self._builder.ask_scope(tenant_id, project_id, created)
            except GraphNotFound:
                continue
            base = request_ask(request, scope, created)
            if base is None:
                continue
            matrix = (await self._escalation.matrix_for(tenant_id, project_id)).matrix
            reports = [
                definition.report_id
                for definition in definitions
                if definition.project_id == project_id and definition.release_id is None
            ]
            sent_days = await self._sent_days(tenant_id, reports)
            # A level reached after the ask was closed never came.
            ladder = [
                step
                for step in _ladder(base, matrix, scope, created)
                if closed_on is None or step.on is None or step.on <= closed_on
            ]
            for index, step in enumerate(ladder):
                key = (step.who, step.role, step.on or created)
                if key in seen:
                    continue
                seen.add(key)
                # It stands at this level until the next one is reached, or it closes.
                after = ladder[index + 1].on if index + 1 < len(ladder) else None
                until = min(end, after - timedelta(days=1)) if after is not None else end
                steps.append(
                    replace(
                        _carried(step, sent_days, has_report=bool(reports), until=until, day=day),
                        who=None if hide else step.who,
                        project_name=scope.project.name,
                    )
                )
        return steps

    async def _projects_of(self, request: CrossPersonRequest, day: date) -> list[str]:
        """The projects whose pods hold the requester or the person asked, by name."""
        people = {person for person in (request.requester_id, request.counterpart_id) if person}
        nodes = {
            node.id: node for node in await self._graph.list_nodes(request.tenant_id, as_of=day)
        }
        contains = [
            edge
            for edge in await self._graph.list_edges(request.tenant_id, kind=EdgeKind.CONTAINS)
            if edge.is_active_on(day)
        ]
        pods = {
            edge.from_node_id
            for edge in contains
            if edge.to_node_id in people
            and (node := nodes.get(edge.from_node_id)) is not None
            and node.kind is NodeKind.POD
        }
        projects = {
            edge.from_node_id
            for edge in contains
            if edge.to_node_id in pods
            and (node := nodes.get(edge.from_node_id)) is not None
            and node.kind is NodeKind.PROJECT
        }
        return sorted(projects, key=lambda project_id: (nodes[project_id].name, project_id))

    async def _sent_days(self, tenant_id: str, report_ids: Iterable[str]) -> list[date]:
        days: set[date] = set()
        for report_id in report_ids:
            for run in await self._reports.list_runs(tenant_id, report_id, _RUNS_READ):
                if run.status in _SENT_RUNS:
                    days.add(run.report_date)
        return sorted(days)


def _ladder(base: Ask, matrix: EscalationMatrix, scope: AskScope, created: date) -> list[TrailStep]:
    """The levels the ask reaches as it waits, each from the day it reaches it.

    Each threshold of the matrix for the ask's kind is tried in turn with the
    day report's own rule (``escalated``): whom it names once it has waited
    that long, or that nobody stands above. A level that names the same person
    as the one before is the same step.
    """
    thresholds = sorted(
        {level.after_days[base.need] for level in matrix.levels if base.need in level.after_days}
    )
    steps: list[TrailStep] = []
    previous: tuple[str, str | None] | None = None
    for waited in thresholds:
        on = created + timedelta(days=waited)
        result = escalated(replace(base, waited_days=waited), matrix, scope)
        if result.escalated_to:
            key = (result.escalated_to, result.escalation_label)
            if key == previous:
                continue
            previous = key
            steps.append(
                TrailStep(
                    kind="named",
                    state="due",
                    on=on,
                    who=result.escalated_to,
                    role=result.escalation_label,
                    computed=True,
                )
            )
        elif result.nobody_above and not steps:
            # Due to go up, with nobody named above: the report says so, once.
            steps.append(
                TrailStep(
                    kind="nobody_above",
                    state="due",
                    on=on,
                    who=result.nobody_above,
                    computed=True,
                )
            )
    return steps


def _carried(
    step: TrailStep, sent_days: Sequence[date], *, has_report: bool, until: date, day: date
) -> TrailStep:
    """Whether a day report carried the step: one sent on or after its day, while it stood there."""
    if step.kind != "named" or step.on is None:
        return step
    if step.on > day:
        return replace(step, report="upcoming")
    if not has_report:
        return replace(step, report="no_report")
    carried = next((sent for sent in sent_days if step.on <= sent <= until), None)
    if carried is None:
        return replace(step, report="not_sent")
    return replace(step, state="done", on=carried, report="sent")


def _unmatched(
    request: CrossPersonRequest, transitions: Sequence[FactEvent], *, hide: bool, by: str | None
) -> list[TrailStep]:
    """The name in the ask matched nobody, so its requester was asked whom they meant."""
    if not request.raw_name:
        return []
    was = request.status is CrossPersonRequestStatus.NEEDS_RESOLUTION or any(
        fact.payload.get("transition") == "needs_resolution" for fact in transitions
    )
    if not was:
        return []
    return [
        TrailStep(
            kind="unmatched",
            state="done",
            at=request.created_at,
            who=None if hide else request.raw_name,
            by=by,
        )
    ]


def _window_start(day: date, days: int) -> datetime:
    """The start of the ``days`` days that end with ``day``: midnight UTC, ``days - 1`` before."""
    return datetime.combine(day - timedelta(days=days - 1), time.min, UTC)


def _last(transitions: Sequence[FactEvent], transition: str) -> FactEvent | None:
    found = [fact for fact in transitions if fact.payload.get("transition") == transition]
    return found[-1] if found else None


_KIND_ORDER: Mapping[str, int] = {
    "unmatched": 0,
    "asked": 1,
    "acknowledged": 2,
    "named": 3,
    "nobody_above": 3,
    "resolved": 4,
    "cleared": 4,
    "closed": 4,
}


def _step_order(step: TrailStep, asked_at: datetime) -> tuple[datetime, int]:
    """Oldest first. A step of a day sorts at its end (the report goes out that day); a
    step whose time was not kept sorts beside the ask."""
    if step.at is not None:
        moment = step.at
    elif step.on is not None:
        moment = datetime.combine(step.on, time.max, UTC)
    else:
        moment = asked_at
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment, _KIND_ORDER.get(step.kind, 9))
