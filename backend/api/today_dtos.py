"""Shapes of the person's own conversation, a project's "who must act", and Today's counts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from core.application.today_service import (
    ConversationDayView,
    CountsScope,
    ProjectAsksView,
    ProjectAskView,
    TodayCountsView,
)
from core.domain.escalation_matrix import NeedType

NOT_KEPT_NOTE = "Not kept after {days} days."


class ConversationTurnDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: ``agent`` for what Ora sent, ``user`` for what the person wrote.
    role: Literal["agent", "user"]
    text: str
    at: datetime


class ConversationResponse(BaseModel):
    """The person's own conversation of one day, read-only, oldest first.

    ``kept`` False: past the retention window, so nothing is shown and
    ``note`` says why.
    """

    model_config = ConfigDict(frozen=True)

    day: date
    kept: bool
    retention_days: int
    note: str | None = None
    turns: list[ConversationTurnDto]

    @classmethod
    def from_view(cls, view: ConversationDayView) -> ConversationResponse:
        return cls(
            day=view.day,
            kept=view.kept,
            retention_days=view.retention_days,
            note=None if view.kept else NOT_KEPT_NOTE.format(days=view.retention_days),
            turns=[
                ConversationTurnDto(role=turn.role, text=turn.text, at=turn.at)
                for turn in view.turns
            ],
        )


class ProjectAskDto(BaseModel):
    """One ask of who must act: what, who, how long, why. Ordered by the item, never by person.

    For an executive ``who`` is the owning team and ``detail`` and
    ``escalated_to`` are empty: no one's name reaches them.
    """

    model_config = ConfigDict(frozen=True)

    need: NeedType
    need_label: str
    what: str
    who: str | None = None
    team: str | None = None
    waited_days: int | None = None
    detail: str | None = None
    issue_key: str | None = None
    #: The matrix level it reached: 1 is its owner.
    level: int
    escalated_to: str | None = None
    escalation_label: str | None = None

    @classmethod
    def from_view(cls, view: ProjectAskView) -> ProjectAskDto:
        return cls(
            need=view.need,
            need_label=view.need_label,
            what=view.what,
            who=view.who,
            team=view.team,
            waited_days=view.waited_days,
            detail=view.detail,
            issue_key=view.issue_key,
            level=view.level,
            escalated_to=view.escalated_to,
            escalation_label=view.escalation_label,
        )


class ProjectAsksResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    project_name: str
    as_of: date
    #: True for an executive: each ask names its team, not a person.
    names_hidden: bool
    asks: list[ProjectAskDto]

    @classmethod
    def from_view(cls, view: ProjectAsksView) -> ProjectAsksResponse:
        return cls(
            project_id=view.project_id,
            project_name=view.project_name,
            as_of=view.as_of,
            names_hidden=view.names_hidden,
            asks=[ProjectAskDto.from_view(ask) for ask in view.asks],
        )


class RepliesCountDto(BaseModel):
    """The day's replies: ``replied`` (confirmed or partial) of ``people``, and
    ``inferred``, filled from the tracker and Git, never green."""

    model_config = ConfigDict(frozen=True)

    people: int
    replied: int
    inferred: int
    waiting: int


class TodayCountsResponse(BaseModel):
    """Today's counts over what the caller reads: the portfolio, or their own pods and projects."""

    model_config = ConfigDict(frozen=True)

    as_of: date
    scope: CountsScope
    replies: RepliesCountDto
    open_warnings: int
    project_count: int

    @classmethod
    def from_view(cls, view: TodayCountsView) -> TodayCountsResponse:
        return cls(
            as_of=view.as_of,
            scope=view.scope,
            replies=RepliesCountDto(
                people=view.replies.people,
                replied=view.replies.replied,
                inferred=view.replies.inferred,
                waiting=view.replies.waiting,
            ),
            open_warnings=view.open_warnings,
            project_count=len(view.project_ids),
        )
