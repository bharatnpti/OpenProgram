"""What a project needs from whom: its asks, their owners, and how far each escalated.

An ask is one thing the project needs from one person, of one kind (fix,
decision, answer, review). Its owner is the person who can do it:

- a blocker that waits on another team's issue: that issue's assignee, else
  that team's scrum master;
- any other blocker: the scrum master of the blocked work's team, else the
  person who reported it;
- someone outside the project waiting on its issue: the issue's assignee;
- a request between people: the person asked;
- a risk or drift signal: the issue's owner;
- a gate item to sign off, or one still missing: the project's decision owner
  for a kind the product owner or a manager signs off, else the issue's
  assignee;
- a question asked on an issue: the person asked.

How long an ask has waited takes it up the project's escalation matrix; the
highest level it reached whose contact is someone other than its owner is the
one named. Asks describe work; nothing here ranks or scores anyone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime

from core.application.blocker_resolution import ResolvedBlocker
from core.application.gate_service import GateBoardView, IssueGatesView
from core.domain.auth import Role
from core.domain.cross_person import CrossPersonRequest, CrossPersonRequestKind
from core.domain.delivery import STAGE_LABELS, STAGE_ORDER
from core.domain.escalation import EscalationContact, escalation_contacts_from_metadata
from core.domain.escalation_matrix import (
    NEED_LABELS,
    NEED_ORDER,
    ContactSource,
    EscalationLevel,
    EscalationMatrix,
    NeedType,
    reached_levels,
)
from core.domain.gates import GateItem, GateTemplate, ItemKind, ItemStatus, QuestionStatus
from core.domain.graph import GraphNode
from core.domain.risk import DriftFinding, RiskFinding

MAX_TEXT = 160
#: Roles whose sign-off is a decision, not a review.
DECIDING_ROLES = frozenset({Role.PO, Role.MGR})

_REQUEST_NEEDS = {
    CrossPersonRequestKind.DEPENDENCY: NeedType.FIX,
    CrossPersonRequestKind.REVIEW: NeedType.REVIEW,
    CrossPersonRequestKind.INPUT: NeedType.ANSWER,
}
_REQUEST_WORDS = {
    CrossPersonRequestKind.DEPENDENCY: "a dependency",
    CrossPersonRequestKind.REVIEW: "a review",
    CrossPersonRequestKind.INPUT: "input",
}


@dataclass(frozen=True, kw_only=True)
class Team:
    node: GraphNode
    scrum_master: str | None
    manager: str | None


@dataclass(frozen=True, kw_only=True)
class AskScope:
    """Who is who in a project, as the asks need it."""

    project: GraphNode
    #: The project's requirements (or one release's), by task id.
    tasks: Mapping[str, GraphNode]
    #: The project's teams.
    teams: Mapping[str, Team]
    #: Every team in the tenant, for work the project waits on.
    all_teams: Mapping[str, Team]
    #: The people on the project's teams or assigned its requirements.
    members: frozenset[str]
    #: Every member's display name, by id.
    names: Mapping[str, str]
    #: Each task's assignee, by task id.
    assignees: Mapping[str, str]
    #: The team each task belongs to, by task id.
    task_teams: Mapping[str, str]
    #: The first team each member is on, by member id.
    member_teams: Mapping[str, str]
    #: A report on one release counts only what is on the release's issues.
    release_only: bool = False

    def task_by_key(self, key: str) -> str | None:
        return next(
            (task_id for task_id, task in self.tasks.items() if task_key(task) == key), None
        )

    def team_of_task(self, task_id: str | None) -> Team | None:
        team_id = self.task_teams.get(task_id or "")
        return self.all_teams.get(team_id or "")

    def team_of_member(self, member_id: str | None) -> Team | None:
        team_id = self.member_teams.get(member_id or "")
        return self.all_teams.get(team_id or "")

    def assignee_name(self, task_id: str | None) -> str | None:
        return self.names.get(self.assignees.get(task_id or "", ""))


@dataclass(frozen=True, kw_only=True)
class Ask:
    need: NeedType
    #: Display name of who can do it; None when nobody is named yet.
    owner: str | None
    text: str
    #: Days it has waited; None when OpenProgram cannot tell.
    waited_days: int | None
    #: Who raised it or what it waits on, said after how long it waited.
    detail: str = ""
    team: Team | None = None
    issue_key: str | None = None
    #: The level it reached (1 is its owner), and who stands there.
    level: int = 1
    escalated_to: str | None = None
    escalation_label: str | None = None

    def line(self) -> str:
        context = [
            part
            for part in (
                age_words(self.waited_days) if self.waited_days is not None else "",
                self.detail,
            )
            if part
        ]
        aside = f" ({'; '.join(context)})" if context else ""
        line = f"{NEED_LABELS[self.need]}: {self.text}{aside}."
        if self.escalated_to:
            line += f" Escalated to {self.escalated_to} ({self.escalation_label})."
        return line

    def brief(self) -> str:
        """'a fix from Ben on CHK-109 (6 days)', for the report's opening."""
        need = NEED_LABELS[self.need].lower()
        article = "an" if need[0] in "aeiou" else "a"
        on = f" on {self.issue_key}" if self.issue_key else ""
        waited = f" ({age_words(self.waited_days)})" if self.waited_days else ""
        return f"{article} {need} from {self.owner or 'someone not yet named'}{on}{waited}"


def team_from(node: GraphNode, names: Mapping[str, str]) -> Team:
    contacts = escalation_contacts_from_metadata(node.metadata)
    return Team(
        node=node,
        scrum_master=_contact_name(contacts.scrum_master, names),
        manager=_contact_name(contacts.manager, names),
    )


# ---- Escalation ------------------------------------------------------------------------


def escalated(ask: Ask, matrix: EscalationMatrix, names: Mapping[str, str]) -> Ask:
    """The ask with the highest level it reached whose contact is not its owner."""
    if ask.waited_days is None:
        return ask
    for reached in reversed(reached_levels(matrix, ask.need, ask.waited_days)):
        contact = _level_contact(reached.level, ask.team, names)
        if contact and contact != ask.owner:
            return replace(
                ask,
                level=reached.number,
                escalated_to=contact,
                escalation_label=reached.level.label,
            )
    return ask


def _level_contact(
    level: EscalationLevel, team: Team | None, names: Mapping[str, str]
) -> str | None:
    if level.source is ContactSource.MEMBER:
        return names.get(level.member_id or "")
    if team is None:
        return None
    if level.source is ContactSource.TEAM_SCRUM_MASTER:
        return team.scrum_master
    return team.manager


def grouped(asks: Sequence[Ask]) -> list[tuple[str | None, list[Ask]]]:
    """Asks by owner: the owner whose asks went highest first, then the busiest."""
    by_owner: dict[str | None, list[Ask]] = {}
    for ask in asks:
        by_owner.setdefault(ask.owner, []).append(ask)
    for items in by_owner.values():
        items.sort(key=_ask_order)
    return sorted(
        by_owner.items(),
        key=lambda item: (
            item[0] is None,
            -max(ask.level for ask in item[1]),
            -len(item[1]),
            (item[0] or "").casefold(),
        ),
    )


def most_needed(asks: Sequence[Ask], limit: int = 3) -> list[Ask]:
    return sorted(asks, key=_ask_order)[:limit]


def _ask_order(ask: Ask) -> tuple[int, int, int, str]:
    return (-ask.level, -(ask.waited_days or 0), NEED_ORDER.index(ask.need), ask.text)


# ---- Blockers, dependencies, requests and signals -------------------------------------


def blocker_ask(blocker: ResolvedBlocker, scope: AskScope, day: date) -> Ask:
    reporter = scope.names.get(blocker.developer_id, "someone")
    label = issue_label(blocker.work_item_ref.id if blocker.work_item_ref else None, scope)
    description = trimmed(blocker.description)
    head = f"{label}: {description}" if label else description
    waited = max(0, (day - blocker.first_seen_on).days)
    if blocker.cross_team:
        waited_team = first_team(blocker.depends_on_pod_ids, scope.all_teams)
        waited_issue = blocker.depends_on_ref.id if blocker.depends_on_ref else None
        owner = scope.assignee_name(waited_issue) or (
            waited_team.scrum_master if waited_team else None
        )
        waits_on = ", ".join(blocker.depends_on_pod_names) or "another team"
        detail = f"waits on {waits_on}" + (f" ({waited_issue})" if waited_issue else "")
        return Ask(
            need=NeedType.FIX,
            owner=owner,
            text=head,
            waited_days=waited,
            detail=f"reported by {reporter}, {detail}",
            team=waited_team,
            issue_key=waited_issue or label,
        )
    team = first_team(blocker.pod_ids, scope.teams) or first_team(blocker.pod_ids, scope.all_teams)
    return Ask(
        need=NeedType.FIX,
        owner=(team.scrum_master if team else None) or reporter,
        text=head,
        waited_days=waited,
        detail=f"reported by {reporter}",
        team=team,
        issue_key=label,
    )


def incoming_ask(developer_id: str, blocker: ResolvedBlocker, scope: AskScope, day: date) -> Ask:
    """Someone outside the project waits on its work."""
    task_id = next(
        (
            ref.id
            for ref in (blocker.depends_on_ref, blocker.work_item_ref)
            if ref is not None and ref.id in scope.tasks
        ),
        None,
    )
    team = first_team(blocker.depends_on_pod_ids, scope.teams)
    if task_id is not None:
        waited_on = task_key(scope.tasks[task_id])
        owner = scope.assignee_name(task_id)
        team = scope.team_of_task(task_id) or team
    else:
        waited_on = team.node.name if team is not None else scope.project.name
        owner = team.scrum_master if team is not None else None
    return Ask(
        need=NeedType.FIX,
        owner=owner,
        text=f"{scope.names.get(developer_id, 'someone')} waits on {waited_on}",
        waited_days=max(0, (day - blocker.first_seen_on).days),
        team=team,
        issue_key=waited_on if task_id is not None else None,
    )


def request_ask(request: CrossPersonRequest, scope: AskScope, day: date) -> Ask | None:
    on_task = request.task_ref is not None and request.task_ref.id in scope.tasks
    involved = not scope.release_only and (
        request.requester_id in scope.members
        or (request.counterpart_id is not None and request.counterpart_id in scope.members)
    )
    if not (on_task or involved):
        return None
    requester = scope.names.get(request.requester_id, "someone")
    counterpart = scope.names.get(request.counterpart_id or "") or request.counterpart_display_name
    issue = request.task_ref.id if request.task_ref is not None else None
    on = f"{issue_label(issue, scope)}: " if issue else ""
    return Ask(
        need=_REQUEST_NEEDS.get(request.kind, NeedType.ANSWER),
        owner=counterpart,
        text=f"{on}{requester} asked for {_REQUEST_WORDS.get(request.kind, 'an answer')}",
        waited_days=max(0, (day - request.created_at.date()).days),
        team=scope.team_of_member(request.counterpart_id) or scope.team_of_task(issue),
        issue_key=issue_label(issue, scope) if issue else None,
    )


def signal_ask(finding: RiskFinding | DriftFinding, scope: AskScope, day: date) -> Ask:
    task_id = finding.entity_ref.id if finding.entity_ref.id in scope.tasks else None
    return Ask(
        need=NeedType.FIX,
        owner=scope.names.get(finding.owner_id or ""),
        text=trimmed(finding.reason).rstrip("."),
        waited_days=max(0, (day - finding.detected_at.date()).days),
        team=scope.team_of_task(task_id) or scope.team_of_member(finding.owner_id),
        issue_key=issue_label(task_id, scope) if task_id else None,
    )


# ---- Gates and questions --------------------------------------------------------------


def gate_asks(
    board: GateBoardView, scope: AskScope, matrix: EscalationMatrix, day: date
) -> list[Ask]:
    """What requirements at a gate still need: sign-offs, suggestions, missing items.

    A requirement waiting at a gate (in the stage just before the one it
    guards) gets every ask. One already past it gets only the sign-offs and
    failures someone confirmed; that it moved on without the gate is the
    report's "Most important", not one more ask per requirement.
    """
    decider = scope.names.get(matrix.decision_owner_id or "")
    asks: list[Ask] = []
    for issue in board.issues:
        task_id = scope.task_by_key(issue.key)
        assignee = scope.assignee_name(task_id)
        team = scope.team_of_task(task_id)
        for template in board.templates:
            applies = any(
                evaluation.template.template_id == template.template_id
                for evaluation in issue.evaluations
            )
            position = _gate_position(issue, template)
            if not applies or position is None:
                continue
            for kind in template.kinds:
                owner = decider if DECIDING_ROLES & set(kind.sign_off_roles) else assignee
                items = [
                    item
                    for item in issue.items
                    if item.template_id == template.template_id and item.kind == kind.key
                ]
                asks.extend(
                    _kind_asks(
                        issue,
                        template,
                        kind,
                        items,
                        owner=owner,
                        fixer=assignee,
                        team=team,
                        day=day,
                        waiting=position == "at",
                    )
                )
    return asks


def _gate_position(issue: IssueGatesView, template: GateTemplate) -> str | None:
    """'at' in the stage just before the one the gate guards, 'past' in it or later."""
    guarded = STAGE_ORDER.index(template.guards_stage)
    stage = STAGE_ORDER.index(issue.stage)
    if stage == guarded - 1:
        return "at"
    return "past" if stage >= guarded else None


def _kind_asks(
    issue: IssueGatesView,
    template: GateTemplate,
    kind: ItemKind,
    items: Sequence[GateItem],
    *,
    owner: str | None,
    fixer: str | None,
    team: Team | None,
    day: date,
    waiting: bool,
) -> list[Ask]:
    deciding = bool(DECIDING_ROLES & set(kind.sign_off_roles))
    label = kind.label.lower()
    where = (
        f"before {STAGE_LABELS[template.guards_stage].lower()}"
        if waiting
        else f"already in {STAGE_LABELS[issue.stage].lower()}"
    )
    pending = [item for item in items if item.status is ItemStatus.PENDING]
    failed = [item for item in items if item.status is ItemStatus.FAILED]
    suggested = [item for item in items if item.status is ItemStatus.SUGGESTED]
    confirmed = [
        item for item in items if item.status not in {ItemStatus.SUGGESTED, ItemStatus.DISMISSED}
    ]
    asks: list[Ask] = []
    if pending:
        asks.append(
            Ask(
                need=NeedType.DECISION if deciding else NeedType.REVIEW,
                owner=owner,
                text=f"{issue.key}: sign off {plural(len(pending), label)} ({where})",
                waited_days=_days_since((item.updated_at for item in pending), day),
                team=team,
                issue_key=issue.key,
            )
        )
    for item in failed:
        asks.append(
            Ask(
                need=NeedType.FIX,
                owner=fixer,
                text=f"{issue.key}: a failed {label}, {trimmed(item.text, 90)}",
                waited_days=_days_since([item.signed_at or item.updated_at], day),
                team=team,
                issue_key=issue.key,
            )
        )
    if not waiting:
        return asks
    if suggested:
        asks.append(
            Ask(
                need=NeedType.REVIEW,
                owner=owner,
                text=f"{issue.key}: keep or dismiss {plural(len(suggested), label)} read from Jira",
                waited_days=_days_since((item.created_at for item in suggested), day),
                team=team,
                issue_key=issue.key,
            )
        )
    elif not confirmed:
        asks.append(
            Ask(
                need=NeedType.DECISION if deciding else NeedType.FIX,
                owner=owner,
                text=f"{issue.key}: no {label} yet for {template.name.lower()} ({where})",
                waited_days=None,
                team=team,
                issue_key=issue.key,
            )
        )
    return asks


def open_question_asks(board: GateBoardView, scope: AskScope, day: date) -> list[Ask]:
    asks: list[Ask] = []
    for question in board.questions:
        if not question.confirmed or question.status not in {
            QuestionStatus.NOT_YET,
            QuestionStatus.PARTLY,
        }:
            continue
        task_id = scope.task_by_key(question.issue_key)
        asked_by = question.asked_by_name or scope.names.get(question.asked_by, "someone")
        partly = " (partly answered)" if question.status is QuestionStatus.PARTLY else ""
        asks.append(
            Ask(
                need=NeedType.ANSWER,
                owner=question.asked_to_name or scope.names.get(question.asked_to) or None,
                text=f'{question.issue_key}: "{trimmed(question.summary, 120)}"',
                detail=f"asked by {asked_by}{partly}",
                waited_days=max(0, (day - question.asked_at.date()).days),
                team=scope.team_of_member(question.asked_to) or scope.team_of_task(task_id),
                issue_key=question.issue_key,
            )
        )
    return asks


# ---- Words ------------------------------------------------------------------------------


def _days_since(moments: Iterable[datetime], day: date) -> int | None:
    days = [max(0, (day - moment.date()).days) for moment in moments]
    return max(days) if days else None


def _contact_name(contact: EscalationContact | None, names: Mapping[str, str]) -> str | None:
    if contact is None:
        return None
    if contact.member_id and contact.member_id in names:
        return names[contact.member_id]
    return contact.display_name


def first_team(ids: Iterable[str], teams: Mapping[str, Team]) -> Team | None:
    return next((teams[team_id] for team_id in ids if team_id in teams), None)


def issue_label(task_id: str | None, scope: AskScope) -> str | None:
    if task_id is None:
        return None
    task = scope.tasks.get(task_id)
    return task_key(task) if task is not None else task_id


def task_key(task: GraphNode) -> str:
    key = task.metadata.get("key")
    return key if isinstance(key, str) and key else task.id


def age_words(days: int | None) -> str:
    if not days:
        return "since today"
    return "1 day" if days == 1 else f"{days} days"


def plural(count: int, noun: str) -> str:
    if count == 1:
        return f"1 {noun}"
    if noun.endswith("criterion"):
        return f"{count} {noun[: -len('criterion')]}criteria"
    return f"{count} {noun}s"


def trimmed(text: str, limit: int = MAX_TEXT) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"
