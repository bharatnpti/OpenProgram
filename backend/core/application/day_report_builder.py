"""What a project's day report says, in the order a status mail reads.

1. In short: the note someone wrote for the day, the delivery date and what
   the forecast says of it, and what is needed most.
2. Where we stand: progress against the previous snapshot, why it changed
   (requirements that moved, scope added or removed, the delivery date
   moved), and how requirements stand against their gates.
3. Most important: the release readiness gaps that are blocking and close
   to their date, first, so the line cap never folds one away; then what
   threatens the delivery date.
4. What we need, and from whom: every ask, grouped by the person who can do
   it, with its kind, how long it has waited, and whom it escalated to under
   the project's escalation matrix (see ``day_report_asks``).
5. Open questions: the questions asked on the project's issues, and whether
   the person asked has answered.

A report covers a project or one of its releases. Every line is built from
what OpenProgram already holds, the same facts its screens show. Only derived
fields are used (a blocker's recorded description, a finding's reason, a
question's summary), never the text of anyone's check-in reply. Items are
about work, not people; the report never ranks or scores anyone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from core.application.blocker_resolution import BlockerResolutionService, ResolvedBlocker
from core.application.day_report_asks import (
    Ask,
    AskScope,
    age_words,
    blocker_ask,
    escalated,
    gate_asks,
    grouped,
    incoming_ask,
    most_needed,
    open_question_asks,
    request_ask,
    signal_ask,
    team_from,
    trimmed,
)
from core.application.delivery_service import DeliveryService
from core.application.escalation_matrix_service import EscalationMatrixService
from core.application.forecast_service import ForecastService, ScopeDeliveryView
from core.application.gate_service import GateBoardView, GateService, IssueGatesView
from core.application.persona_views import owned_project_tasks
from core.application.release_readiness_service import ReleaseReadinessService, ReportGaps
from core.application.risk_service import RiskService
from core.domain.delivery import (
    STAGE_LABELS,
    STAGE_ORDER,
    RequirementsSnapshot,
    StageMove,
    stage_moves,
)
from core.domain.errors import GraphNotFound
from core.domain.escalation_matrix import NEED_LABELS, NEED_ORDER, EscalationMatrix, NeedType
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    Release,
    Verdict,
)
from core.domain.gates import GateState, QuestionStatus, TrackedQuestion
from core.domain.graph import EdgeKind, NodeKind
from core.domain.release_readiness import ScopeKind
from core.domain.report_facts import (
    AskFacts,
    BypassFacts,
    DateFacts,
    DayReportFacts,
    GateFacts,
    ImportantFacts,
    NoteFacts,
    OwnerAsks,
    ProgressFacts,
    StageCount,
    StageMoveFacts,
)
from core.domain.reports import (
    DayReport,
    DayReportNote,
    ReportGroup,
    ReportSection,
    ReportTable,
    day_report_path,
)
from core.domain.risk import DriftFinding, RiskFinding
from core.domain.rollup import Rag
from core.ports.repositories import CrossPersonRequestRepository, GraphRepository, RollupRepository

MAX_MOVE_LINES = 12
MAX_IMPORTANT_LINES = 8
#: How long an answered or closed question stays in the report's table.
CLOSED_QUESTION_DAYS = 7

_VERDICT_WORDS = {
    Verdict.ON_TRACK: "on track",
    Verdict.AT_RISK: "at risk",
    Verdict.OFF_TRACK: "off track",
    Verdict.DONE: "done",
    Verdict.NO_DATE: "no delivery date set",
    Verdict.NOT_ENOUGH_DATA: "not enough history to forecast",
}
_HEARD_BACK = {
    QuestionStatus.PARTLY: "Partly",
    QuestionStatus.ANSWERED: "Yes",
    QuestionStatus.CLOSED_UNANSWERED: "No, the issue closed",
}


@dataclass(frozen=True, kw_only=True)
class _Facts:
    scope: AskScope
    release: Release | None
    snapshot: RequirementsSnapshot | None
    previous: RequirementsSnapshot | None
    delivery: ScopeDeliveryView | None
    board: GateBoardView
    matrix: EscalationMatrix
    findings: tuple[RiskFinding | DriftFinding, ...]
    #: Blocking release readiness gaps close to their date; none while readiness is off.
    readiness: ReportGaps = ReportGaps()


class DayReportBuilder:
    def __init__(
        self,
        *,
        graph_repository: GraphRepository,
        delivery_service: DeliveryService,
        rollup_repository: RollupRepository,
        blocker_resolution: BlockerResolutionService,
        risk_service: RiskService,
        cross_person_repository: CrossPersonRequestRepository,
        forecast_service: ForecastService,
        gate_service: GateService,
        escalation_service: EscalationMatrixService,
        console_base_url: str | None = None,
        readiness_service: ReleaseReadinessService | None = None,
    ) -> None:
        self._graph = graph_repository
        self._delivery = delivery_service
        self._rollups = rollup_repository
        self._blockers = blocker_resolution
        self._risks = risk_service
        self._requests = cross_person_repository
        self._forecast = forecast_service
        self._gates = gate_service
        self._escalation = escalation_service
        self._readiness = readiness_service
        self._console_base_url = console_base_url.rstrip("/") if console_base_url else None

    async def build(
        self,
        tenant_id: str,
        project_id: str,
        day: date,
        *,
        release_id: str | None = None,
        report_id: str | None = None,
        note: DayReportNote | None = None,
    ) -> DayReport:
        facts = await self._facts(tenant_id, project_id, day, release_id)
        asks = [
            escalated(ask, facts.matrix, facts.scope.names)
            for ask in await self._asks(tenant_id, facts, day)
        ]
        scope = facts.scope
        status = await self._rollups.latest_node_status(tenant_id, scope.project.ref, day)
        name = (
            f"{scope.project.name}, {facts.release.name}"
            if facts.release is not None
            else scope.project.name
        )
        console_path = day_report_path(scope.project.id, report_id)
        owners = grouped(asks)
        stand = _stand(facts, day)
        return DayReport(
            title=f"{name}: day report, {_day_label(day)}",
            project_name=name,
            report_date=day,
            rag=status.rag if status is not None else Rag.UNKNOWN,
            headline=_headline(asks),
            percent_complete=facts.snapshot.percent_complete if facts.snapshot else None,
            progress_line=_progress_line(facts.snapshot, facts.previous),
            sections=(
                ReportSection(title="In short", lines=_in_short(facts, asks, note)),
                ReportSection(title="Where we stand", groups=_where_we_stand(facts, stand)),
                ReportSection(
                    title="Most important",
                    lines=_most_important(facts),
                    empty_text="Nothing threatens the delivery date today.",
                ),
                ReportSection(
                    title="What we need, and from whom",
                    groups=tuple(
                        ReportGroup(
                            heading=owner or "Nobody named yet",
                            lines=tuple(ask.line() for ask in items),
                        )
                        for owner, items in owners
                    ),
                    empty_text="Nothing is needed from anyone today.",
                ),
                ReportSection(
                    title="Open questions",
                    table=_question_table(facts.board.questions, scope, day),
                    empty_text="No open questions.",
                ),
            ),
            console_path=console_path,
            console_url=(
                f"{self._console_base_url}{console_path}" if self._console_base_url else None
            ),
            attention_count=len(asks),
            facts=DayReportFacts(
                note=_note_facts(scope, note),
                delivery=_date_facts(facts),
                progress=_progress_facts(facts, stand),
                gates=_gate_facts(facts.board),
                important=_important_facts(facts),
                asks=_owner_asks(owners, most_needed(asks)),
            ),
        )

    async def check_release(self, tenant_id: str, project_id: str, release_id: str) -> None:
        """Raise GraphNotFound unless the release belongs to the project."""
        await self._release(tenant_id, project_id, release_id)

    # ---- gathering --------------------------------------------------------------------

    async def _facts(
        self, tenant_id: str, project_id: str, day: date, release_id: str | None
    ) -> _Facts:
        release = await self._release(tenant_id, project_id, release_id)
        scope = await self._scope(tenant_id, project_id, day, release)
        return _Facts(
            scope=scope,
            release=release,
            snapshot=await self._delivery.snapshot(tenant_id, project_id, day, release),
            previous=await self._delivery.previous_snapshot(tenant_id, project_id, day, release),
            delivery=await self._delivery_view(tenant_id, project_id, day, release),
            board=await self._gates.board(tenant_id, project_id, day, release),
            matrix=(await self._escalation.matrix_for(tenant_id, project_id)).matrix,
            findings=(
                *await self._risks.project_risks(tenant_id, project_id, day),
                *await self._risks.project_drift(tenant_id, project_id, day),
            ),
            readiness=(
                await self._readiness.report_gaps(tenant_id, project_id, release)
                if self._readiness is not None
                else ReportGaps()
            ),
        )

    async def _release(
        self, tenant_id: str, project_id: str, release_id: str | None
    ) -> Release | None:
        if not release_id:
            return None
        release = await self._forecast.release(tenant_id, release_id)
        if release.project_id != project_id:
            raise GraphNotFound(f"No release {release_id!r} in project {project_id!r}.")
        return release

    async def _delivery_view(
        self, tenant_id: str, project_id: str, day: date, release: Release | None
    ) -> ScopeDeliveryView | None:
        scope = CommitmentScope(
            kind=CommitmentScopeKind.RELEASE if release else CommitmentScopeKind.PROJECT,
            id=release.release_id if release else project_id,
            project_id=project_id,
        )
        try:
            return await self._forecast.scope_delivery(tenant_id, scope, day)
        except GraphNotFound:
            return None

    async def _scope(
        self, tenant_id: str, project_id: str, day: date, release: Release | None
    ) -> AskScope:
        # Read as of the report's day: a pod or person deleted since still
        # appears on an earlier day's report, as it was that day.
        project = await self._graph.get_node(tenant_id, project_id, as_of=day)
        if project is None or project.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"project {project_id} not found for tenant {tenant_id}")
        owned = await owned_project_tasks(self._graph, tenant_id, [project_id], day)
        tasks = {
            task.id: task
            for task in owned.get(project_id, ())
            if release is None or release.includes(task.metadata)
        }
        nodes = {node.id: node for node in await self._graph.list_nodes(tenant_id, as_of=day)}
        contains = [
            edge
            for edge in await self._graph.list_edges(tenant_id, kind=EdgeKind.CONTAINS)
            if edge.is_active_on(day)
        ]
        names = {node.id: node.name for node in nodes.values() if node.kind is NodeKind.DEVELOPER}
        all_teams = {
            node.id: team_from(node, names) for node in nodes.values() if node.kind is NodeKind.POD
        }
        team_ids = {
            edge.to_node_id
            for edge in contains
            if edge.from_node_id == project_id and edge.to_node_id in all_teams
        }
        member_teams: dict[str, str] = {}
        task_teams: dict[str, str] = {}
        for edge in contains:
            if edge.from_node_id not in all_teams:
                continue
            if edge.to_node_id in names:
                member_teams.setdefault(edge.to_node_id, edge.from_node_id)
            elif edge.to_node_id in tasks:
                task_teams.setdefault(edge.to_node_id, edge.from_node_id)
        assignees: dict[str, str] = {}
        for edge in await self._graph.list_edges(tenant_id, kind=EdgeKind.ASSIGNED_TO):
            if edge.is_active_on(day) and edge.from_node_id in names:
                assignees.setdefault(edge.to_node_id, edge.from_node_id)
        members = {member for member, team in member_teams.items() if team in team_ids}
        members |= {assignees[task_id] for task_id in tasks if task_id in assignees}
        return AskScope(
            project=project,
            tasks=tasks,
            teams={team_id: all_teams[team_id] for team_id in team_ids},
            all_teams=all_teams,
            members=frozenset(members),
            names=names,
            assignees=assignees,
            task_teams=task_teams,
            member_teams=member_teams,
            release_only=release is not None,
        )

    async def _asks(self, tenant_id: str, facts: _Facts, day: date) -> list[Ask]:
        scope = facts.scope
        asks: list[Ask] = []
        seen: set[str] = set()
        for member in sorted(scope.members):
            for blocker in await self._blockers.open_blockers_for_developer(tenant_id, member, day):
                if blocker.blocker_id in seen or not _in_scope(blocker, scope):
                    continue
                seen.add(blocker.blocker_id)
                asks.append(blocker_ask(blocker, scope, day))
        for developer_id, blocker in await self._incoming(tenant_id, scope, day):
            asks.append(incoming_ask(developer_id, blocker, scope, day))
        for request in await self._requests.list_open(tenant_id):
            if (ask := request_ask(request, scope, day)) is not None:
                asks.append(ask)
        asks.extend(
            _signal_asks(
                [
                    finding
                    for finding in facts.findings
                    if not scope.release_only or finding.entity_ref.id in scope.tasks
                ],
                scope,
                day,
            )
        )
        asks.extend(gate_asks(facts.board, scope, facts.matrix, day))
        asks.extend(open_question_asks(facts.board, scope, day))
        asks.extend(_readiness_asks(facts.readiness, scope, facts.matrix, day))
        return asks

    async def _incoming(
        self, tenant_id: str, scope: AskScope, day: date
    ) -> list[tuple[str, ResolvedBlocker]]:
        """Open blockers of people outside the project that wait on its work.

        Found two ways, as the rollups find them: blockers recorded on the
        project's issues, and other people's blockers whose waited-on issue or
        team belongs to the project.
        """
        found: dict[str, tuple[str, ResolvedBlocker]] = {}
        for developer, blocker in await self._blockers.blockers_on_tasks(
            tenant_id, scope.tasks, day
        ):
            if developer.id not in scope.members:
                found.setdefault(blocker.blocker_id, (developer.id, blocker))
        for developer_id in sorted(set(scope.names) - scope.members):
            for blocker in await self._blockers.open_blockers_for_developer(
                tenant_id, developer_id, day
            ):
                waits_on_task = (
                    blocker.depends_on_ref is not None and blocker.depends_on_ref.id in scope.tasks
                )
                waits_on_team = not scope.release_only and any(
                    pod_id in scope.teams for pod_id in blocker.depends_on_pod_ids
                )
                if waits_on_task or waits_on_team:
                    found.setdefault(blocker.blocker_id, (developer_id, blocker))
        return list(found.values())


# ---- the sections ---------------------------------------------------------------------


def _in_short(facts: _Facts, asks: Sequence[Ask], note: DayReportNote | None) -> tuple[str, ...]:
    lines: list[str] = []
    if note is not None and note.text:
        author = facts.scope.names.get(note.author, "")
        lines.append(f"{author}: {note.text}" if author else note.text)
    if facts.delivery is not None:
        lines.append(_delivery_line(facts.delivery))
    needed = most_needed(asks)
    if needed:
        lines.append("Needed most: " + _joined([ask.brief() for ask in needed]) + ".")
    return tuple(lines)


def _delivery_line(view: ScopeDeliveryView) -> str:
    verdict = _VERDICT_WORDS[view.verdict]
    if view.target is None:
        return f"Delivery: {verdict}."
    source = " (the Jira release date)" if view.target_source == "jira_release" else ""
    line = f"Delivery {_day_label(view.target)}{source}: {verdict}."
    if view.history.p85 is not None:
        line += f" History says 85% likely by {_day_label(view.history.p85)}."
    if view.team.latest is not None and view.team.latest_key:
        line += (
            f" The team's latest date is {_day_label(view.team.latest)} ({view.team.latest_key})."
        )
    return line


@dataclass(frozen=True, kw_only=True)
class _Stand:
    """Where we stand, in parts. The report's lines and the console's facts are both
    built from these, so the two cannot disagree."""

    since: date | None
    unmapped: str | None
    moves: tuple[StageMove, ...]
    more_moves: int
    scope_line: str | None
    date_lines: tuple[str, ...]
    #: Said in place of the changes when there are none.
    placeholder: str | None


def _stand(facts: _Facts, day: date) -> _Stand:
    snapshot, previous = facts.snapshot, facts.previous
    unmapped = None
    if snapshot is not None and snapshot.total and snapshot.unmapped_statuses:
        unmapped = (
            "Statuses no stage names yet: "
            + ", ".join(snapshot.unmapped_statuses)
            + " (counted by their broad state; set them under Configuration, Delivery stages)."
        )
    since = previous.day if previous is not None else None
    moves, more, scope_line = _move_parts(previous, snapshot)
    date_lines = tuple(_date_lines(facts.delivery, since, day))
    placeholder = None
    if not (moves or more or scope_line or date_lines):
        placeholder = (
            "Nothing changed stage."
            if previous is not None
            else "The first snapshot is today; changes show from tomorrow."
        )
    return _Stand(
        since=since,
        unmapped=unmapped,
        moves=moves,
        more_moves=more,
        scope_line=scope_line,
        date_lines=date_lines,
        placeholder=placeholder,
    )


def _where_we_stand(facts: _Facts, stand: _Stand) -> tuple[ReportGroup, ...]:
    snapshot, previous = facts.snapshot, facts.previous
    progress = [_progress_line(snapshot, previous)]
    if snapshot is not None and snapshot.total:
        progress.append(_stage_line(snapshot, previous))
        if stand.unmapped:
            progress.append(stand.unmapped)
    since = stand.since
    changed = [stand.placeholder] if stand.placeholder else [*_move_lines(stand), *stand.date_lines]
    groups = [
        ReportGroup(heading="Progress", lines=tuple(progress)),
        ReportGroup(
            heading=f"What changed since {_day_label(since)}" if since else "What changed",
            lines=tuple(changed),
        ),
    ]
    checks = _gate_lines(facts.board)
    if checks:
        groups.append(ReportGroup(heading="Acceptance and tests", lines=tuple(checks)))
    return tuple(groups)


def _date_reasons(facts: _Facts) -> tuple[str, ...]:
    """The delivery date's reasons Most important leads with, while the date is in danger."""
    delivery = facts.delivery
    if delivery is not None and delivery.verdict in {Verdict.AT_RISK, Verdict.OFF_TRACK}:
        return delivery.reasons[:3]
    return ()


def _bypass_line(issue: IssueGatesView) -> str:
    return (
        f"{issue.key} reached {STAGE_LABELS[issue.stage].lower()} without "
        f"{_joined(list(issue.passed_without))} passing."
    )


def _risk_lines(facts: _Facts) -> list[str]:
    return [
        trimmed(finding.reason)
        for finding in facts.findings
        if finding.severity is Rag.RED
        and (not facts.scope.release_only or finding.entity_ref.id in facts.scope.tasks)
    ]


def _most_important(facts: _Facts) -> tuple[str, ...]:
    # A blocking readiness gap first: at most four lines (ReportGaps.lines), so
    # the cap below never folds one into "and N more". Without one, the order
    # is what it always was.
    lines: list[str] = list(facts.readiness.lines)
    lines.extend(_date_reasons(facts))
    lines.extend(_bypass_line(issue) for issue in facts.board.issues if issue.passed_without)
    lines.extend(_risk_lines(facts))
    kept = list(dict.fromkeys(lines))
    if len(kept) > MAX_IMPORTANT_LINES:
        rest = len(kept) - MAX_IMPORTANT_LINES
        kept = [*kept[:MAX_IMPORTANT_LINES], f"and {rest} more."]
    return tuple(kept)


def _question_table(
    questions: Sequence[TrackedQuestion], scope: AskScope, day: date
) -> ReportTable:
    def shown(question: TrackedQuestion) -> bool:
        if not question.confirmed:
            return False
        if question.status in {QuestionStatus.NOT_YET, QuestionStatus.PARTLY}:
            return True
        changed = question.updated_at or question.asked_at
        return changed.date() >= day - timedelta(days=CLOSED_QUESTION_DAYS)

    rows = sorted(
        (question for question in questions if shown(question)),
        key=lambda question: (
            question.status not in {QuestionStatus.NOT_YET, QuestionStatus.PARTLY},
            question.asked_at,
        ),
    )
    return ReportTable(
        columns=("Ticket", "What we asked", "Asked to", "Asked on", "Heard back?"),
        rows=tuple(
            (
                question.issue_key,
                trimmed(question.summary, 140),
                question.asked_to_name or scope.names.get(question.asked_to) or "—",
                _day_label(question.asked_at.date()),
                _HEARD_BACK.get(
                    question.status,
                    f"Not yet ({age_words(max(0, (day - question.asked_at.date()).days))})",
                ),
            )
            for question in rows
        ),
    )


# ---- the console's facts --------------------------------------------------------------
#
# The same report, structured for the console's pictures. Each fact comes from the
# parts its line is written from, and is left out where the line does not say it.

#: How the delivery date's reasons (forecast_service) open, by what the date bar
#: draws of them. A reason that opens any other way stays a line under Most important.
_DRAWN_REASONS: tuple[tuple[str, str], ...] = (
    ("Committed for ", "committed"),
    ("No delivery date is committed yet", "no_date"),
    ("No date committed; Jira's release date", "jira"),
    ("History: ", "forecast"),
    ("No history yet", "history"),
    ("Nothing reached production in the last ", "history"),
    ("Team dates: ", "team"),
)


def _reason_kind(reason: str) -> str | None:
    if reason.startswith("Only ") and "of history; a forecast needs" in reason:
        return "history"
    return next((kind for opening, kind in _DRAWN_REASONS if reason.startswith(opening)), None)


def _note_facts(scope: AskScope, note: DayReportNote | None) -> NoteFacts | None:
    if note is None or not note.text:
        return None
    return NoteFacts(author=scope.names.get(note.author, ""), text=note.text)


def _date_facts(facts: _Facts) -> DateFacts | None:
    view = facts.delivery
    if view is None:
        return None
    said = {kind for reason in _date_reasons(facts) if (kind := _reason_kind(reason))}
    # In short names the 85% date and the team's date only beside a date.
    beside = view.target is not None
    commitment = view.commitment
    actor = commitment.changes[-1].changed_by if commitment.changes else None
    committed = "committed" in said and actor is not None
    moved = committed and bool(commitment.times_moved and commitment.moved_days)
    history = view.history
    short = "history" in said and history.sample_days < history.needed_days
    team = view.team
    team_said = (beside and team.latest is not None and bool(team.latest_key)) or "team" in said
    return DateFacts(
        verdict=view.verdict,
        target=view.target,
        target_source=view.target_source,
        committed_by=view.actor_names.get(actor, actor) if committed and actor else None,
        times_moved=commitment.times_moved if moved else 0,
        moved_days=commitment.moved_days if moved else None,
        p50=history.p50 if "forecast" in said else None,
        p85=history.p85 if beside or "forecast" in said else None,
        history_days=history.sample_days if short else None,
        history_needed=history.needed_days if short else None,
        no_forecast_reason=history.reason if "history" in said else None,
        team_latest=team.latest if team_said else None,
        team_latest_key=team.latest_key if team_said else None,
    )


def _progress_facts(facts: _Facts, stand: _Stand) -> ProgressFacts:
    snapshot, previous = facts.snapshot, facts.previous
    stages: tuple[StageCount, ...] = ()
    if snapshot is not None and snapshot.total:
        stages = tuple(
            StageCount(
                stage=stage,
                count=snapshot.stage_counts.get(stage, 0),
                previous=previous.stage_counts.get(stage, 0) if previous is not None else None,
            )
            for stage in STAGE_ORDER
        )
    if stand.placeholder:
        changes: tuple[str, ...] = (stand.placeholder,)
    else:
        changes = (*([stand.scope_line] if stand.scope_line else []), *stand.date_lines)
    return ProgressFacts(
        percent=snapshot.percent_complete if snapshot is not None else None,
        since=stand.since,
        total=snapshot.total if snapshot is not None else 0,
        stages=stages,
        moves=tuple(
            StageMoveFacts(
                key=move.key,
                title=trimmed(move.title, 80),
                from_stage=move.from_stage,
                to_stage=move.to_stage,
            )
            for move in stand.moves
        ),
        more_moves=stand.more_moves,
        other_changes=changes,
        notes=(stand.unmapped,) if stand.unmapped else (),
    )


def _gate_facts(board: GateBoardView) -> tuple[GateFacts, ...]:
    """Each gate's requirements once each: moved on without it, else by state."""
    gates: list[GateFacts] = []
    for template in board.templates:
        states = [
            (template.name in issue.passed_without, evaluation.state)
            for issue in board.issues
            for evaluation in issue.evaluations
            if evaluation.template.template_id == template.template_id
        ]
        if not states:
            continue
        rest = [state for behind, state in states if not behind]
        gates.append(
            GateFacts(
                name=template.name,
                guards_stage=template.guards_stage,
                total=len(states),
                passed=rest.count(GateState.PASSED),
                bypassed=len(states) - len(rest),
                failed=rest.count(GateState.FAILED),
                open=rest.count(GateState.OPEN),
                missing=rest.count(GateState.MISSING),
            )
        )
    return tuple(gates)


def _important_facts(facts: _Facts) -> ImportantFacts:
    reasons = _date_reasons(facts)
    kinds = [_reason_kind(reason) for reason in reasons]
    behind = [issue for issue in facts.board.issues if issue.passed_without]
    readiness = facts.readiness.lines
    said = {*reasons, *(_bypass_line(issue) for issue in behind), *readiness}
    risks = [line for line in dict.fromkeys(_risk_lines(facts)) if line not in said]
    return ImportantFacts(
        drawn=tuple(kind for kind in kinds if kind is not None),
        bypassed=tuple(
            BypassFacts(key=issue.key, stage=issue.stage, gates=issue.passed_without)
            for issue in behind
        ),
        risks=len(risks),
        lines=(
            *readiness,
            *(reason for reason, kind in zip(reasons, kinds, strict=True) if kind is None),
        ),
        readiness_gaps=len(facts.readiness.gaps),
    )


def _owner_asks(
    owners: Sequence[tuple[str | None, Sequence[Ask]]], needed: Sequence[Ask]
) -> tuple[OwnerAsks, ...]:
    # The very asks In short names, not equal ones: two asks may read alike.
    most = {id(ask) for ask in needed}
    return tuple(
        OwnerAsks(
            heading=owner or "Nobody named yet",
            named=bool(owner),
            asks=tuple(
                AskFacts(
                    need=ask.need,
                    text=ask.text,
                    detail=ask.detail,
                    waited_days=ask.waited_days,
                    issue_key=ask.issue_key,
                    escalated_to=ask.escalated_to,
                    escalation_label=ask.escalation_label,
                    needed_most=id(ask) in most,
                    open_question=ask.open_question,
                )
                for ask in items
            ),
        )
        for owner, items in owners
    )


# ---- lines ----------------------------------------------------------------------------


def _readiness_asks(
    readiness: ReportGaps, scope: AskScope, matrix: EscalationMatrix, day: date
) -> list[Ask]:
    """One decision per blocking readiness gap: the decision owner's, or a pod's scrum master's.

    A pod's ask climbs from that pod; a project's or a release's from the
    project's first team, as the matrix's team levels need one.
    """
    decider = scope.names.get(matrix.decision_owner_id or "")
    first = min(scope.teams.values(), key=lambda team: team.node.name.casefold(), default=None)
    asks: list[Ask] = []
    for gap in readiness.gaps:
        if gap.scope.kind is ScopeKind.POD:
            team = scope.all_teams.get(gap.scope.id)
            owner = team.scrum_master if team is not None else None
        else:
            team, owner = first, decider
        asks.append(
            Ask(
                need=NeedType.DECISION,
                owner=owner,
                text=trimmed(gap.ask),
                waited_days=max(0, (day - gap.entered_on).days) if gap.entered_on else None,
                team=team,
            )
        )
    return asks


def _signal_asks(
    findings: Sequence[RiskFinding | DriftFinding], scope: AskScope, day: date
) -> list[Ask]:
    asks: list[Ask] = []
    seen: set[str] = set()
    for finding in findings:
        key = f"{finding.entity_ref.id}:{trimmed(finding.reason)}"
        if key not in seen:
            seen.add(key)
            asks.append(signal_ask(finding, scope, day))
    return asks


def _gate_lines(board: GateBoardView) -> list[str]:
    lines: list[str] = []
    for template in board.templates:
        states = [
            evaluation.state
            for issue in board.issues
            for evaluation in issue.evaluations
            if evaluation.template.template_id == template.template_id
        ]
        if not states:
            continue
        counts = {state: states.count(state) for state in GateState}
        parts = [f"{counts[GateState.PASSED]} of {len(states)} passed"]
        if counts[GateState.OPEN]:
            parts.append(f"{counts[GateState.OPEN]} open")
        if counts[GateState.FAILED]:
            parts.append(f"{counts[GateState.FAILED]} failed")
        if counts[GateState.MISSING]:
            parts.append(f"{counts[GateState.MISSING]} with nothing confirmed")
        behind = sum(1 for issue in board.issues if template.name in issue.passed_without)
        line = f"{template.name} (before {STAGE_LABELS[template.guards_stage].lower()}): "
        line += ", ".join(parts)
        if behind:
            line += f"; {behind} moved on without it"
        lines.append(line + ".")
    return lines


def _stage_line(snapshot: RequirementsSnapshot, previous: RequirementsSnapshot | None) -> str:
    parts: list[str] = []
    for stage in STAGE_ORDER:
        count = snapshot.stage_counts.get(stage, 0)
        change = ""
        if previous is not None:
            delta = count - previous.stage_counts.get(stage, 0)
            change = f" ({delta:+d})" if delta else ""
        parts.append(f"{STAGE_LABELS[stage]} {count}{change}")
    return " · ".join(parts)


def _move_parts(
    previous: RequirementsSnapshot | None, snapshot: RequirementsSnapshot | None
) -> tuple[tuple[StageMove, ...], int, str | None]:
    """The moves the report lists, how many more it only counts, and its scope line."""
    if previous is None or snapshot is None:
        return (), 0, None
    moves = stage_moves(previous, snapshot)
    scope_change = snapshot.total - previous.total
    scope_line = (
        f"Scope {scope_change:+d} {'requirement' if abs(scope_change) == 1 else 'requirements'}"
        if scope_change
        else None
    )
    listed = tuple(move for move in moves[:MAX_MOVE_LINES] if move.from_stage or move.to_stage)
    return listed, max(0, len(moves) - MAX_MOVE_LINES), scope_line


def _move_lines(stand: _Stand) -> list[str]:
    lines: list[str] = []
    for move in stand.moves:
        title = trimmed(move.title, 80)
        if move.from_stage is None and move.to_stage is not None:
            lines.append(f"{move.key} {title}: new, in {STAGE_LABELS[move.to_stage]}")
        elif move.to_stage is None and move.from_stage is not None:
            lines.append(f"{move.key} {title}: left the scope from {STAGE_LABELS[move.from_stage]}")
        elif move.from_stage is not None and move.to_stage is not None:
            lines.append(
                f"{move.key} {title}: "
                f"{STAGE_LABELS[move.from_stage]} → {STAGE_LABELS[move.to_stage]}"
            )
    if stand.more_moves:
        lines.append(f"and {stand.more_moves} more")
    if stand.scope_line:
        lines.append(stand.scope_line)
    return lines


def _date_lines(view: ScopeDeliveryView | None, since: date | None, day: date) -> list[str]:
    """The delivery date's changes after ``since`` up to ``day``, with who and why."""
    if view is None:
        return []
    changes = list(view.commitment.changes)
    lines: list[str] = []
    for index, change in enumerate(changes):
        when = change.changed_at.date()
        if when > day or (since is not None and when <= since) or (since is None and when != day):
            continue
        before = _earlier_target(changes[:index])
        who = view.actor_names.get(change.changed_by, "someone")
        why = f": {trimmed(change.note, 120)}" if change.note else ""
        lines.append(_date_change(before, change) + f" ({who}{why}).")
    return lines


def _earlier_target(changes: Sequence[DateChange]) -> date | None:
    return next((change.target_date for change in reversed(changes)), None)


def _date_change(before: date | None, change: DateChange) -> str:
    if change.target_date is None:
        return "The delivery date was cleared"
    if before is None:
        return f"The delivery date was set to {_day_label(change.target_date)}"
    return f"The delivery date moved from {_day_label(before)} to {_day_label(change.target_date)}"


def _progress_line(
    snapshot: RequirementsSnapshot | None, previous: RequirementsSnapshot | None
) -> str:
    if snapshot is None or snapshot.total == 0:
        return "No requirements counted yet."
    percent = snapshot.percent_complete or 0.0
    if snapshot.has_points:
        line = (
            f"{percent:.0f}% complete: {snapshot.points_done:g} of "
            f"{snapshot.points_total:g} story points in production"
        )
    else:
        line = (
            f"{percent:.0f}% complete: {snapshot.done} of {snapshot.total} "
            "requirements in production"
        )
    if previous is not None and previous.percent_complete is not None:
        line += f" ({previous.percent_complete:.0f}% on {_day_label(previous.day)})"
    return line + "."


def _headline(asks: Sequence[Ask]) -> str:
    if not asks:
        return "Nothing is needed from anyone today."
    counts = {need: sum(1 for ask in asks if ask.need is need) for need in NEED_ORDER}
    parts = [_count(count, need) for need, count in counts.items() if count]
    escalated_count = sum(1 for ask in asks if ask.escalated_to)
    line = f"{_joined(parts)} needed"
    if escalated_count:
        line += f", {escalated_count} escalated"
    return line + "."


def _count(count: int, need: NeedType) -> str:
    word = NEED_LABELS[need].lower()
    if count == 1:
        return f"1 {word}"
    return f"{count} {word}es" if word.endswith("x") else f"{count} {word}s"


def _joined(parts: Sequence[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _in_scope(blocker: ResolvedBlocker, scope: AskScope) -> bool:
    if blocker.work_item_ref is not None and blocker.work_item_ref.id in scope.tasks:
        return True
    if scope.release_only:
        return False
    if any(pod_id in scope.teams for pod_id in blocker.pod_ids):
        return True
    return blocker.unattributed and not blocker.pod_ids


def _day_label(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b %Y}"
