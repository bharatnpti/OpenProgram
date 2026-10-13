"""The silence ladder on a pod's check-ins, and a project's check-in counts (lane R).

A pod's check-ins carry, for the roles that read the ladder (a scrum master
for their own pods, a manager, an admin), when each person was asked and
replied and the rungs that went out; nothing counts or ranks anyone. A
project's coverage is counts only, with no one named.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.application.persona_views import CheckinCoverageView, ProjectCheckinCoverageView
from core.domain.checkin_rungs import RungView
from core.domain.escalation import EscalationTarget

RungTarget = Literal["developer", "scrum_master", "manager"]
_TARGETS: dict[EscalationTarget, RungTarget] = {
    EscalationTarget.DEVELOPER: "developer",
    EscalationTarget.SCRUM_MASTER: "scrum_master",
    EscalationTarget.MANAGER: "manager",
}


class CheckinRungDto(BaseModel):
    """One rung of the day's silence ladder that went out: whom it was for, and when."""

    model_config = ConfigDict(frozen=True)

    number: int = Field(description="Its place on the ladder: 1 is the reminder to the person.")
    target: RungTarget | None = Field(
        description="developer (a reminder to the person), scrum_master or manager; null when "
        "the rung was not recorded and today's ladder has no rung of that number."
    )
    sent_at: datetime
    recorded: bool = Field(
        description="Whom it was for was recorded when it went out; false: read from "
        "today's ladder, for a rung sent before rungs were recorded."
    )

    @classmethod
    def from_view(cls, rung: RungView) -> CheckinRungDto:
        return cls(
            number=rung.number,
            target=_TARGETS[rung.target] if rung.target is not None else None,
            sent_at=rung.sent_at,
            recorded=rung.recorded,
        )


class ProjectCheckinCoverageDto(BaseModel):
    """How the day was captured across the project's people: counts, no names."""

    model_config = ConfigDict(frozen=True)

    replied: int = Field(description="Gave the day's status themselves (confirmed or partial).")
    inferred: int = Field(description="Filled from what moved in the tracker and Git.")
    no_data: int = Field(description="Closed stale or unknown: nothing said, nothing moved.")
    waiting: int = Field(description="No status for the day yet.")

    @classmethod
    def from_view(cls, view: CheckinCoverageView) -> ProjectCheckinCoverageDto:
        return cls(
            replied=view.replied,
            inferred=view.inferred,
            no_data=view.no_data,
            waiting=view.waiting,
        )


class ProjectCheckinCoverageResponse(BaseModel):
    """``GET /projects/{id}/checkin-coverage``: the day's check-ins over the project, counted."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    as_of: date
    people: int = Field(description="Everyone on the project's pods, each once.")
    coverage: ProjectCheckinCoverageDto

    @classmethod
    def from_view(cls, view: ProjectCheckinCoverageView) -> ProjectCheckinCoverageResponse:
        return cls(
            project_id=view.project_id,
            as_of=view.as_of,
            people=view.people,
            coverage=ProjectCheckinCoverageDto.from_view(view.coverage),
        )
