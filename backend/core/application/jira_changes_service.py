"""A person's Jira changes waiting for their yes, answered from the console (D89).

Three kinds of item, each the person's own and nobody else's:

- **proposal**: a check-in's ``proposed`` write-back row still pending (an
  ``always_ask`` person said "CHK-4 is done" and was asked in the DM). Its yes
  or no goes through ``WriteBackService.resolve_proposal``, the one resolve
  path the chat's answer takes too, under the correlation's lock; then
  ``finish_after_console_consent`` finishes a check-in held for the answer and
  sends one line to the DM.
- **suggestion**: a merged merge request whose issue, assigned to the person,
  is still open (``merged_issue_open`` drift on their own issue). Its yes is a
  console move under ``console_moves``; its no records a declined row, so it is
  not offered again that day.
- **due_date**: the person's own ETA for an issue is later than its due date in
  the tracker. OpenProgram never writes a due date, so it carries no answer:
  "Change it in Jira".

Each proposal and suggestion carries the gate a yes would meet now
(``AnswerGate``), so the console offers a yes only where the write would pass.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Protocol

import structlog

from core.application.delivery_scope import MemberScope
from core.application.persona_views import FocusView
from core.application.status_summaries import day_label
from core.application.writeback_service import (
    CHECKIN_CLOSED_SOURCE,
    CONSOLE_CONSENT_SOURCE,
    NO_CHANGE_SOURCE,
    AnswerGate,
    AnswerOutcome,
    ConsentDecision,
    ProposalAnswer,
    WriteBackAnswerNotFound,
    WriteBackService,
    target_state_label,
)
from core.domain.errors import OpenProgramError
from core.domain.jira_writes import JiraWriteKind
from core.domain.risk import DriftFinding, DriftFindingKind
from core.domain.status import CheckIn
from core.domain.writeback import WriteBackAudit, WriteBackStatus, WriteBackTarget
from core.ports.repositories import GraphRepository, IdentityLinkRepository, StatusRepository

JiraChangeKind = Literal["proposal", "suggestion", "due_date"]

PROPOSAL_PREFIX = "proposal:"
SUGGESTION_PREFIX = "suggestion:"
DUE_DATE_PREFIX = "due:"
#: How far back a check-in's proposals are looked for: one check-in day, with margin.
PROPOSAL_LOOKBACK = timedelta(days=3)
DUE_DATE_ANSWER = "Change the due date in Jira: OpenProgram never writes a due date."
_DONE_TRACKER_STATUSES = frozenset({"done", "closed", "resolved"})


class JiraChangeNotFound(OpenProgramError):
    """No such change waiting for this person (404)."""


class JiraChangeNotAnswerable(OpenProgramError):
    """A change the console cannot make, such as a due date (422)."""


@dataclass(frozen=True, kw_only=True)
class JiraChangeView:
    """One change waiting for the person's yes, with the gate a yes would meet now.

    ``gate`` is None for a due date, which has no answer here. ``holds_checkin``:
    the day's check-in is held open for this answer.
    """

    id: str
    kind: JiraChangeKind
    issue_key: str
    issue_title: str | None
    why: str
    asked_on: date | None
    target_state: str | None = None
    target_label: str | None = None
    gate: AnswerGate | None = None
    holds_checkin: bool = False
    due_on: date | None = None
    eta_on: date | None = None


@dataclass(frozen=True, kw_only=True)
class JiraChangesView:
    as_of: date
    tracker: str
    items: tuple[JiraChangeView, ...]


@dataclass(frozen=True, kw_only=True)
class JiraChangeAnswerView:
    """What the person's answer did, in plain words, and what it finished."""

    item_id: str
    issue_key: str
    outcome: AnswerOutcome
    detail: str
    checkin_finished: bool = False
    line_sent: bool = False


class _Finish(Protocol):
    @property
    def finished(self) -> bool: ...

    @property
    def line(self) -> str | None: ...


class ConsoleConsentFinisher(Protocol):
    """``StatusCollector.finish_after_console_consent``, as the answer needs it."""

    async def finish_after_console_consent(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        answers: Sequence[ProposalAnswer],
        answered_at: datetime | None = None,
    ) -> _Finish: ...


ProjectDrift = Callable[[str, str, date], Awaitable[list[DriftFinding]]]
ScopeOf = Callable[[str, str], Awaitable[MemberScope]]
FocusOf = Callable[[str, str, date], Awaitable[FocusView]]


class JiraChangesService:
    def __init__(
        self,
        *,
        write_back: WriteBackService,
        status_repository: StatusRepository,
        graph_repository: GraphRepository,
        project_drift: ProjectDrift,
        scope_of: ScopeOf,
        focus_of: FocusOf,
        finisher: Callable[[], ConsoleConsentFinisher],
        identity_link_repository: IdentityLinkRepository | None = None,
        tracker_name: str = "Jira",
        clock: Callable[[], datetime] | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._write_back = write_back
        self._status = status_repository
        self._graph = graph_repository
        self._drift = project_drift
        self._scope_of = scope_of
        self._focus_of = focus_of
        self._finisher = finisher
        self._links = identity_link_repository
        self._tracker = tracker_name
        # The clock times an answer and bounds the check-ins read; ``today`` is the day
        # the list and a suggestion's answer are for. The API passes the tenant's
        # today, the day /me/focus and the drift read are for in the console.
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._today = today or (lambda: self._clock().date())

    async def changes(self, tenant_id: str, developer_id: str) -> JiraChangesView:
        """Every change waiting for this person's yes today: proposals, suggestions, due dates."""
        today = self._today()
        proposals = await self._proposals(tenant_id, developer_id, today)
        proposed_keys = {item.issue_key for item in proposals}
        suggestions = [
            item
            for item in await self._suggestions(tenant_id, developer_id, today)
            if item.issue_key not in proposed_keys
        ]
        due_dates = await self._due_dates(tenant_id, developer_id, today)
        return JiraChangesView(
            as_of=today,
            tracker=self._tracker,
            items=(*proposals, *suggestions, *due_dates),
        )

    async def answer(
        self, tenant_id: str, developer_id: str, item_id: str, decision: ConsentDecision
    ) -> JiraChangeAnswerView:
        """Answer one change yes or no, as the person themself."""
        if item_id.startswith(DUE_DATE_PREFIX):
            raise JiraChangeNotAnswerable(DUE_DATE_ANSWER)
        if item_id.startswith(PROPOSAL_PREFIX):
            return await self._answer_proposal(
                tenant_id, developer_id, item_id, item_id.removeprefix(PROPOSAL_PREFIX), decision
            )
        if item_id.startswith(SUGGESTION_PREFIX):
            return await self._answer_suggestion(
                tenant_id, developer_id, item_id, item_id.removeprefix(SUGGESTION_PREFIX), decision
            )
        raise JiraChangeNotFound(f"No change {item_id!r} is waiting for you.")

    # ---- proposals ---------------------------------------------------------------------

    async def _proposals(
        self, tenant_id: str, developer_id: str, today: date
    ) -> list[JiraChangeView]:
        items: list[JiraChangeView] = []
        for correlation_id in await self._recent_correlations(tenant_id, developer_id):
            pending = [
                proposal
                for proposal in await self._write_back.list_pending_proposals(
                    tenant_id, correlation_id
                )
                if proposal.developer_id == developer_id
            ]
            if not pending:
                continue
            checkin = await self._status.checkin_by_correlation(tenant_id, correlation_id)
            held = checkin is not None and await self._held(checkin)
            for proposal in pending:
                items.append(
                    JiraChangeView(
                        id=f"{PROPOSAL_PREFIX}{proposal.id}",
                        kind="proposal",
                        issue_key=proposal.issue_key,
                        issue_title=await self._title(tenant_id, proposal.issue_key),
                        why=proposal_why(proposal, checkin, today),
                        asked_on=_asked_on(proposal, checkin),
                        target_state=proposal.target_state,
                        target_label=target_state_label(proposal.target_state),
                        gate=await self._write_back.answer_gate(
                            tenant_id,
                            developer_id,
                            proposal.issue_key,
                            proposal.target_state,
                            JiraWriteKind.CHECKIN_UPDATES,
                        ),
                        holds_checkin=held,
                    )
                )
        return items

    async def _recent_correlations(self, tenant_id: str, developer_id: str) -> list[str]:
        """The person's own check-ins of the last days, newest first, by every chat id of theirs."""
        now = self._clock()
        asked_from = datetime.combine(now.date() - PROPOSAL_LOOKBACK, time.min, tzinfo=UTC)
        found: dict[str, datetime] = {}
        for chat_ref in await self._chat_refs(tenant_id, developer_id):
            for correlation in await self._status.recent_checkin_correlations_for_user(
                tenant_id, chat_ref, asked_from=asked_from, asked_to=now
            ):
                # Members may share a chat id: only this person's own check-ins count.
                if correlation.developer_id == developer_id:
                    found.setdefault(correlation.correlation_id, correlation.asked_at)
        return sorted(found, key=lambda key: found[key], reverse=True)

    async def _chat_refs(self, tenant_id: str, developer_id: str) -> list[str]:
        refs = [developer_id]
        node = await self._graph.get_node(tenant_id, developer_id)
        chat_id = node.metadata.get("chat_external_id") if node is not None else None
        if isinstance(chat_id, str) and chat_id:
            refs.append(chat_id)
        if self._links is not None:
            link = await self._links.get_identity_link(tenant_id, developer_id)
            if link is not None and link.chat_user_id:
                refs.append(link.chat_user_id)
        return list(dict.fromkeys(refs))

    async def _held(self, checkin: CheckIn) -> bool:
        """Whether the check-in is open with its signals held: waiting for this answer."""
        if checkin.replied_at is not None or checkin.signals is None:
            return False
        correlation = await self._status.checkin_correlation_by_id(
            checkin.tenant_id, checkin.correlation_id
        )
        return correlation is None or correlation.consumed_at is None

    async def _answer_proposal(
        self,
        tenant_id: str,
        developer_id: str,
        item_id: str,
        audit_id: str,
        decision: ConsentDecision,
    ) -> JiraChangeAnswerView:
        proposal = await self._write_back.get_audit(tenant_id, audit_id)
        if proposal is None or proposal.developer_id != developer_id:
            raise JiraChangeNotFound(f"No change {item_id!r} is waiting for you.")
        checkin = await self._status.checkin_by_correlation(tenant_id, proposal.correlation_id)
        try:
            answer = await self._write_back.resolve_proposal(
                tenant_id=tenant_id,
                developer_id=developer_id,
                audit_id=audit_id,
                decision=decision,
                source=CONSOLE_CONSENT_SOURCE,
                claims=(
                    checkin.signals.issue_updates
                    if checkin is not None and checkin.signals is not None
                    else ()
                ),
                reported_on=checkin.checkin_date if checkin is not None else None,
            )
        except WriteBackAnswerNotFound as exc:
            raise JiraChangeNotFound(f"No change {item_id!r} is waiting for you.") from exc
        finished = False
        line_sent = False
        if answer.outcome not in ("already_answered", "off"):
            # The answer is recorded; finishing the chat side is best-effort.
            try:
                finish = await self._finisher().finish_after_console_consent(
                    tenant_id=tenant_id,
                    developer_id=developer_id,
                    correlation_id=proposal.correlation_id,
                    answers=(answer,),
                    answered_at=self._clock(),
                )
                finished, line_sent = finish.finished, finish.line is not None
            except Exception:  # pragma: no cover - defensive; the answer is recorded
                _log_warning(
                    "console_consent_finish_failed",
                    tenant_id=tenant_id,
                    correlation_id=proposal.correlation_id,
                )
        return JiraChangeAnswerView(
            item_id=item_id,
            issue_key=answer.issue_key,
            outcome=answer.outcome,
            detail=answer_detail(answer, tracker=self._tracker),
            checkin_finished=finished,
            line_sent=line_sent,
        )

    # ---- suggestions -------------------------------------------------------------------

    async def _suggestions(
        self, tenant_id: str, developer_id: str, today: date
    ) -> list[JiraChangeView]:
        """Merged merge requests whose issue, the person's own, is still open in the tracker."""
        scope = await self._scope_of(tenant_id, developer_id)
        found: dict[str, DriftFinding] = {}
        for project_id in sorted(scope.projects):
            for finding in await self._drift(tenant_id, project_id, today):
                if (
                    finding.kind is DriftFindingKind.MERGED_ISSUE_OPEN
                    and finding.owner_id == developer_id
                ):
                    found.setdefault(finding.entity_ref.id, finding)
        items: list[JiraChangeView] = []
        for issue_key, finding in sorted(found.items()):
            if await self._write_back.suggestion_answered(tenant_id, issue_key, today) is not None:
                continue
            gate = await self._write_back.answer_gate(
                tenant_id,
                developer_id,
                issue_key,
                WriteBackTarget.DONE.value,
                JiraWriteKind.CONSOLE_MOVES,
            )
            if gate.state == "no_change":
                continue  # the tracker already shows it done: nothing to ask
            items.append(
                JiraChangeView(
                    id=f"{SUGGESTION_PREFIX}{issue_key}",
                    kind="suggestion",
                    issue_key=issue_key,
                    issue_title=await self._title(tenant_id, issue_key),
                    why=finding.reason,
                    asked_on=today,
                    target_state=WriteBackTarget.DONE.value,
                    target_label=target_state_label(WriteBackTarget.DONE.value),
                    gate=gate,
                )
            )
        return items

    async def _answer_suggestion(
        self,
        tenant_id: str,
        developer_id: str,
        item_id: str,
        issue_key: str,
        decision: ConsentDecision,
    ) -> JiraChangeAnswerView:
        today = self._today()
        offered = {
            item.issue_key for item in await self._suggestions(tenant_id, developer_id, today)
        }
        if issue_key not in offered:
            answered = await self._write_back.suggestion_answered(tenant_id, issue_key, today)
            if answered is None or answered.developer_id != developer_id:
                raise JiraChangeNotFound(f"No change {item_id!r} is waiting for you.")
        answer = await self._write_back.answer_suggestion(
            tenant_id=tenant_id,
            developer_id=developer_id,
            issue_key=issue_key,
            day=today,
            decision=decision,
            tracker_name=self._tracker,
        )
        return JiraChangeAnswerView(
            item_id=item_id,
            issue_key=issue_key,
            outcome=answer.outcome,
            detail=answer_detail(answer, tracker=self._tracker, suggestion=True),
        )

    # ---- due dates ---------------------------------------------------------------------

    async def _due_dates(
        self, tenant_id: str, developer_id: str, today: date
    ) -> list[JiraChangeView]:
        """The person's own open issues whose ETA they gave is later than the due date."""
        try:
            focus = await self._focus_of(tenant_id, developer_id, today)
        except OpenProgramError:
            return []
        items: list[JiraChangeView] = []
        for task in focus.tasks:
            if task.my_eta is None or task.deadline is None or task.my_eta <= task.deadline:
                continue
            if (task.tracker_status or "").strip().lower() in _DONE_TRACKER_STATUSES:
                continue
            items.append(
                JiraChangeView(
                    id=f"{DUE_DATE_PREFIX}{task.id}",
                    kind="due_date",
                    issue_key=task.id,
                    issue_title=task.name if task.name != task.id else None,
                    why=(
                        f"You gave {day_label(task.my_eta)} as its ETA; its due date in "
                        f"{self._tracker} is {day_label(task.deadline)}."
                    ),
                    asked_on=None,
                    due_on=task.deadline,
                    eta_on=task.my_eta,
                )
            )
        return items

    async def _title(self, tenant_id: str, issue_key: str) -> str | None:
        node = await self._graph.get_node(tenant_id, issue_key)
        if node is None or not node.name or node.name == issue_key:
            return None
        return node.name


def _asked_on(proposal: WriteBackAudit, checkin: CheckIn | None) -> date:
    if checkin is not None and checkin.checkin_date is not None:
        return checkin.checkin_date
    return proposal.created_at.date()


_REPORTED: dict[str, str] = {
    WriteBackTarget.TODO.value: "not started",
    WriteBackTarget.IN_PROGRESS.value: "in progress",
    WriteBackTarget.IN_REVIEW.value: "in review",
    WriteBackTarget.BLOCKED.value: "blocked",
    WriteBackTarget.DONE.value: "done",
}


def proposal_why(proposal: WriteBackAudit, checkin: CheckIn | None, today: date) -> str:
    """Why the change is asked: "You said it is done in today's check-in."

    The canonical state only, never the person's words.
    """
    said = _REPORTED.get(proposal.target_state)
    day = _asked_on(proposal, checkin)
    when = "today's check-in" if day == today else f"your check-in of {day_label(day)}"
    if said is None:
        return f"You asked for it in {when}."
    return f"You said it is {said} in {when}."


def answer_detail(
    answer: ProposalAnswer, *, tracker: str = "Jira", suggestion: bool = False
) -> str:
    """What the answer did, in one plain sentence for the console."""
    key = answer.issue_key
    label = target_state_label(answer.target_state)
    if answer.outcome == "applied":
        return f"Moved {key} to {label} in {tracker}."
    if answer.outcome == "declined":
        return f"Left {key} as it is in {tracker}."
    if answer.outcome == "no_change":
        return f"{key} is already {label} in {tracker}. Nothing was changed."
    if answer.outcome == "held_open_mr":
        held = ", ".join(answer.merge_requests) or "its merge request"
        verb = "are" if len(answer.merge_requests) > 1 else "is"
        return f"Not moved: {held} {verb} still open."
    if answer.outcome == "not_owner":
        return f"Not moved: {key} is not assigned to you in {tracker}."
    if answer.outcome == "failed":
        return f"Not moved: {tracker} could not be updated. Try again later."
    if answer.outcome == "off":
        where = "the console" if suggestion else "check-ins"
        return f"Not moved: updating {tracker} from {where} is off."
    return _already_answered_detail(answer.row, key, tracker)


def _already_answered_detail(row: WriteBackAudit | None, key: str, tracker: str) -> str:
    if row is None:
        return f"Already answered. Nothing was changed in {tracker}."
    if row.status is WriteBackStatus.APPLIED:
        label = target_state_label(row.after_state or row.target_state)
        return f"Already answered: {key} was moved to {label} in {tracker}."
    if row.status is WriteBackStatus.EXPIRED and row.source == CHECKIN_CLOSED_SOURCE:
        return f"The question closed with the check-in. Nothing was changed in {tracker}."
    if row.status is WriteBackStatus.EXPIRED and row.source == NO_CHANGE_SOURCE:
        return f"Already answered: {key} was already where you said in {tracker}."
    return f"Already answered: {key} was left as it is in {tracker}."


def _log_warning(event: str, **fields: str) -> None:
    structlog.get_logger(__name__).warning(event, **fields)
