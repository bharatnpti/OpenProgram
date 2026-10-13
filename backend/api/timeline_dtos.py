"""The timeline per project and pod, as the API reads it (DESIGN §5.4).

One row per scope: its span from planning to the committed date, the committed
date's earlier values, progress, the 50% to 85% band, the team's date, and the
impact markers per day and kind. ``access`` says how much of a row the reader
gets: ``panel`` is the whole row, ``dates`` the span, ticks and band only (no
progress, scope or markers). A row the reader may not see is not listed.
Markers are events on work items, never counted or sorted by person; ``added_by``
is a name on one item's row, left out for readers who may not see it.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from core.application.delivery_scope import NodeAccess
from core.application.impact_service import MarkerView
from core.application.timeline_service import TimelineRow, TimelineView
from core.domain.delivery import DeliveryStage
from core.domain.forecast import CommitmentScopeKind, Verdict
from core.domain.impact import ScopeGroup


class TimelineAxisResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: date
    end: date
    #: The day read: today, or the past day asked for.
    today: date


class TimelineBaselineResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    day: date
    reconstructed: bool


class TimelineTickResponse(BaseModel):
    """One value the committed date took, and the day it was set."""

    model_config = ConfigDict(frozen=True)

    #: The day it was set (named before ``date``, which would shadow the type below it).
    on: date
    date: date


class TimelineCommittedResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: date | None
    #: Every committed date in order, the current one last.
    history: list[TimelineTickResponse]


class TimelineForecastResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    p50: date
    p85: date
    #: The day the kept forecast is from: the day read, or the last kept before it.
    on: date | None


class TimelineTeamResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: date | None
    key: str | None
    #: Open requirements with neither an ETA nor a due date.
    undated: int


class TimelineProgressResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    done: float
    total: float
    #: "requirements", or "story points" when every requirement carries them.
    unit: str


class TimelineScopeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    at_baseline: int
    now: int


class TimelineMarkerItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    stage: DeliveryStage | None
    group: ScopeGroup | None
    #: Scope added: whether the item carries the change label.
    change_label: bool | None
    #: Who brought it in (D14); left out for executives and developers.
    added_by: str | None
    #: A blocker: what it waits on.
    waits_on: str | None
    #: Added without the change label: whether a Jira hygiene finding is open on it.
    hygiene: bool | None


class TimelineMarkerResponse(BaseModel):
    """One kind of event on one day, on a row's impact lane."""

    model_config = ConfigDict(frozen=True)

    day: date
    #: scope_added, bug, scope_removed, reopened, late, blocker_opened, blocker_closed or
    #: committed_date_changed.
    kind: str
    count: int
    items: list[TimelineMarkerItemResponse]
    #: Scope added: whether these items carry the change label.
    change_label: bool | None
    #: Working days this event moved the date, on the 85% date: scope events only (scope
    #: added, a bug counted as a requirement, scope removed, reopened), the day's step
    #: split among its markers by item count (D88). None for the other kinds (D12) and
    #: for a day whose move is not known.
    working_days: float | None
    p50_days: float | None
    p85_days: float | None
    #: True: read from the day's kept breakdown; False: a scope event whose day kept none
    #: that can be read ("not known"); None: a kind that never gets days.
    taken_apart: bool | None
    #: The day's kept row was a reconstruction.
    reconstructed: bool

    @classmethod
    def from_view(cls, marker: MarkerView) -> TimelineMarkerResponse:
        return cls(
            day=marker.day,
            kind=marker.kind,
            count=marker.count,
            items=[
                TimelineMarkerItemResponse(
                    key=item.key,
                    title=item.title,
                    stage=item.stage,
                    group=item.group,
                    change_label=item.change_label,
                    added_by=item.added_by,
                    waits_on=item.waits_on,
                    hygiene=item.hygiene,
                )
                for item in marker.items
            ],
            change_label=marker.change_label,
            working_days=marker.working_days,
            p50_days=marker.p50_days,
            p85_days=marker.p85_days,
            taken_apart=marker.taken_apart,
            reconstructed=marker.reconstructed,
        )


class TimelineRowResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: "<scope_kind>:<scope_id>", as Overall's ?row= names it.
    row: str
    scope_kind: CommitmentScopeKind
    scope_id: str
    project_id: str
    project_name: str
    name: str
    access: NodeAccess
    verdict: Verdict | None
    baseline: TimelineBaselineResponse | None
    #: Where the row's span starts: the baseline's day, else its first kept forecast day.
    start: date
    committed: TimelineCommittedResponse
    jira_release_date: date | None
    forecast: TimelineForecastResponse | None
    #: Why there is no band, in words.
    forecast_reason: str | None
    team: TimelineTeamResponse
    progress: TimelineProgressResponse | None
    scope: TimelineScopeResponse | None
    markers: list[TimelineMarkerResponse]
    #: A pod's row: its project's committed date, drawn as a reference tick.
    reference_date: date | None
    #: Set date or Change date, as the date routes would allow it.
    can_set_date: bool

    @classmethod
    def from_view(cls, row: TimelineRow) -> TimelineRowResponse:
        return cls(
            row=row.row_id,
            scope_kind=row.scope.kind,
            scope_id=row.scope.id,
            project_id=row.scope.project_id,
            project_name=row.project_name,
            name=row.name,
            access=row.level,
            verdict=row.verdict,
            baseline=(
                TimelineBaselineResponse(
                    day=row.baseline_day, reconstructed=row.baseline_reconstructed
                )
                if row.baseline_day is not None
                else None
            ),
            start=row.start,
            committed=TimelineCommittedResponse(
                date=row.committed,
                history=[
                    TimelineTickResponse(date=tick.target, on=tick.on)
                    for tick in row.committed_history
                ],
            ),
            jira_release_date=row.jira_release_date,
            forecast=(
                TimelineForecastResponse(p50=row.p50, p85=row.p85, on=row.forecast_on)
                if row.p50 is not None and row.p85 is not None
                else None
            ),
            forecast_reason=row.forecast_reason,
            team=TimelineTeamResponse(
                date=row.team_date, key=row.team_key, undated=row.team_undated
            ),
            progress=(
                TimelineProgressResponse(done=row.done, total=row.total, unit=row.unit)
                if row.done is not None and row.total is not None and row.unit is not None
                else None
            ),
            scope=(
                TimelineScopeResponse(at_baseline=row.at_baseline, now=row.in_scope)
                if row.at_baseline is not None and row.in_scope is not None
                else None
            ),
            markers=[TimelineMarkerResponse.from_view(marker) for marker in row.markers],
            reference_date=row.reference,
            can_set_date=row.can_set_date,
        )


class TimelineTextResponse(BaseModel):
    """The spoken summary of one row, worded by the server."""

    model_config = ConfigDict(frozen=True)

    row: str
    scope_id: str
    project_id: str
    sentence: str


class TimelineResponse(BaseModel):
    """A project's or a pod's timeline: the axis, the rows, and each row in words."""

    model_config = ConfigDict(frozen=True)

    axis: TimelineAxisResponse
    rows: list[TimelineRowResponse]
    text: list[TimelineTextResponse]
    #: The project's change label (tenant data), for the legend's words.
    change_label: str

    @classmethod
    def from_view(cls, view: TimelineView) -> TimelineResponse:
        return cls(
            axis=TimelineAxisResponse(
                start=view.axis.start, end=view.axis.end, today=view.axis.today
            ),
            rows=[TimelineRowResponse.from_view(row) for row in view.rows],
            text=[
                TimelineTextResponse(
                    row=row.row_id,
                    scope_id=row.scope.id,
                    project_id=row.scope.project_id,
                    sentence=row.sentence,
                )
                for row in view.rows
            ],
            change_label=view.change_label,
        )
