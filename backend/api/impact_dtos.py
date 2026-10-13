"""Why the date moved, as the API reads it: one scope's forecast move over a period, taken apart.

The shapes follow the timeline's (DESIGN §5.4) where they overlap: markers are
``{day, kind, count, items: [{key, title, stage}], change_label}``, and the row
is ``<scope_kind>:<scope_id>``. Items are work items; ``added_by`` is a name on
one item's row, left out for readers who may not see it.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from core.application.impact_service import (
    CommittedChangeView,
    ImpactDetail,
    ImpactDriverView,
    ImpactEnd,
    ImpactItemView,
    ImpactView,
    MarkerView,
    PeriodKind,
    TeamDateView,
)
from core.domain.delivery import DeliveryStage
from core.domain.forecast import CommitmentScopeKind, Verdict
from core.domain.impact import Measure, ScopeGroup


class ImpactEndResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    day: date
    p50: date | None
    p85: date | None
    committed: date | None
    verdict: Verdict | None

    @classmethod
    def from_view(cls, end: ImpactEnd | None) -> ImpactEndResponse | None:
        if end is None:
            return None
        return cls(
            day=end.day, p50=end.p50, p85=end.p85, committed=end.committed, verdict=end.verdict
        )


class ImpactItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    type: str | None
    #: The item's share of its driver, in working days, on the measure read.
    working_days: float
    day: date | None
    group: ScopeGroup | None
    change_label: bool | None
    added_by: str | None

    @classmethod
    def from_view(cls, item: ImpactItemView) -> ImpactItemResponse:
        return cls(
            key=item.key,
            title=item.title,
            type=item.type,
            working_days=item.working_days,
            day=item.day,
            group=item.group,
            change_label=item.change_label,
            added_by=item.added_by,
        )


class ImpactDriverResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: One of the three causes a leader reads: scope_added, scope_removed or team_speed
    #: (core/domain/impact_words.py); group is None and each item carries its own.
    kind: str
    group: ScopeGroup | None
    working_days: float
    p50: float
    p85: float
    items: list[ImpactItemResponse]
    more: int
    note: str

    @classmethod
    def from_view(cls, driver: ImpactDriverView) -> ImpactDriverResponse:
        return cls(
            kind=driver.kind,
            group=driver.group,
            working_days=driver.working_days,
            p50=driver.p50,
            p85=driver.p85,
            items=[ImpactItemResponse.from_view(item) for item in driver.items],
            more=driver.more,
            note=driver.note,
        )


class ScopeChangesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    added: list[ImpactItemResponse]
    removed: list[ImpactItemResponse]
    reopened: list[ImpactItemResponse]
    added_count: int
    removed_count: int
    reopened_count: int
    finished_count: int


class LateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    stage: DeliveryStage
    date: date
    #: due, eta or baseline_due.
    date_kind: str
    working_days_late: int


class EvidenceResponse(BaseModel):
    """What explains pace without a share of its own."""

    model_config = ConfigDict(frozen=True)

    late: list[LateResponse]
    more_late: int
    undated: list[str]


class CommittedChangeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_date: date | None
    to_date: date | None
    on: date
    by: str
    note: str
    after_added: list[str]

    @classmethod
    def from_view(cls, change: CommittedChangeView) -> CommittedChangeResponse:
        return cls(
            from_date=change.from_date,
            to_date=change.to_date,
            on=change.on,
            by=change.by,
            note=change.note,
            after_added=list(change.after_added),
        )


class TeamMoveResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    day: date
    from_date: date | None
    from_key: str | None
    to_date: date | None
    to_key: str | None
    cause: str
    words: str


class TeamDateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_date: date | None
    from_key: str | None
    to_date: date | None
    to_key: str | None
    moves: list[TeamMoveResponse]

    @classmethod
    def from_view(cls, view: TeamDateView | None) -> TeamDateResponse | None:
        if view is None:
            return None
        return cls(
            from_date=view.from_date,
            from_key=view.from_key,
            to_date=view.to_date,
            to_key=view.to_key,
            moves=[
                TeamMoveResponse(
                    day=move.day,
                    from_date=move.from_date,
                    from_key=move.from_key,
                    to_date=move.to_date,
                    to_key=move.to_key,
                    cause=move.cause,
                    words=move.words,
                )
                for move in view.moves
            ],
        )


class MarkerItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    stage: DeliveryStage | None
    group: ScopeGroup | None
    added_by: str | None


class MarkerResponse(BaseModel):
    """One kind of event on one day, on the timeline's impact lane."""

    model_config = ConfigDict(frozen=True)

    day: date
    #: scope_added, bug, scope_removed, reopened, late, blocker_opened, blocker_closed or
    #: committed_date_changed.
    kind: str
    count: int
    items: list[MarkerItemResponse]
    change_label: bool | None
    #: Working days this event moved the date, on the measure read: scope events only
    #: (scope added, a bug counted as a requirement, scope removed, reopened), the day's
    #: step split among its markers by item count (D88). None for the other kinds (D12)
    #: and for a day whose move is not known.
    working_days: float | None
    p50_days: float | None
    p85_days: float | None
    #: True: read from the day's kept breakdown; False: a scope event whose day kept none
    #: that can be read ("not known"); None: a kind that never gets days.
    taken_apart: bool | None
    #: The day's kept row was a reconstruction.
    reconstructed: bool

    @classmethod
    def from_view(cls, marker: MarkerView) -> MarkerResponse:
        return cls(
            day=marker.day,
            kind=marker.kind,
            count=marker.count,
            items=[
                MarkerItemResponse(
                    key=item.key,
                    title=item.title,
                    stage=item.stage,
                    group=item.group,
                    added_by=item.added_by,
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


class DeliveryImpactResponse(BaseModel):
    """Why one scope's forecast moved over a period: then, now, and each driver between."""

    model_config = ConfigDict(frozen=True)

    scope_kind: CommitmentScopeKind
    scope_id: str
    project_id: str
    name: str
    project_name: str
    period: PeriodKind
    period_words: str
    measure: Measure
    #: full, or dates: how far it moved and by which kind of driver, with no items.
    detail: ImpactDetail
    change_label: str
    baseline_day: date | None
    start: ImpactEndResponse | None
    end: ImpactEndResponse | None
    moved_working_days: float | None
    unit: str | None
    summary: str
    drivers: list[ImpactDriverResponse]
    scope_changes: ScopeChangesResponse
    evidence: EvidenceResponse | None
    committed_changes: list[CommittedChangeResponse]
    team_date: TeamDateResponse | None
    markers: list[MarkerResponse]
    link: str
    reconstructed_days: int
    not_known: list[str]

    @classmethod
    def from_view(cls, view: ImpactView) -> DeliveryImpactResponse:
        changes = view.scope_changes
        evidence = view.evidence
        return cls(
            scope_kind=view.scope.kind,
            scope_id=view.scope.id,
            project_id=view.scope.project_id,
            name=view.name,
            project_name=view.project_name,
            period=view.period,
            period_words=view.period_words,
            measure=view.measure,
            detail=view.detail,
            change_label=view.change_label,
            baseline_day=view.baseline_day,
            start=ImpactEndResponse.from_view(view.start),
            end=ImpactEndResponse.from_view(view.end),
            moved_working_days=view.moved_working_days,
            unit=view.unit,
            summary=view.summary,
            drivers=[ImpactDriverResponse.from_view(driver) for driver in view.drivers],
            scope_changes=ScopeChangesResponse(
                added=[ImpactItemResponse.from_view(item) for item in changes.added],
                removed=[ImpactItemResponse.from_view(item) for item in changes.removed],
                reopened=[ImpactItemResponse.from_view(item) for item in changes.reopened],
                added_count=changes.added_count,
                removed_count=changes.removed_count,
                reopened_count=changes.reopened_count,
                finished_count=changes.finished_count,
            ),
            evidence=(
                EvidenceResponse(
                    late=[
                        LateResponse(
                            key=item.key,
                            title=item.title,
                            stage=item.stage,
                            date=item.date,
                            date_kind=item.date_kind,
                            working_days_late=item.working_days_late,
                        )
                        for item in evidence.late
                    ],
                    more_late=evidence.more_late,
                    undated=list(evidence.undated),
                )
                if evidence is not None
                else None
            ),
            committed_changes=[
                CommittedChangeResponse.from_view(change) for change in view.committed_changes
            ],
            team_date=TeamDateResponse.from_view(view.team_date),
            markers=[MarkerResponse.from_view(marker) for marker in view.markers],
            link=view.link,
            reconstructed_days=view.reconstructed_days,
            not_known=list(view.not_known),
        )
