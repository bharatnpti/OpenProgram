"""The portfolio's bulk reads: Will we make the date?, Scope control and Early warnings.

Kept beside api/dtos.py so the three reads' shapes read as one piece. Each
is one request for every project the reader reads, from kept rows, never a live
forecast per project. Who sees what is decided on the server: an executive's
bodies name no person, and a group read only as a count comes with no items.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from core.application.portfolio_delivery_service import (
    BarMark,
    PodLine,
    PortfolioDeliveryView,
    PortfolioProjectView,
    ProgramCounts,
    ReleaseLine,
)
from core.application.portfolio_scope_service import (
    FindingLine,
    PortfolioScopeView,
    RuleCount,
    ScopeProjectView,
)
from core.application.portfolio_warnings_service import (
    OwnerSays,
    PortfolioWarningsView,
    WarningGroup,
    WarningItem,
    WarningKind,
)
from core.domain.forecast import Verdict
from core.domain.portfolio import AgainstPlan
from core.domain.rollup import Rag
from core.domain.status import StatusSource


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


# ---- against the plan (shared by the delivery and scope reads) -------------------------


class AgainstPlanDto(_Frozen):
    """The scope today against the planning baseline: keys counted, never who."""

    planned_on: date
    planned: int
    added: int = Field(description="Requirements in scope now that were not in the baseline.")
    without_label: int = Field(
        description="Of those added, the ones without the baseline's change label."
    )
    without_label_percent: int | None = Field(
        description="without_label as a whole percentage of added; null with none added."
    )
    change_label: str = Field(description="The baseline's change label: tenant data.")
    removed: int
    added_days: float | None = Field(
        default=None,
        description=(
            "Working days the additions since planning moved the 85% date, summed from "
            "the kept days' breakdowns; null when no kept day took them apart."
        ),
    )

    @classmethod
    def from_domain(cls, plan: AgainstPlan) -> AgainstPlanDto:
        return cls(
            planned_on=plan.planned_on,
            planned=plan.planned,
            added=plan.added,
            without_label=plan.without_label,
            without_label_percent=plan.without_label_percent,
            change_label=plan.change_label,
            removed=plan.removed,
            added_days=round(plan.added_days, 1) if plan.added_days is not None else None,
        )


# ---- Will we make the date? ------------------------------------------------------------


class PortfolioPodDto(_Frozen):
    id: str
    name: str
    done: int
    total: int
    verdict: Verdict | None
    target: date | None
    p85: date | None

    @classmethod
    def from_view(cls, pod: PodLine) -> PortfolioPodDto:
        return cls(
            id=pod.id,
            name=pod.name,
            done=pod.done,
            total=pod.total,
            verdict=pod.verdict,
            target=pod.target,
            p85=pod.p85,
        )


class PortfolioReleaseDto(_Frozen):
    id: str
    name: str
    verdict: Verdict | None
    target: date | None
    p85: date | None

    @classmethod
    def from_view(cls, release: ReleaseLine) -> PortfolioReleaseDto:
        return cls(
            id=release.id,
            name=release.name,
            verdict=release.verdict,
            target=release.target,
            p85=release.p85,
        )


class PortfolioMarkDto(_Frozen):
    """One change on a project's bar."""

    day: date
    kind: str = Field(description="scope_added, scope_removed, reopened or committed_date_changed.")
    count: int
    working_days: float | None = Field(
        description=(
            "Working days it moved the 85% date that day, later positive; null for a "
            "committed date's move or a day not taken apart."
        )
    )
    pushed: bool = Field(description="It moved the 85% date later: a change that pushed it.")
    moved_to: date | None = Field(
        default=None, description="A committed date's move: the date it moved to."
    )

    @classmethod
    def from_view(cls, mark: BarMark) -> PortfolioMarkDto:
        return cls(
            day=mark.day,
            kind=mark.kind,
            count=mark.count,
            working_days=mark.working_days,
            pushed=mark.pushed,
            moved_to=mark.moved_to,
        )


class PortfolioProjectDto(_Frozen):
    id: str
    name: str
    program_id: str | None
    program_name: str | None
    verdict: Verdict | None = Field(description="Null while no forecast row is kept for it.")
    target: date | None = Field(description="The committed date, else Jira's release date.")
    target_source: str | None = Field(description="committed, jira_release, or null.")
    p50: date | None
    p85: date | None
    team_date: date | None = Field(description="The team's own date: the latest ETA or due date.")
    team_key: str | None
    days_late: int | None = Field(
        description="Working days the 85% date lies after the target; negative: before it."
    )
    reason: str = Field(description="Why it has its verdict, in one line that counts work items.")
    health: str = Field(
        description=(
            "Today's health in words, e.g. '1 open blocker, 2 of 8 not replied'; never a colour."
        )
    )
    health_day: date | None = Field(description="The day the rollup it is read from was kept.")
    done: int
    total: int
    pods: list[PortfolioPodDto]
    releases: list[PortfolioReleaseDto]
    marks: list[PortfolioMarkDto]
    plan: AgainstPlanDto | None
    start: date = Field(description="Where the bar starts: planning, else a month back.")
    kept_on: date | None = Field(description="The day of the forecast row read.")
    computed_at: datetime | None = Field(description="When that row was computed.")
    reconstructed: bool

    @classmethod
    def from_view(cls, view: PortfolioProjectView) -> PortfolioProjectDto:
        return cls(
            id=view.id,
            name=view.name,
            program_id=view.program_id,
            program_name=view.program_name,
            verdict=view.verdict,
            target=view.target,
            target_source=view.target_source,
            p50=view.p50,
            p85=view.p85,
            team_date=view.team_date,
            team_key=view.team_key,
            days_late=view.days_late,
            reason=view.reason,
            health=view.health,
            health_day=view.health_day,
            done=view.done,
            total=view.total,
            pods=[PortfolioPodDto.from_view(pod) for pod in view.pods],
            releases=[PortfolioReleaseDto.from_view(release) for release in view.releases],
            marks=[PortfolioMarkDto.from_view(mark) for mark in view.marks],
            plan=AgainstPlanDto.from_domain(view.plan) if view.plan is not None else None,
            start=view.start,
            kept_on=view.kept_on,
            computed_at=view.computed_at,
            reconstructed=view.reconstructed,
        )


class VerdictCountsDto(_Frozen):
    on_track: int = 0
    at_risk: int = 0
    off_track: int = 0
    done: int = 0
    no_date: int = 0
    not_enough_data: int = 0
    none: int = Field(default=0, description="Projects with no forecast row kept yet.")

    @classmethod
    def from_counts(cls, counts: Mapping[str, int]) -> VerdictCountsDto:
        return cls.model_validate({key: int(value) for key, value in counts.items()})


class PortfolioProgramDto(_Frozen):
    id: str
    name: str
    counts: VerdictCountsDto

    @classmethod
    def from_view(cls, view: ProgramCounts) -> PortfolioProgramDto:
        return cls(id=view.id, name=view.name, counts=VerdictCountsDto.from_counts(view.counts))


class PortfolioDeliveryResponse(_Frozen):
    """Every project the reader reads, worst first, from the kept forecast rows.

    ``counts`` cover every project the search matches (the verdict filter aside), so
    each tile is a filter; ``total`` is how many match the filters, of which
    ``projects`` is the page from ``offset``.
    """

    as_of: date
    computed_at: datetime | None = Field(description="The newest kept row's time: 'as of HH:MM'.")
    counts: VerdictCountsDto
    total: int
    offset: int
    limit: int
    projects: list[PortfolioProjectDto]
    programs: list[PortfolioProgramDto]

    @classmethod
    def from_view(cls, view: PortfolioDeliveryView) -> PortfolioDeliveryResponse:
        return cls(
            as_of=view.as_of,
            computed_at=view.computed_at,
            counts=VerdictCountsDto.from_counts(view.counts),
            total=view.total,
            offset=view.offset,
            limit=view.limit,
            projects=[PortfolioProjectDto.from_view(project) for project in view.projects],
            programs=[PortfolioProgramDto.from_view(program) for program in view.programs],
        )


# ---- Scope control ---------------------------------------------------------------------


class ScopeProjectDto(_Frozen):
    id: str
    name: str
    program_id: str | None
    program_name: str | None
    plan: AgainstPlanDto | None = Field(
        description="Null without a baseline, or for a reader of no baselines."
    )
    added_keys: list[str]
    without_label_keys: list[str]
    scope_day: date | None = Field(description="The stored snapshot the scope is read from.")
    hygiene_open: int

    @classmethod
    def from_view(cls, view: ScopeProjectView) -> ScopeProjectDto:
        return cls(
            id=view.id,
            name=view.name,
            program_id=view.program_id,
            program_name=view.program_name,
            plan=AgainstPlanDto.from_domain(view.plan) if view.plan is not None else None,
            added_keys=list(view.added_keys),
            without_label_keys=list(view.without_label_keys),
            scope_day=view.scope_day,
            hygiene_open=view.hygiene_open,
        )


class ScopeRuleCountDto(_Frozen):
    id: str
    name: str
    asks: bool = Field(description="The rule asks each owner in the day report.")
    open: int

    @classmethod
    def from_view(cls, view: RuleCount) -> ScopeRuleCountDto:
        return cls(id=view.id, name=view.name, asks=view.asks, open=view.open)


class ScopeFindingDto(_Frozen):
    id: str
    issue_key: str
    title: str
    rule_id: str
    rule_name: str
    project_id: str
    project_name: str
    owner_name: str | None = Field(description="Null for a reader who sees no person.")
    owner_team: str | None
    found_on: date
    open_days: int
    asks: bool = Field(description="Its rule asks the owner in the day report: nothing to send.")
    held: bool

    @classmethod
    def from_view(cls, view: FindingLine) -> ScopeFindingDto:
        return cls(
            id=view.id,
            issue_key=view.issue_key,
            title=view.title,
            rule_id=view.rule_id,
            rule_name=view.rule_name,
            project_id=view.project_id,
            project_name=view.project_name,
            owner_name=view.owner_name,
            owner_team=view.owner_team,
            found_on=view.found_on,
            open_days=view.open_days,
            asks=view.asks,
            held=view.held,
        )


class PortfolioScopeResponse(_Frozen):
    """Each readable project against its plan, and the open Jira hygiene findings.

    ``counts_only``: the reader reads each rule's count, never an issue or an owner.
    """

    as_of: date
    reads_baselines: bool
    hygiene_enabled: bool
    counts_only: bool
    projects: list[ScopeProjectDto]
    rules: list[ScopeRuleCountDto]
    findings: list[ScopeFindingDto]
    findings_total: int

    @classmethod
    def from_view(cls, view: PortfolioScopeView) -> PortfolioScopeResponse:
        return cls(
            as_of=view.as_of,
            reads_baselines=view.reads_baselines,
            hygiene_enabled=view.hygiene_enabled,
            counts_only=view.counts_only,
            projects=[ScopeProjectDto.from_view(project) for project in view.projects],
            rules=[ScopeRuleCountDto.from_view(rule) for rule in view.rules],
            findings=[ScopeFindingDto.from_view(finding) for finding in view.findings],
            findings_total=view.findings_total,
        )


# ---- Early warnings --------------------------------------------------------------------


class OwnerSaysDto(_Frozen):
    """What the item's owner last said in their own status, and where it came from."""

    text: str = Field(description="Their latest status in their words, member ids said as names.")
    source: StatusSource = Field(
        description="confirmed: they said it; partial, inferred (from Jira and Git) or stale."
    )
    on: date = Field(description="The day of that status.")

    @classmethod
    def from_view(cls, says: OwnerSays) -> OwnerSaysDto:
        return cls(text=says.text, source=says.source, on=says.on)


class WarningPodDto(_Frozen):
    id: str
    name: str


class WarningItemDto(_Frozen):
    id: str
    kind: str = Field(description="The drift kind, the risk rule, or 'blocker'.")
    key: str
    line: str
    age_days: int
    since: date | None
    age_known: bool = Field(
        description="False: the age only counts from the first recording ('seen since')."
    )
    owner: str | None = Field(
        description="Who acts on it, by name; null where the reader sees the team instead."
    )
    owner_team: str | None
    owner_is_you: bool
    project_id: str
    project_name: str
    severity: Rag
    evidence_url: str | None = None
    watermelon: bool = False
    pods: list[WarningPodDto] = Field(
        description="The project's pods it sits in: the issue's, else its owner's, by name.",
    )
    owner_says: OwnerSaysDto | None = Field(
        description=(
            "What the named owner last said in their own status; null wherever the "
            "reader sees the team instead of the owner."
        ),
    )

    @classmethod
    def from_view(cls, item: WarningItem) -> WarningItemDto:
        return cls(
            id=item.id,
            kind=item.kind,
            key=item.key,
            line=item.line,
            age_days=item.age_days,
            since=item.since,
            age_known=item.age_known,
            owner=item.owner,
            owner_team=item.owner_team,
            owner_is_you=item.owner_is_you,
            project_id=item.project_id,
            project_name=item.project_name,
            severity=item.severity,
            evidence_url=item.evidence_url,
            watermelon=item.watermelon,
            pods=[WarningPodDto(id=pod.id, name=pod.name) for pod in item.pods],
            owner_says=(
                OwnerSaysDto.from_view(item.owner_says) if item.owner_says is not None else None
            ),
        )


class WarningGroupDto(_Frozen):
    kind: WarningKind
    title: str
    count: int
    items: list[WarningItemDto] = Field(description="Oldest first.")
    counts_only: bool = Field(description="The reader reads this group's count, not its items.")

    @classmethod
    def from_view(cls, group: WarningGroup) -> WarningGroupDto:
        return cls(
            kind=group.kind,
            title=group.title,
            count=group.count,
            items=[WarningItemDto.from_view(item) for item in group.items],
            counts_only=group.counts_only,
        )


class PortfolioWarningsResponse(_Frozen):
    """Early warnings grouped by kind, each with its count; findings attach to work items."""

    as_of: date
    seen_since: date | None = Field(
        description="The first day drift sightings were recorded: an age from it is a lower bound."
    )
    groups: list[WarningGroupDto]
    total: int
    projects: int

    @classmethod
    def from_view(cls, view: PortfolioWarningsView) -> PortfolioWarningsResponse:
        return cls(
            as_of=view.as_of,
            seen_since=view.seen_since,
            groups=[WarningGroupDto.from_view(group) for group in view.groups],
            total=view.total,
            projects=view.projects,
        )
