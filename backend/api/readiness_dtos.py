"""Release readiness request and response bodies.

Kept beside api/dtos.py so the feature's shapes read as one piece. Every
``can`` is worked out on the server for the viewer, so the console draws only
the buttons that will work.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from core.application.release_readiness_service import (
    BoardView,
    ConfigView,
    FindingView,
    PreviewRow,
    RunSummary,
)
from core.domain.auth import Role
from core.domain.delivery import DeliveryStage
from core.domain.release_readiness import (
    DEFAULT_SUMMARY,
    MAX_DESCRIPTION,
    MAX_EVIDENCE,
    MAX_LABELS,
    MAX_LEAD_DAYS,
    MAX_MATCHER_VALUE,
    MAX_MATCHERS,
    MAX_NAME,
    MAX_SUMMARY,
    MAX_WHEN,
    WAIVE_ROLES,
    AppliesTo,
    DecidedBy,
    DecisionKind,
    Draft,
    DraftTemplate,
    EvidenceRef,
    FindingState,
    Matcher,
    MatcherKind,
    ReadinessAction,
    ReadinessSettings,
    ReleaseCriterion,
    RunStatus,
    ScopeKind,
    Severity,
    Strength,
    SuggestionStatus,
    UrgencyKind,
)


class ReadinessShownState(StrEnum):
    COVERED = "covered"
    MISSING = "missing"
    UNSURE = "unsure"
    #: A person said it does not apply to this scope.
    NOT_APPLICABLE = "not_applicable"


# ---- configuration --------------------------------------------------------------------


class ReadinessMatcherDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: MatcherKind
    value: str = Field(min_length=1, max_length=MAX_MATCHER_VALUE)
    strength: Strength = Field(
        description="evidence: a match covers the criterion; candidate: it only makes it unsure."
    )


class ReadinessDraftTemplateDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_key: str = Field(
        default="", max_length=20, description="Empty drafts into the scope's own Jira project."
    )
    issue_type: str = Field(default="", max_length=60, description="Empty takes the tenant's.")
    summary: str = Field(default=DEFAULT_SUMMARY, max_length=MAX_SUMMARY)
    description: str = Field(
        default="", max_length=MAX_DESCRIPTION, description="Empty writes OpenProgram's own text."
    )
    labels: list[str] = Field(default_factory=list, max_length=MAX_LABELS)


class ReadinessCriterionDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion_id: str = Field(
        default="", max_length=80, description="Empty creates one; an example's id adds it."
    )
    version: int = Field(default=0, description="Read only: +1 on every save.")
    name: str = Field(min_length=1, max_length=MAX_NAME)
    evidence: str = Field(min_length=1, max_length=MAX_EVIDENCE)
    applies_to: AppliesTo
    required_before: DeliveryStage = DeliveryStage.PRODUCTION
    lead_working_days: int = Field(default=10, ge=0, le=MAX_LEAD_DAYS)
    severity: Severity = Severity.BLOCKING
    needs_done: bool = True
    when_labels: list[str] = Field(default_factory=list, max_length=MAX_WHEN)
    when_types: list[str] = Field(default_factory=list, max_length=MAX_WHEN)
    matchers: list[ReadinessMatcherDto] = Field(max_length=MAX_MATCHERS)
    draft: ReadinessDraftTemplateDto = Field(default_factory=ReadinessDraftTemplateDto)
    enabled: bool = True

    @classmethod
    def from_domain(cls, criterion: ReleaseCriterion) -> ReadinessCriterionDto:
        return cls(
            criterion_id=criterion.criterion_id,
            version=criterion.version,
            name=criterion.name,
            evidence=criterion.evidence,
            applies_to=criterion.applies_to,
            required_before=criterion.required_before,
            lead_working_days=criterion.lead_working_days,
            severity=criterion.severity,
            needs_done=criterion.needs_done,
            when_labels=list(criterion.when_labels),
            when_types=list(criterion.when_types),
            matchers=[
                ReadinessMatcherDto(kind=item.kind, value=item.value, strength=item.strength)
                for item in criterion.matchers
            ],
            draft=ReadinessDraftTemplateDto(
                project_key=criterion.draft.project_key,
                issue_type=criterion.draft.issue_type,
                summary=criterion.draft.summary,
                description=criterion.draft.description,
                labels=list(criterion.draft.labels),
            ),
            enabled=criterion.enabled,
        )

    def to_domain(self, tenant_id: str) -> ReleaseCriterion:
        return ReleaseCriterion(
            tenant_id=tenant_id,
            criterion_id=self.criterion_id.strip(),
            name=self.name,
            evidence=self.evidence,
            applies_to=self.applies_to,
            required_before=self.required_before,
            lead_working_days=self.lead_working_days,
            severity=self.severity,
            needs_done=self.needs_done,
            when_labels=tuple(self.when_labels),
            when_types=tuple(self.when_types),
            matchers=tuple(
                Matcher(kind=item.kind, value=item.value, strength=item.strength)
                for item in self.matchers
            ),
            draft=DraftTemplate(
                project_key=self.draft.project_key,
                issue_type=self.draft.issue_type,
                summary=self.draft.summary,
                description=self.draft.description,
                labels=tuple(self.draft.labels),
            ),
            enabled=self.enabled,
        )


class ReadinessSettingsDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(description="The agent checks scopes; off, a tick does nothing.")
    auto_suggest: bool = Field(description="Draft a Jira issue for every missing criterion.")
    create_in_jira: bool = Field(
        description="Lets a person press Create in Jira; Jira write-back must be on too."
    )
    issue_type: str = Field(min_length=1, max_length=60)
    labels: list[str] = Field(default_factory=list, max_length=MAX_LABELS)
    updated_at: datetime | None = None
    updated_by: str | None = None

    @classmethod
    def from_domain(cls, settings: ReadinessSettings) -> ReadinessSettingsDto:
        return cls(
            enabled=settings.enabled,
            auto_suggest=settings.auto_suggest,
            create_in_jira=settings.create_in_jira,
            issue_type=settings.issue_type,
            labels=list(settings.labels),
            updated_at=settings.updated_at,
            updated_by=settings.updated_by,
        )

    def to_domain(self, tenant_id: str) -> ReadinessSettings:
        return ReadinessSettings(
            tenant_id=tenant_id,
            enabled=self.enabled,
            auto_suggest=self.auto_suggest,
            create_in_jira=self.create_in_jira,
            issue_type=self.issue_type,
            labels=tuple(self.labels),
        )


class ReadinessConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    settings: ReadinessSettingsDto
    #: True while the tenant has saved no settings.
    is_default: bool
    criteria: list[ReadinessCriterionDto]
    #: Generic examples not yet added; none is on until an admin adds it.
    examples: list[ReadinessCriterionDto]
    #: The tenant's Jira write-back switch, which Create in Jira needs too.
    writeback_enabled: bool
    #: Who marks a blocking criterion not applicable, besides an admin.
    waive_roles: list[Role]

    @classmethod
    def from_view(cls, view: ConfigView) -> ReadinessConfigResponse:
        return cls(
            settings=ReadinessSettingsDto.from_domain(view.settings),
            is_default=view.is_default,
            criteria=[ReadinessCriterionDto.from_domain(item) for item in view.criteria],
            examples=[ReadinessCriterionDto.from_domain(item) for item in view.examples],
            writeback_enabled=view.writeback_on,
            waive_roles=sorted(WAIVE_ROLES),
        )


class ReadinessPreviewRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str = Field(min_length=1, max_length=200)
    criterion: ReadinessCriterionDto


# ---- findings ---------------------------------------------------------------------------


class ReadinessEvidenceDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    issue_key: str
    title: str
    status: str
    done: bool
    how: str = Field(description='How it was found: "label security-review", "linked".')
    url: str = Field(description="A record outside Jira a person linked; empty for an issue.")
    note: str

    @classmethod
    def from_domain(cls, item: EvidenceRef) -> ReadinessEvidenceDto:
        return cls(
            issue_key=item.issue_key,
            title=item.title,
            status=item.status,
            done=item.done,
            how=item.how,
            url=item.url,
            note=item.note,
        )


class ReadinessCandidateDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    issue_key: str
    title: str
    why: MatcherKind


class ReadinessUrgencyDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: UrgencyKind
    due_on: date | None
    #: Working days after today up to the due day; negative once it passed.
    working_days_left: int | None
    delivery_date: date | None
    #: The requirement that reached the stage, for stage_reached.
    stage_key: str | None


class ReadinessScopeDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ScopeKind
    id: str
    name: str


class ReadinessCriterionSummaryDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion_id: str
    name: str
    severity: Severity
    required_before: DeliveryStage
    evidence: str
    needs_done: bool


class ReadinessDecisionDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: DecisionKind
    by: str
    by_name: str | None
    at: datetime
    reason: str
    issue_key: str
    url: str
    note: str
    #: The linked issue was created from this finding's draft.
    created: bool


class ReadinessDraftDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_key: str = Field(min_length=1, max_length=20)
    issue_type: str = Field(min_length=1, max_length=60)
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)
    labels: list[str] = Field(default_factory=list, max_length=MAX_LABELS)

    @classmethod
    def from_domain(cls, draft: Draft) -> ReadinessDraftDto:
        return cls.model_construct(
            project_key=draft.project_key,
            issue_type=draft.issue_type,
            summary=draft.summary,
            description=draft.description,
            labels=list(draft.labels),
        )

    def to_domain(self) -> Draft:
        return Draft(
            project_key=self.project_key,
            issue_type=self.issue_type,
            summary=self.summary,
            description=self.description,
            labels=tuple(self.labels),
        )


class ReadinessDismissalDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    by: str
    by_name: str | None
    at: datetime | None
    reason: str


class ReadinessSuggestionDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    suggestion_id: str
    status: SuggestionStatus
    version: int
    draft: ReadinessDraftDto
    #: Goes on the created issue, so a retry finds what an earlier try made.
    marker_label: str
    created_issue_key: str | None
    created_by_name: str | None
    created_at: datetime | None
    dismissed: ReadinessDismissalDto | None


class ReadinessActionsDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    create: bool
    #: Why Create in Jira is off for a draft this viewer could otherwise create.
    create_off_reason: str | None
    link: bool
    not_applicable: bool
    dismiss: bool
    edit: bool
    reopen: bool
    #: Draft an issue on request (the tenant drafts none by itself).
    draft: bool


class ReadinessFindingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_id: str
    criterion: ReadinessCriterionSummaryDto
    scope: ReadinessScopeDto
    state: ReadinessShownState
    done: bool
    decided_by: DecidedBy
    #: Based on stale Jira data: kept out of the day report until a fresh sync.
    held: bool
    evidence: list[ReadinessEvidenceDto]
    candidates: list[ReadinessCandidateDto]
    reason: str
    urgency: ReadinessUrgencyDto
    person_decision: ReadinessDecisionDto | None
    suggestion: ReadinessSuggestionDto | None
    can: ReadinessActionsDto
    checked_at: datetime

    @classmethod
    def from_view(cls, view: FindingView, names: Mapping[str, str]) -> ReadinessFindingResponse:
        finding, criterion, suggestion = view.finding, view.criterion, view.suggestion
        person = finding.person
        actions = view.actions
        return cls(
            finding_id=finding.finding_id,
            criterion=ReadinessCriterionSummaryDto(
                criterion_id=criterion.criterion_id,
                name=criterion.name,
                severity=criterion.severity,
                required_before=criterion.required_before,
                evidence=criterion.evidence,
                needs_done=criterion.needs_done,
            ),
            scope=ReadinessScopeDto(
                kind=finding.scope.kind, id=finding.scope.id, name=view.scope_name
            ),
            state=ReadinessShownState(view.shown_state),
            done=finding.done,
            decided_by=finding.decided_by,
            held=finding.held,
            evidence=[ReadinessEvidenceDto.from_domain(item) for item in finding.evidence],
            candidates=[
                ReadinessCandidateDto(issue_key=item.issue_key, title=item.title, why=item.why)
                for item in finding.candidates
            ],
            reason=finding.reason,
            urgency=ReadinessUrgencyDto(
                kind=finding.urgency.kind,
                due_on=finding.urgency.due_on,
                working_days_left=view.working_days_left,
                delivery_date=finding.urgency.delivery_date,
                stage_key=finding.urgency.stage_key,
            ),
            person_decision=(
                ReadinessDecisionDto(
                    kind=person.kind,
                    by=person.by,
                    by_name=names.get(person.by),
                    at=person.at,
                    reason=person.reason,
                    issue_key=person.issue_key,
                    url=person.url,
                    note=person.note,
                    created=person.created,
                )
                if person is not None
                else None
            ),
            suggestion=(
                ReadinessSuggestionDto(
                    suggestion_id=suggestion.suggestion_id,
                    status=suggestion.status,
                    version=suggestion.version,
                    draft=ReadinessDraftDto.from_domain(suggestion.draft),
                    marker_label=suggestion.marker_label,
                    created_issue_key=suggestion.created_issue_key,
                    created_by_name=(
                        names.get(suggestion.created_by) if suggestion.created_by else None
                    ),
                    created_at=suggestion.created_at,
                    dismissed=(
                        ReadinessDismissalDto(
                            by=suggestion.dismissed_by or "",
                            by_name=names.get(suggestion.dismissed_by or ""),
                            at=suggestion.dismissed_at,
                            reason=suggestion.dismissed_reason or "",
                        )
                        if suggestion.status is SuggestionStatus.DISMISSED
                        else None
                    ),
                )
                if suggestion is not None
                else None
            ),
            can=ReadinessActionsDto(
                create=actions.create,
                create_off_reason=actions.create_off_reason,
                link=actions.link,
                not_applicable=actions.not_applicable,
                dismiss=actions.dismiss,
                edit=actions.edit,
                reopen=actions.reopen,
                draft=actions.draft,
            ),
            checked_at=finding.updated_at,
        )


class ReadinessAgentDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
    create_in_jira: bool
    writeback_enabled: bool
    last_run_at: datetime | None
    last_run_status: RunStatus | None
    #: The Jira sync the findings read.
    data_as_of: datetime | None
    #: The Jira sync is failing or behind: findings show Jira as of data_as_of.
    stale: bool


class ReadinessSummaryDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    blocking_missing: int
    missing: int
    unsure: int
    covered: int
    not_applicable: int
    total: int


class ReadinessReleaseLineDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    release_id: str
    name: str
    missing: int
    unsure: int
    total: int


class ReadinessBoardResponse(BaseModel):
    """A project's (or a release's, or a pod's) criteria and where each stands."""

    model_config = ConfigDict(frozen=True)

    project_id: str | None
    pod_id: str | None
    release_id: str | None
    scope_name: str
    agent: ReadinessAgentDto
    summary: ReadinessSummaryDto
    findings: list[ReadinessFindingResponse]
    #: In the whole-project view: each release's rows, counted.
    releases: list[ReadinessReleaseLineDto]
    #: What the day report's Most important says of these scopes, in its words.
    important: list[str]
    can_run: bool

    @classmethod
    def from_view(cls, view: BoardView) -> ReadinessBoardResponse:
        findings = [ReadinessFindingResponse.from_view(item, view.names) for item in view.findings]
        states = [item.state for item in findings]
        last = view.last_run
        return cls(
            project_id=view.project_id,
            pod_id=view.pod_id,
            release_id=view.release_id,
            scope_name=view.scope_name,
            agent=ReadinessAgentDto(
                enabled=view.settings.enabled,
                create_in_jira=view.settings.create_in_jira,
                writeback_enabled=view.writeback_on,
                last_run_at=(last.finished_at or last.started_at) if last else None,
                last_run_status=last.status if last else None,
                data_as_of=view.jira.as_of,
                stale=view.jira.stale,
            ),
            summary=ReadinessSummaryDto(
                blocking_missing=sum(
                    1
                    for item in findings
                    if item.state is ReadinessShownState.MISSING
                    and item.criterion.severity is Severity.BLOCKING
                ),
                missing=states.count(ReadinessShownState.MISSING),
                unsure=states.count(ReadinessShownState.UNSURE),
                covered=states.count(ReadinessShownState.COVERED),
                not_applicable=states.count(ReadinessShownState.NOT_APPLICABLE),
                total=len(findings),
            ),
            findings=findings,
            releases=[
                ReadinessReleaseLineDto(
                    release_id=item.release_id,
                    name=item.name,
                    missing=item.missing,
                    unsure=item.unsure,
                    total=item.total,
                )
                for item in view.releases
            ],
            important=list(view.important),
            can_run=view.can_run,
        )


class ReadinessRunSummaryDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: RunStatus
    scopes: int
    changed: int
    missing: int
    unsure: int
    covered: int

    @classmethod
    def from_domain(cls, summary: RunSummary) -> ReadinessRunSummaryDto:
        return cls(
            status=summary.status,
            scopes=summary.scopes,
            changed=summary.changed,
            missing=summary.missing,
            unsure=summary.unsure,
            covered=summary.covered,
        )


class ReadinessRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: ReadinessRunSummaryDto
    board: ReadinessBoardResponse


class ReadinessLinkRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    issue_key: str | None = Field(default=None, max_length=80)
    evidence_url: str | None = Field(default=None, max_length=2000)
    note: str = Field(default="", max_length=300)


class ReadinessReasonRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str = Field(max_length=300, description="3 to 300 characters.")


class ReadinessDraftUpdateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int = Field(description="The draft's version as it was opened.")
    draft: ReadinessDraftDto


class ReadinessCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int = Field(description="The draft's version the person approved.")


class ReadinessCreateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    issue_key: str
    url: str | None
    #: False when an earlier press or try had already made it.
    created: bool
    finding: ReadinessFindingResponse


class ReadinessHistoryEntryDto(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime
    actor: str
    actor_name: str | None
    action: str
    reason: str | None


class ReadinessHistoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_id: str
    entries: list[ReadinessHistoryEntryDto]

    @classmethod
    def from_domain(
        cls, finding_id: str, actions: list[ReadinessAction], names: Mapping[str, str]
    ) -> ReadinessHistoryResponse:
        return cls(
            finding_id=finding_id,
            entries=[
                ReadinessHistoryEntryDto(
                    at=item.at,
                    actor=item.actor,
                    actor_name=names.get(item.actor),
                    action=item.action,
                    reason=item.reason,
                )
                for item in actions
            ],
        )


class ReadinessPreviewRowResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: ReadinessScopeDto
    applies: bool
    state: FindingState
    evidence: list[ReadinessEvidenceDto]
    candidates: list[ReadinessCandidateDto]
    reason: str
    urgency: UrgencyKind
    due_on: date | None

    @classmethod
    def from_domain(cls, row: PreviewRow) -> ReadinessPreviewRowResponse:
        return cls(
            scope=ReadinessScopeDto(kind=row.scope.kind, id=row.scope.id, name=row.scope_name),
            applies=row.applies,
            state=row.evaluation.state,
            evidence=[ReadinessEvidenceDto.from_domain(item) for item in row.evaluation.evidence],
            candidates=[
                ReadinessCandidateDto(issue_key=item.issue_key, title=item.title, why=item.why)
                for item in row.evaluation.candidates
            ],
            reason=row.evaluation.reason,
            urgency=row.urgency.kind,
            due_on=row.urgency.due_on,
        )


class ReadinessPreviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    rows: list[ReadinessPreviewRowResponse]
