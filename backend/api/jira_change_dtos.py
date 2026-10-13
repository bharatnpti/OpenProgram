"""Shapes of "Jira changes waiting for your yes": the person's own list, and their answer."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from core.application.jira_changes_service import (
    JiraChangeAnswerView,
    JiraChangeKind,
    JiraChangesView,
    JiraChangeView,
)
from core.application.writeback_service import AnswerGate, AnswerGateState, AnswerOutcome


class JiraChangeGateDto(BaseModel):
    """What a yes would meet now, read from the same gates the write passes.

    ``yes_allowed`` is False for every state but ``open`` and ``no_change``: the
    console offers no yes a gate would refuse. A no is always allowed for a
    proposal or a suggestion: it never writes.
    """

    model_config = ConfigDict(frozen=True)

    state: AnswerGateState
    yes_allowed: bool
    #: The tracker's own status name for the issue now, when it was read.
    tracker_status: str | None = None
    #: The open merge requests that hold a move to done back (``repo !id``).
    merge_requests: list[str] = []

    @classmethod
    def from_domain(cls, gate: AnswerGate) -> JiraChangeGateDto:
        return cls(
            state=gate.state,
            yes_allowed=gate.yes_allowed,
            tracker_status=gate.tracker_status,
            merge_requests=list(gate.merge_requests),
        )


class JiraChangeDto(BaseModel):
    """One change waiting for the person's yes.

    ``kind``: ``proposal`` (from a check-in), ``suggestion`` (a merged merge
    request, the issue still open) or ``due_date`` (no answer here: change it
    in the tracker). ``holds_checkin``: the day's check-in waits for this answer.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    kind: JiraChangeKind
    issue_key: str
    issue_title: str | None = None
    #: Why it is asked, in plain words; never anyone's reply.
    why: str
    asked_on: date | None = None
    #: The state a yes moves the issue to ("Done"); none for a due date.
    target_label: str | None = None
    gate: JiraChangeGateDto | None = None
    #: Whether the console takes a yes or a no for it at all (not for a due date).
    answerable: bool
    holds_checkin: bool = False
    due_on: date | None = None
    eta_on: date | None = None

    @classmethod
    def from_view(cls, view: JiraChangeView) -> JiraChangeDto:
        return cls(
            id=view.id,
            kind=view.kind,
            issue_key=view.issue_key,
            issue_title=view.issue_title,
            why=view.why,
            asked_on=view.asked_on,
            target_label=view.target_label,
            gate=JiraChangeGateDto.from_domain(view.gate) if view.gate is not None else None,
            answerable=view.kind != "due_date",
            holds_checkin=view.holds_checkin,
            due_on=view.due_on,
            eta_on=view.eta_on,
        )


class JiraChangesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: date
    #: How the tracker is named: "Jira", or "the issue tracker".
    tracker: str
    items: list[JiraChangeDto]

    @classmethod
    def from_view(cls, view: JiraChangesView) -> JiraChangesResponse:
        return cls(
            as_of=view.as_of,
            tracker=view.tracker,
            items=[JiraChangeDto.from_view(item) for item in view.items],
        )


class JiraChangeAnswerRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: Literal["yes", "no"]


class JiraChangeAnswerResponse(BaseModel):
    """What the answer did. ``already_answered``: the chat (or another tab) answered first."""

    model_config = ConfigDict(frozen=True)

    item_id: str
    issue_key: str
    outcome: AnswerOutcome
    detail: str
    #: The check-in held for this answer is now recorded.
    checkin_finished: bool = False
    #: One line went to the person's chat ("Answered in the console: ...").
    line_sent: bool = False

    @classmethod
    def from_view(cls, view: JiraChangeAnswerView) -> JiraChangeAnswerResponse:
        return cls(
            item_id=view.item_id,
            issue_key=view.issue_key,
            outcome=view.outcome,
            detail=view.detail,
            checkin_finished=view.checkin_finished,
            line_sent=view.line_sent,
        )
