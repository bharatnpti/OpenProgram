"""Planning baselines and the forecast's reconstruction, as the API reads and writes them."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from core.application.baseline_service import BaselinesView, BaselineView
from core.application.forecast_service import MAX_RECONSTRUCT_DAYS, RECONSTRUCT_DAYS
from core.domain.baseline import (
    DEFAULT_CHANGE_LABEL,
    DEFAULT_GRACE_WORKING_DAYS,
    MAX_BASELINE_NOTE_LENGTH,
    MAX_CHANGE_LABEL_LENGTH,
    MAX_GRACE_WORKING_DAYS,
    BaselineKind,
    BaselineScopeKind,
)

#: The most keys a read lists per difference; the counts are always whole.
MAX_LISTED_KEYS = 50


class PlanningCompleteRequest(BaseModel):
    """Mark planning complete for the project, or for one of its releases."""

    model_config = ConfigDict(frozen=True)

    release_id: str | None = Field(
        default=None, description="One release of the project; null for the whole project."
    )
    planned_on: date | None = Field(
        default=None,
        description=(
            "The day planning was complete; null for today. An earlier day freezes that "
            "day's stored snapshot."
        ),
    )
    change_label: str | None = Field(
        default=None,
        max_length=MAX_CHANGE_LABEL_LENGTH,
        description=(
            "The Jira label of a change agreed after planning; null for the default "
            f'"{DEFAULT_CHANGE_LABEL}".'
        ),
    )
    grace_working_days: int = Field(
        default=DEFAULT_GRACE_WORKING_DAYS,
        ge=0,
        le=MAX_GRACE_WORKING_DAYS,
        description="Working days after planning in which an added requirement counts as planned.",
    )
    note: str = Field(default="", max_length=MAX_BASELINE_NOTE_LENGTH)


class ScopeSinceBaselineResponse(BaseModel):
    """Today's scope against one baseline: keys only (at most 50 listed), never who."""

    model_config = ConfigDict(frozen=True)

    added: int
    added_with_label: int
    removed: int
    added_keys: list[str]
    added_with_label_keys: list[str]
    removed_keys: list[str]


class PlanningBaselineResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    baseline_id: str
    #: 1 for the scope's first baseline.
    number: int
    active: bool
    scope_kind: BaselineScopeKind
    scope_id: str
    project_id: str
    kind: BaselineKind
    planned_on: date
    reconstructed: bool = Field(
        description="True when no snapshot of the day was kept and the requirements were rebuilt."
    )
    snapshot_day: date | None = Field(
        description="The snapshot the requirements were read from, when it is not planned_on."
    )
    requirements: int
    #: Of those, the ones not yet in production when planning was complete.
    open: int
    points: float | None = Field(
        description="The story points frozen, when every requirement carried them."
    )
    change_label: str
    grace_working_days: int
    note: str
    set_by: str
    set_by_name: str
    set_at: datetime
    superseded_at: datetime | None
    since: ScopeSinceBaselineResponse

    @classmethod
    def from_view(cls, view: BaselineView) -> PlanningBaselineResponse:
        baseline = view.baseline
        since = view.since
        return cls(
            baseline_id=baseline.baseline_id,
            number=view.number,
            active=view.active,
            scope_kind=baseline.scope_kind,
            scope_id=baseline.scope_id,
            project_id=baseline.project_id,
            kind=baseline.kind,
            planned_on=baseline.planned_on,
            reconstructed=baseline.reconstructed,
            snapshot_day=baseline.snapshot_day,
            requirements=len(baseline.items),
            open=len(baseline.open_keys),
            points=baseline.points,
            change_label=baseline.change_label,
            grace_working_days=baseline.grace_working_days,
            note=baseline.note,
            set_by=baseline.set_by,
            set_by_name=view.set_by_name,
            set_at=baseline.set_at,
            superseded_at=baseline.superseded_at if not view.active else None,
            since=ScopeSinceBaselineResponse(
                added=len(since.added),
                added_with_label=len(since.added_with_label),
                removed=len(since.removed),
                added_keys=list(since.added[:MAX_LISTED_KEYS]),
                added_with_label_keys=list(since.added_with_label[:MAX_LISTED_KEYS]),
                removed_keys=list(since.removed[:MAX_LISTED_KEYS]),
            ),
        )


class PlanningBaselinesResponse(BaseModel):
    """A project's or a release's baselines on the day read, newest first."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    release_id: str | None
    scope_kind: BaselineScopeKind
    scope_id: str
    name: str
    as_of: date
    in_scope: int = Field(description="Requirements in scope on the day read.")
    active: PlanningBaselineResponse | None
    baselines: list[PlanningBaselineResponse]
    default_change_label: str = DEFAULT_CHANGE_LABEL
    default_grace_working_days: int = DEFAULT_GRACE_WORKING_DAYS

    @classmethod
    def from_view(cls, view: BaselinesView) -> PlanningBaselinesResponse:
        items = [PlanningBaselineResponse.from_view(item) for item in view.baselines]
        return cls(
            project_id=view.project_id,
            release_id=view.scope_id if view.scope_kind is BaselineScopeKind.RELEASE else None,
            scope_kind=view.scope_kind,
            scope_id=view.scope_id,
            name=view.name,
            as_of=view.as_of,
            in_scope=view.in_scope,
            active=next((item for item in items if item.active), None),
            baselines=items,
        )


class ForecastReconstructionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    days: int = Field(
        default=RECONSTRUCT_DAYS,
        ge=1,
        le=MAX_RECONSTRUCT_DAYS,
        description="How many days before today to rebuild from the stored snapshots.",
    )
