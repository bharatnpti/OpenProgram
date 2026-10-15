"""A request's trail and the requests board as counts.

The trail says only what happened or is due. A step worked out from the
escalation matrix says so (``computed``), and whether a day report carried it
(``report``). Names are null for a reader who reads none (an executive).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from core.application.request_trail_service import (
    DmState,
    ReportState,
    RequestCountsView,
    RequestTrailView,
    TrailState,
    TrailStep,
    TrailStepKind,
)
from core.domain.cross_person import CrossPersonRequestStatus


class RequestTrailStepDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: TrailStepKind = Field(
        description="unmatched (nobody matched the name; the requester was asked whom they "
        "meant), asked, acknowledged, named (in the day report, by the escalation matrix), "
        "nobody_above (due to go up, nobody named above), resolved, cleared (the merge "
        "landed) or closed (a copy of a newer ask)."
    )
    state: TrailState = Field(
        description="done: it happened. due: the matrix makes it due and no day report has "
        "carried it (see report)."
    )
    at: datetime | None = Field(default=None, description="When, for a step of a moment.")
    on: date | None = Field(
        default=None,
        description="The day, for a step of a day: the report that named it, or the day the "
        "matrix reaches it.",
    )
    who: str | None = Field(
        default=None,
        description="The person at this step (the person asked, or whom the report names); "
        "null when not known or not read.",
    )
    role: str | None = Field(default=None, description="The matrix level's name for `named`.")
    by: str | None = Field(
        default=None, description="Who made it happen; null for OpenProgram itself."
    )
    dm: DmState | None = Field(
        default=None,
        description="On `asked`: the DM to the person asked was sent, is being retried, was "
        "not delivered, or none was sent.",
    )
    dm_attempts: int = 0
    dm_at: datetime | None = Field(default=None, description="When the DM went out, if kept.")
    eta: str | None = Field(
        default=None, description="On `acknowledged`: when their reply said, in its words."
    )
    project_name: str | None = Field(
        default=None, description="The project whose day report carries a named step."
    )
    report: ReportState | None = Field(
        default=None,
        description="On `named`: sent (a day report carried it), not_sent (none went out "
        "while it stood there), no_report (the project has no day report) or upcoming.",
    )
    computed: bool = Field(
        default=False, description="Worked out from the escalation matrix, not a recorded event."
    )

    @classmethod
    def from_view(cls, step: TrailStep) -> RequestTrailStepDto:
        return cls(
            kind=step.kind,
            state=step.state,
            at=step.at,
            on=step.on,
            who=step.who,
            role=step.role,
            by=step.by,
            dm=step.dm,
            dm_attempts=step.dm_attempts,
            dm_at=step.dm_at,
            eta=step.eta,
            project_name=step.project_name,
            report=step.report,
            computed=step.computed,
        )


class RequestTrailResponse(BaseModel):
    """``GET /cross-person-requests/{id}/trail``: what happened to one ask, and what is due."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    status: CrossPersonRequestStatus
    as_of: date
    names_hidden: bool = Field(description="No one is named: the caller reads no names.")
    steps: list[RequestTrailStepDto]

    @classmethod
    def from_view(cls, view: RequestTrailView) -> RequestTrailResponse:
        return cls(
            request_id=view.request_id,
            status=view.status,
            as_of=view.as_of,
            names_hidden=view.names_hidden,
            steps=[RequestTrailStepDto.from_view(step) for step in view.steps],
        )


class RequestCountsResponse(BaseModel):
    """``GET /cross-person-requests/counts``: the requests board as counts, no one named."""

    model_config = ConfigDict(frozen=True)

    as_of: date
    open: int = Field(description="Asked and not yet acknowledged.")
    acknowledged: int
    needs_resolution: int = Field(description="The name matched nobody yet.")
    waiting_over_a_week: int = Field(description="Open or acknowledged for 7 days or more.")
    not_delivered: int = Field(description="Open, and the DM to the person asked failed.")
    resolved_recently: int = Field(description="Resolved in the last `resolved_window_days`.")
    resolved_window_days: int

    @classmethod
    def from_view(cls, view: RequestCountsView) -> RequestCountsResponse:
        return cls(
            as_of=view.as_of,
            open=view.open,
            acknowledged=view.acknowledged,
            needs_resolution=view.needs_resolution,
            waiting_over_a_week=view.waiting_over_a_week,
            not_delivered=view.not_delivered,
            resolved_recently=view.resolved_recently,
            resolved_window_days=view.resolved_window_days,
        )
