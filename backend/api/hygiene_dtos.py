"""Jira hygiene request and response bodies.

Kept beside api/dtos.py so the feature's shapes read as one piece. Every ``can``
is worked out on the server for the viewer, so the console draws only the
buttons that will work. An executive's board carries the rules' counts and no
findings: counts, never who.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from core.application.hygiene_service import (
    BoardView,
    ConfigView,
    FindingView,
    PreviewScope,
    RuleRow,
    RunSummary,
)
from core.application.person_names import UNKNOWN_PERSON
from core.domain.delivery import DeliveryStage
from core.domain.hygiene import (
    FIX_PLACEHOLDERS,
    MAX_FIX,
    MAX_LABELS,
    MAX_MATCHER_VALUE,
    MAX_NAME,
    MAX_SELECT,
    MAX_WORKING_DAYS,
    Condition,
    ConditionKind,
    HygieneAction,
    HygieneRule,
    HygieneSettings,
    HygieneState,
    IssueField,
    Owner,
    OwnerKind,
    Resolution,
    RuleNeed,
    RuleScope,
    RuleSeverity,
    Select,
    StageFilter,
)
from core.domain.release_readiness import Matcher, MatcherKind, RunStatus, ScopeKind, Strength

# ---- configuration --------------------------------------------------------------------


class HygieneMatcherDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: MatcherKind
    value: str = Field(min_length=1, max_length=MAX_MATCHER_VALUE)


class HygieneSelectDto(BaseModel):
    """Which of a scope's issues the rule looks at: every matcher must match."""

    model_config = ConfigDict(frozen=True)

    matchers: list[HygieneMatcherDto] = Field(default_factory=list, max_length=MAX_SELECT)
    stage_filter: StageFilter = StageFilter.ANY
    stage: DeliveryStage | None = None
    every_type: bool = Field(
        default=False,
        description="False looks only at the requirements the stage mapping counts.",
    )


class HygieneConditionDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ConditionKind
    labels: list[str] = Field(
        default_factory=list,
        max_length=MAX_LABELS,
        description=(
            "added_after_baseline_without_label: the change labels (empty: the planning "
            "baseline's and the tenant's); missing_label: any of these."
        ),
    )
    field: IssueField | None = None
    gate_kind: str = Field(default="", max_length=40, description="A gate item kind's key.")
    stage: DeliveryStage | None = None
    working_days: int = Field(default=0, ge=0, le=MAX_WORKING_DAYS)
    points_over: float = Field(default=0, ge=0)


class HygieneOwnerDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: OwnerKind
    profile: str = Field(
        default="", max_length=32, description="A profile id (GET /profiles), for kind profile."
    )


class HygieneRuleDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(
        default="", max_length=80, description="Empty creates one; an example's id adds it."
    )
    version: int = Field(default=0, description="Read only: +1 on every save.")
    name: str = Field(min_length=1, max_length=MAX_NAME)
    scope: RuleScope = RuleScope.PROJECT
    select: HygieneSelectDto = Field(default_factory=HygieneSelectDto)
    condition: HygieneConditionDto
    severity: RuleSeverity = Field(
        default=RuleSeverity.LIST_ONLY,
        description="list_only shows it on Overall and counts it; ask also asks its owner.",
    )
    need: RuleNeed = RuleNeed.FIX
    owner: HygieneOwnerDto = Field(
        default_factory=lambda: HygieneOwnerDto(kind=OwnerKind.POD_SCRUM_MASTER)
    )
    blocking_before: DeliveryStage | None = None
    suggested_fix: str = Field(default="", max_length=MAX_FIX)
    enabled: bool = True

    @classmethod
    def from_domain(cls, rule: HygieneRule) -> HygieneRuleDto:
        select, condition = rule.select, rule.condition
        return cls(
            rule_id=rule.rule_id,
            version=rule.version,
            name=rule.name,
            scope=rule.scope,
            select=HygieneSelectDto(
                matchers=[
                    HygieneMatcherDto(kind=item.kind, value=item.value) for item in select.matchers
                ],
                stage_filter=select.stage_filter,
                stage=select.stage,
                every_type=select.every_type,
            ),
            condition=HygieneConditionDto(
                kind=condition.kind,
                labels=list(condition.labels),
                field=condition.field,
                gate_kind=condition.gate_kind,
                stage=condition.stage,
                working_days=condition.working_days,
                points_over=condition.points_over,
            ),
            severity=rule.severity,
            need=rule.need,
            owner=HygieneOwnerDto(kind=rule.owner.kind, profile=rule.owner.profile),
            blocking_before=rule.blocking_before,
            suggested_fix=rule.suggested_fix,
            enabled=rule.enabled,
        )

    def to_domain(self, tenant_id: str) -> HygieneRule:
        return HygieneRule(
            tenant_id=tenant_id,
            rule_id=self.rule_id.strip(),
            name=self.name,
            scope=self.scope,
            select=Select(
                matchers=tuple(
                    Matcher(kind=item.kind, value=item.value, strength=Strength.EVIDENCE)
                    for item in self.select.matchers
                ),
                stage_filter=self.select.stage_filter,
                stage=self.select.stage,
                every_type=self.select.every_type,
            ),
            condition=Condition(
                kind=self.condition.kind,
                labels=tuple(self.condition.labels),
                field=self.condition.field,
                gate_kind=self.condition.gate_kind,
                stage=self.condition.stage,
                working_days=self.condition.working_days,
                points_over=self.condition.points_over,
            ),
            severity=self.severity,
            need=self.need,
            owner=Owner(kind=self.owner.kind, profile=self.owner.profile),
            blocking_before=self.blocking_before,
            suggested_fix=self.suggested_fix,
            enabled=self.enabled,
        )


class HygieneSettingsDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(description="The scan checks every hour; off, a tick does nothing.")
    change_labels: list[str] = Field(
        default_factory=list,
        max_length=MAX_LABELS,
        description="The tenant's labels for scope agreed after planning; empty keeps CR.",
    )
    stale_working_days: int = Field(default=5, ge=1, le=MAX_WORKING_DAYS)
    updated_at: datetime | None = None
    updated_by: str | None = None

    @classmethod
    def from_domain(cls, settings: HygieneSettings) -> HygieneSettingsDto:
        return cls(
            enabled=settings.enabled,
            change_labels=list(settings.change_labels),
            stale_working_days=settings.stale_working_days,
            updated_at=settings.updated_at,
            updated_by=settings.updated_by,
        )

    def to_domain(self, tenant_id: str) -> HygieneSettings:
        return HygieneSettings(
            tenant_id=tenant_id,
            enabled=self.enabled,
            change_labels=tuple(self.change_labels),
            stale_working_days=self.stale_working_days,
        )


class HygieneGateKindDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str


class HygieneConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    settings: HygieneSettingsDto
    #: True while the tenant has saved no settings.
    is_default: bool
    rules: list[HygieneRuleDto]
    #: Generic examples not yet added; none is on until an admin adds it.
    examples: list[HygieneRuleDto]
    #: The gate item kinds a rule may look for, from the gates in force.
    gate_kinds: list[HygieneGateKindDto]
    #: The words a suggested fix fills in.
    placeholders: list[str]

    @classmethod
    def from_view(cls, view: ConfigView) -> HygieneConfigResponse:
        return cls(
            settings=HygieneSettingsDto.from_domain(view.settings),
            is_default=view.is_default,
            rules=[HygieneRuleDto.from_domain(item) for item in view.rules],
            examples=[HygieneRuleDto.from_domain(item) for item in view.examples],
            gate_kinds=[HygieneGateKindDto(key=key, label=label) for key, label in view.gate_kinds],
            placeholders=list(FIX_PLACEHOLDERS),
        )


class HygienePreviewRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1, max_length=200)
    rule: HygieneRuleDto


class HygieneScopeDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ScopeKind
    id: str
    name: str


class HygienePreviewIssueDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    what: str
    owner_name: str | None
    suggested_fix: str


class HygienePreviewScopeDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: HygieneScopeDto
    looked_at: int
    found: int
    issues: list[HygienePreviewIssueDto]
    #: The rule needs a planning baseline and the scope has none (Overall, Mark planning complete).
    needs_baseline: bool

    @classmethod
    def from_domain(cls, row: PreviewScope) -> HygienePreviewScopeDto:
        return cls(
            scope=HygieneScopeDto(kind=row.scope.kind, id=row.scope.id, name=row.scope_name),
            looked_at=row.looked_at,
            found=row.found,
            issues=[
                HygienePreviewIssueDto(
                    key=item.key,
                    title=item.title,
                    what=item.what,
                    owner_name=item.owner_name,
                    suggested_fix=item.suggested_fix,
                )
                for item in row.issues
            ],
            needs_baseline=row.needs_baseline,
        )


class HygienePreviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    scopes: list[HygienePreviewScopeDto]


# ---- the board ------------------------------------------------------------------------


class HygieneScanDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
    last_run_at: datetime | None
    last_run_status: RunStatus | None
    #: The Jira sync the findings read.
    data_as_of: datetime | None
    #: The Jira sync is failing or behind: nothing closes, and new asks wait.
    stale: bool


class HygieneRuleRowDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    name: str
    #: The rule stated first, in words.
    stated: str
    severity: RuleSeverity
    need: RuleNeed
    condition: ConditionKind
    blocking: bool
    open: int
    new_today: int
    #: Days the oldest open finding has been open.
    oldest_days: int | None
    #: Opened on stale Jira data: kept out of the day report until fresh.
    held: int
    #: Waived or set aside by a person.
    decided: int

    @classmethod
    def from_domain(cls, row: RuleRow) -> HygieneRuleRowDto:
        rule = row.rule
        return cls(
            rule_id=rule.rule_id,
            name=rule.name,
            stated=row.stated,
            severity=rule.severity,
            need=rule.need,
            condition=rule.condition.kind,
            blocking=rule.blocking,
            open=row.open,
            new_today=row.new_today,
            oldest_days=row.oldest_days,
            held=row.held,
            decided=row.decided,
        )


class HygieneOwnerViewDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str | None
    #: How the owner was found: assignee, reporter, profile, pod_scrum_master, decision_owner.
    via: OwnerKind | None
    profile_label: str | None


class HygieneActionsDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    waive: bool
    not_applicable: bool
    recheck: bool
    reopen: bool


class HygieneDecisionDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    by_name: str | None
    at: datetime
    reason: str


class HygieneFindingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_id: str
    rule_id: str
    issue_key: str
    title: str
    scope: HygieneScopeDto
    stage: DeliveryStage | None
    #: What is wrong, in fixed words: never Jira text.
    what: str
    owner: HygieneOwnerViewDto
    suggested_fix: str
    state: HygieneState
    held: bool
    first_seen_at: datetime
    open_days: int
    resolution: Resolution | None
    decision: HygieneDecisionDto | None
    can: HygieneActionsDto

    @classmethod
    def from_view(cls, view: FindingView) -> HygieneFindingResponse:
        finding = view.finding
        via = finding.owner_via
        return cls(
            finding_id=finding.finding_id,
            rule_id=finding.rule_id,
            issue_key=finding.issue_key,
            title=view.title,
            scope=HygieneScopeDto(
                kind=finding.scope.kind, id=finding.scope.id, name=view.scope_name
            ),
            stage=finding.stage,
            what=finding.what,
            owner=HygieneOwnerViewDto(
                name=view.owner_name,
                via=OwnerKind(via) if via in {kind.value for kind in OwnerKind} else None,
                profile_label=view.owner_profile_label,
            ),
            suggested_fix=view.suggested_fix,
            state=finding.state,
            held=finding.held,
            first_seen_at=finding.first_seen_at,
            open_days=view.open_days,
            resolution=finding.resolution,
            decision=(
                HygieneDecisionDto(
                    by_name=view.decided_by_name,
                    at=finding.decision.at,
                    reason=finding.decision.reason,
                )
                if finding.decision
                else None
            ),
            can=HygieneActionsDto(
                waive=view.actions.waive,
                not_applicable=view.actions.not_applicable,
                recheck=view.actions.recheck,
                reopen=view.actions.reopen,
            ),
        )


class HygieneChoiceDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str


class HygieneBoardResponse(BaseModel):
    """A project's (or a pod's) Jira hygiene: each rule's counts, then the issues that break it."""

    model_config = ConfigDict(frozen=True)

    project_id: str | None
    pod_id: str | None
    release_id: str | None
    scope_name: str
    scan: HygieneScanDto
    #: An executive reads each rule's counts, never who: findings stay empty.
    counts_only: bool
    open: int
    held: int
    rules: list[HygieneRuleRowDto]
    findings: list[HygieneFindingResponse]
    #: What the filter may pick.
    pods: list[HygieneChoiceDto]
    releases: list[HygieneChoiceDto]
    can_run: bool

    @classmethod
    def from_view(cls, view: BoardView) -> HygieneBoardResponse:
        last = view.last_run
        return cls(
            project_id=view.project_id,
            pod_id=view.pod_id,
            release_id=view.release_id,
            scope_name=view.scope_name,
            scan=HygieneScanDto(
                enabled=view.settings.enabled,
                last_run_at=(last.finished_at or last.started_at) if last else None,
                last_run_status=last.status if last else None,
                data_as_of=view.jira.as_of,
                stale=view.jira.stale,
            ),
            counts_only=view.counts_only,
            open=view.open,
            held=view.held,
            rules=[HygieneRuleRowDto.from_domain(row) for row in view.rules],
            findings=[HygieneFindingResponse.from_view(item) for item in view.findings],
            pods=[HygieneChoiceDto(id=pod_id, name=name) for pod_id, name in view.pods],
            releases=[HygieneChoiceDto(id=rid, name=name) for rid, name in view.releases],
            can_run=view.can_run,
        )


class HygieneRunSummaryDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: RunStatus
    scopes: int
    opened: int
    resolved: int
    changed: int
    open: int

    @classmethod
    def from_domain(cls, summary: RunSummary) -> HygieneRunSummaryDto:
        return cls(
            status=summary.status,
            scopes=summary.scopes,
            opened=summary.opened,
            resolved=summary.resolved,
            changed=summary.changed,
            open=summary.open,
        )


class HygieneRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: HygieneRunSummaryDto
    board: HygieneBoardResponse


class HygieneReasonRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str = Field(min_length=3, max_length=300)


class HygieneHistoryEntryDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime
    action: str
    #: A person's name, or "OpenProgram" for the scan.
    actor_name: str
    note: str | None


class HygieneHistoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    entries: list[HygieneHistoryEntryDto]

    @classmethod
    def from_domain(
        cls, actions: list[HygieneAction], names: Mapping[str, str]
    ) -> HygieneHistoryResponse:
        return cls(
            entries=[
                HygieneHistoryEntryDto(
                    at=item.at,
                    action=item.action,
                    actor_name="OpenProgram"
                    if item.actor == "agent"
                    else names.get(item.actor, UNKNOWN_PERSON),
                    note=item.note,
                )
                for item in actions
            ]
        )


__all__ = [
    "HygieneBoardResponse",
    "HygieneConfigResponse",
    "HygieneFindingResponse",
    "HygieneHistoryResponse",
    "HygienePreviewRequest",
    "HygienePreviewResponse",
    "HygienePreviewScopeDto",
    "HygieneReasonRequest",
    "HygieneRuleDto",
    "HygieneRunResponse",
    "HygieneRunSummaryDto",
    "HygieneSettingsDto",
]
