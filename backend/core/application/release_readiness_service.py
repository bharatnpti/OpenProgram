"""Release readiness: each scope checked against the tenant's criteria, and what people do about it.

A run reads only what OpenProgram already holds: the synced issues each scope
owns, where its requirements stand, and its committed date. It never calls
Jira, so a Jira outage cannot fail it; when the issue sync is failing or
stale, a covered finding is never turned missing and a new blocking gap is
held out of the day report until fresh data confirms it. A finding whose
fingerprint did not change is not written and not audited.

A missing finding gets one drafted Jira issue for its whole life: a dismissed
draft is never drafted again until someone reopens it. Nothing reaches Jira
unless a person presses Create on one draft at one version, with the readiness
switch and the tenant's write-back switch both on; this service is the one
caller of ``IssueTracker.create_issue``. Every person's action and every change
the agent makes goes into the append-only audit.

Findings name scopes and criteria, never people: a draft describes the work,
and the day report's ask goes to whoever decides.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from uuid import uuid4

from core.application.delivery_service import DeliveryService
from core.application.forecast_service import ForecastService
from core.application.persona_views import owned_project_tasks
from core.application.sync_services import record_issue_as_synced
from core.domain.auth import Role
from core.domain.delivery import DeliveryStage, StageMapping, place
from core.domain.errors import (
    AuthorizationDenied,
    GraphNotFound,
    IssueCreateFailed,
    OpenProgramError,
    ProviderUnavailable,
)
from core.domain.forecast import Release, split_names
from core.domain.graph import EdgeKind, GraphNode, NodeKind
from core.domain.integrations import Issue, IssueState, NewIssue, SyncCursor
from core.domain.release_readiness import (
    MAX_CRITERIA,
    URGENT,
    WAIVE_ROLES,
    AppliesTo,
    DecidedBy,
    DecisionKind,
    Draft,
    DraftContext,
    Evaluation,
    Finding,
    FindingState,
    PersonDecision,
    ReadinessAction,
    ReadinessError,
    ReadinessRun,
    ReadinessSettings,
    ReleaseCriterion,
    RunStatus,
    RunTrigger,
    ScopeIssue,
    ScopeKind,
    ScopeRef,
    Suggestion,
    SuggestionStatus,
    Urgency,
    UrgencyKind,
    applies_to_scope,
    created_footer,
    criterion_slug,
    default_examples,
    evaluate_criterion,
    finding_fingerprint,
    is_ready,
    marker_label,
    name_in_sentence,
    render_draft,
    stage_words,
    urgency_of,
    validated_criterion,
    validated_draft,
    validated_reason,
    validated_record,
    validated_settings,
    with_article,
    working_days_left,
)
from core.ports.issue_tracker import IssueTracker
from core.ports.release_readiness import ReleaseReadinessRepository
from core.ports.repositories import GraphRepository, IdentityLinkRepository, TimeSeriesRepository

AGENT = "agent"
#: How many readiness lines a day report's Most important carries before "and N more".
MAX_REPORT_LINES = 3

_NOT_YOURS = "You can act on readiness only for pods you run and the projects they work on."
_WAIVE = "Only a manager or an admin marks a blocking criterion not applicable."
_FIELD_WORDS = {
    "project": "the project key",
    "issuetype": "the issue type",
    "summary": "the summary",
    "description": "the text",
    "labels": "the labels",
    "components": "Components",
    "reporter": "the reporter",
    "priority": "the priority",
    "duedate": "the due date",
    "assignee": "the assignee",
}
_URGENCY_ORDER = {
    UrgencyKind.STAGE_REACHED: 0,
    UrgencyKind.OVERDUE: 1,
    UrgencyKind.DUE_SOON: 2,
    UrgencyKind.LATER: 3,
    UrgencyKind.NO_DATE: 4,
}


class ReadinessConflict(OpenProgramError):
    """The action cannot be taken in the finding's or draft's current state (409).

    ``code`` is a fixed word the console may branch on; the message is a
    sentence for people.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, kw_only=True)
class JiraData:
    """How fresh the synced Jira data is, from the issue sync's own status."""

    stale: bool = False
    as_of: datetime | None = None


JiraHealth = Callable[[str], Awaitable[JiraData]]
PodTaskIds = Callable[[str, str, date], Awaitable[Sequence[str]]]
WriteBackGate = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True, kw_only=True)
class Viewer:
    """Who is asking, and how far they reach. The router fills it from the principal."""

    subject: str
    roles: frozenset[Role]
    #: ACT_ON_READINESS.
    may_act: bool
    #: The pods whose findings they see and act on; None for every pod.
    pods: frozenset[str] | None = None
    #: Whether they act on the project's and its releases' findings.
    project_reach: bool = True
    #: An executive reads the rows without drafts.
    sees_drafts: bool = True

    @property
    def waives_blocking(self) -> bool:
        return Role.ADMIN in self.roles or bool(self.roles & WAIVE_ROLES)

    def acts_on(self, scope: ScopeRef) -> bool:
        if not self.may_act:
            return False
        if scope.kind is ScopeKind.POD:
            return self.pods is None or scope.id in self.pods
        return self.project_reach

    def sees(self, scope: ScopeRef) -> bool:
        return scope.kind is not ScopeKind.POD or self.pods is None or scope.id in self.pods


@dataclass(frozen=True, kw_only=True)
class ScopeContext:
    scope: ScopeRef
    name: str
    #: The project a project or release scope belongs to; None for a pod.
    project_id: str | None
    project_name: str = ""
    #: The Jira project a draft goes to by default.
    project_key: str = ""
    release_name: str = ""
    pod_name: str = ""
    issues: tuple[ScopeIssue, ...] = ()
    #: Each requirement's key and stage.
    stages: tuple[tuple[str, DeliveryStage], ...] = ()
    target: date | None = None
    console_path: str = ""
    #: A project with releases is judged per release on release criteria.
    has_releases: bool = False


@dataclass(frozen=True, kw_only=True)
class FindingActions:
    create: bool = False
    create_off_reason: str | None = None
    link: bool = False
    not_applicable: bool = False
    dismiss: bool = False
    edit: bool = False
    reopen: bool = False
    draft: bool = False


@dataclass(frozen=True, kw_only=True)
class FindingView:
    finding: Finding
    criterion: ReleaseCriterion
    scope_name: str
    suggestion: Suggestion | None
    actions: FindingActions
    working_days_left: int | None

    @property
    def shown_state(self) -> str:
        return "not_applicable" if self.finding.not_applicable else self.finding.state.value


@dataclass(frozen=True, kw_only=True)
class ReleaseLine:
    release_id: str
    name: str
    missing: int
    unsure: int
    total: int


@dataclass(frozen=True, kw_only=True)
class BoardView:
    project_id: str | None
    pod_id: str | None
    release_id: str | None
    scope_name: str
    settings: ReadinessSettings
    last_run: ReadinessRun | None
    jira: JiraData
    writeback_on: bool
    findings: tuple[FindingView, ...]
    releases: tuple[ReleaseLine, ...]
    #: The day report's readiness lines for this scope, in its words.
    important: tuple[str, ...]
    can_run: bool
    names: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class RunSummary:
    status: RunStatus
    scopes: int = 0
    changed: int = 0
    missing: int = 0
    unsure: int = 0
    covered: int = 0


@dataclass(frozen=True, kw_only=True)
class ReadinessGap:
    """One blocking finding a day report raises: a Most important line and a decision ask."""

    finding_id: str
    scope: ScopeRef
    line: str
    ask: str
    waited_days: int | None
    urgency: UrgencyKind
    #: The day it became due soon or worse; an ask's wait counts from here.
    entered_on: date | None = None


@dataclass(frozen=True, kw_only=True)
class ReportGaps:
    gaps: tuple[ReadinessGap, ...] = ()

    @property
    def lines(self) -> tuple[str, ...]:
        """Most important's readiness lines: at most three, then how many more."""
        shown = [gap.line for gap in self.gaps[:MAX_REPORT_LINES]]
        rest = len(self.gaps) - MAX_REPORT_LINES
        if rest > 0:
            shown.append(f"and {rest} more in Release readiness.")
        return tuple(shown)


@dataclass(frozen=True, kw_only=True)
class PreviewRow:
    scope: ScopeRef
    scope_name: str
    applies: bool
    evaluation: Evaluation
    urgency: Urgency


@dataclass(frozen=True, kw_only=True)
class ConfigView:
    settings: ReadinessSettings
    is_default: bool
    criteria: tuple[ReleaseCriterion, ...]
    examples: tuple[ReleaseCriterion, ...]
    writeback_on: bool


@dataclass(frozen=True, kw_only=True)
class CreateResult:
    issue_key: str
    created: bool
    finding: FindingView


class ReleaseReadinessService:
    def __init__(
        self,
        *,
        repository: ReleaseReadinessRepository,
        graph_repository: GraphRepository,
        delivery_service: DeliveryService,
        forecast_service: ForecastService,
        issue_tracker: IssueTracker,
        writeback_gate: WriteBackGate,
        jira_health: JiraHealth,
        pod_task_ids: PodTaskIds,
        identity_link_repository: IdentityLinkRepository | None = None,
        time_series_repository: TimeSeriesRepository | None = None,
        console_base_url: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
        today: Callable[[], date] | None = None,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._repository = repository
        self._graph = graph_repository
        self._delivery = delivery_service
        self._forecast = forecast_service
        self._tracker = issue_tracker
        self._writeback_gate = writeback_gate
        self._jira_health = jira_health
        self._pod_task_ids = pod_task_ids
        self._identity_links = identity_link_repository
        self._facts = time_series_repository
        self._console = console_base_url.rstrip("/") if console_base_url else ""
        self._clock = clock
        self._today = today or (lambda: clock().date())
        self._new_id = new_id

    # ---- configuration ---------------------------------------------------------------

    async def settings(self, tenant_id: str) -> ReadinessSettings:
        return await self._repository.get_settings(tenant_id) or ReadinessSettings(
            tenant_id=tenant_id
        )

    async def config(self, tenant_id: str) -> ConfigView:
        stored = await self._repository.get_settings(tenant_id)
        criteria = await self._repository.list_criteria(tenant_id)
        added = {item.criterion_id for item in criteria}
        return ConfigView(
            settings=stored or ReadinessSettings(tenant_id=tenant_id),
            is_default=stored is None,
            criteria=tuple(criteria),
            examples=tuple(
                item for item in default_examples(tenant_id) if item.criterion_id not in added
            ),
            writeback_on=await self._writeback_gate(tenant_id),
        )

    async def save_settings(self, settings: ReadinessSettings, *, actor: str) -> ReadinessSettings:
        before = await self._repository.get_settings(settings.tenant_id)
        saved = replace(validated_settings(settings), updated_at=self._clock(), updated_by=actor)
        await self._repository.save_settings(saved)
        await self._audit(
            settings.tenant_id,
            actor,
            "settings_saved",
            before=_settings_json(before) if before else None,
            after=_settings_json(saved),
        )
        return saved

    async def save_criterion(self, criterion: ReleaseCriterion, *, actor: str) -> ReleaseCriterion:
        tenant_id = criterion.tenant_id
        every = await self._repository.list_criteria(tenant_id, include_deleted=True)
        current = next(
            (item for item in every if item.criterion_id == criterion.criterion_id), None
        )
        examples = {item.criterion_id for item in default_examples(tenant_id)}
        if criterion.criterion_id and current is None and criterion.criterion_id not in examples:
            raise GraphNotFound(f"No criterion {criterion.criterion_id!r}.")
        live = [item for item in every if item.deleted_at is None]
        if (current is None or current.deleted_at is not None) and len(live) >= MAX_CRITERIA:
            raise ReadinessError(f"A tenant has at most {MAX_CRITERIA} release criteria.")
        checked = validated_criterion(criterion)
        saved = replace(
            checked,
            criterion_id=current.criterion_id
            if current
            else criterion.criterion_id
            or criterion_slug(checked.name, (item.criterion_id for item in every)),
            version=current.version + 1 if current else 1,
            deleted_at=None,
            updated_at=self._clock(),
            updated_by=actor,
        )
        await self._repository.save_criterion(saved)
        await self._audit(
            tenant_id,
            actor,
            "criterion_saved",
            criterion_id=saved.criterion_id,
            before={"version": current.version} if current else None,
            after={"version": saved.version, "name": saved.name, "enabled": saved.enabled},
        )
        return saved

    async def remove_criterion(self, tenant_id: str, criterion_id: str, *, actor: str) -> None:
        criterion = await self._criterion(tenant_id, criterion_id)
        await self._repository.save_criterion(
            replace(criterion, deleted_at=self._clock(), updated_at=self._clock(), updated_by=actor)
        )
        await self._audit(tenant_id, actor, "criterion_removed", criterion_id=criterion_id)

    async def preview(self, criterion: ReleaseCriterion, project_id: str) -> tuple[PreviewRow, ...]:
        """What the agent would find on one project with this criterion. Saves nothing."""
        checked = validated_criterion(replace(criterion, criterion_id="preview"))
        today = self._today()
        contexts, known = await self._project_contexts(checked.tenant_id, project_id, today)
        rows: list[PreviewRow] = []
        for context in contexts:
            if not _expected(checked, context):
                continue
            evaluation = evaluate_criterion(
                checked, context.issues, scope_name=context.name, known=known
            )
            rows.append(
                PreviewRow(
                    scope=context.scope,
                    scope_name=context.name,
                    applies=applies_to_scope(checked, context.issues),
                    evaluation=evaluation,
                    urgency=_urgency(checked, evaluation, None, context, today),
                )
            )
        return tuple(rows)

    # ---- runs ------------------------------------------------------------------------

    async def run_tenant(self, tenant_id: str, *, slot: str) -> RunSummary | None:
        """The hourly pass over every scope. None when the slot already ran or the
        tenant never set readiness up: nothing is recorded for it."""
        stored = await self._repository.get_settings(tenant_id)
        if stored is None:
            return None
        run = self._new_run(tenant_id, slot, RunTrigger.SCHEDULE, None, None)
        if not await self._repository.claim_run(run):
            return None
        if not stored.enabled:
            await self._finish(run, RunSummary(status=RunStatus.OFF), None)
            return RunSummary(status=RunStatus.OFF)
        today = self._today()
        jira = await self._jira_health(tenant_id)
        try:
            contexts: list[ScopeContext] = []
            known: dict[str, ScopeIssue] = {}
            pods: dict[str, ScopeContext] = {}
            for project in await self._graph.list_nodes(tenant_id, NodeKind.PROJECT):
                found, known = await self._project_contexts(tenant_id, project.id, today)
                for context in found:
                    if context.scope.kind is not ScopeKind.POD:
                        contexts.append(context)
                    else:
                        pods[context.scope.id] = _earlier(pods.get(context.scope.id), context)
            summary = await self._judge_all(
                tenant_id, [*contexts, *pods.values()], known, stored, jira, run.run_id, today
            )
        except Exception as exc:
            _log_failure(tenant_id, exc)
            failed = RunSummary(status=RunStatus.FAILED)
            await self._finish(run, failed, jira, error="unexpected")
            return failed
        await self._finish(run, summary, jira)
        return summary

    async def run_project(self, tenant_id: str, project_id: str, viewer: Viewer) -> RunSummary:
        """Run check now, for a project with its releases and pods, inline."""
        settings = await self._enabled(tenant_id)
        if not viewer.may_act or not viewer.project_reach:
            raise AuthorizationDenied(_NOT_YOURS)
        today = self._today()
        contexts, known = await self._project_contexts(tenant_id, project_id, today)
        scope = ScopeRef(kind=ScopeKind.PROJECT, id=project_id)
        return await self._manual(tenant_id, scope, contexts, known, settings, viewer)

    async def run_pod(self, tenant_id: str, pod_id: str, viewer: Viewer) -> RunSummary:
        settings = await self._enabled(tenant_id)
        scope = ScopeRef(kind=ScopeKind.POD, id=pod_id)
        if not viewer.acts_on(scope):
            raise AuthorizationDenied(_NOT_YOURS)
        today = self._today()
        context, known = await self._pod_context(tenant_id, pod_id, today)
        return await self._manual(tenant_id, scope, [context], known, settings, viewer)

    async def _manual(
        self,
        tenant_id: str,
        scope: ScopeRef,
        contexts: Sequence[ScopeContext],
        known: Mapping[str, ScopeIssue],
        settings: ReadinessSettings,
        viewer: Viewer,
    ) -> RunSummary:
        now = self._clock()
        slot = f"manual:{scope.key}:{now:%Y%m%d%H%M}"
        run = self._new_run(tenant_id, slot, RunTrigger.MANUAL, scope, viewer.subject)
        claimed = await self._repository.claim_run(run)
        await self._audit(tenant_id, viewer.subject, "run_now", after={"scope": scope.key})
        jira = await self._jira_health(tenant_id)
        summary = await self._judge_all(
            tenant_id,
            [context for context in contexts if viewer.sees(context.scope)],
            known,
            settings,
            jira,
            run.run_id,
            self._today(),
        )
        if claimed:
            await self._finish(run, summary, jira)
        return summary

    async def _judge_all(
        self,
        tenant_id: str,
        contexts: Sequence[ScopeContext],
        known: Mapping[str, ScopeIssue],
        settings: ReadinessSettings,
        jira: JiraData,
        run_id: str,
        today: date,
    ) -> RunSummary:
        criteria = [
            item for item in await self._repository.list_criteria(tenant_id) if item.enabled
        ]
        findings = {
            (item.criterion_id, item.scope.key): item
            for item in await self._repository.list_findings(
                tenant_id, [context.scope for context in contexts]
            )
        }
        suggestions = {
            item.finding_id: item
            for item in await self._repository.list_suggestions(
                tenant_id, [item.finding_id for item in findings.values()]
            )
        }
        changed = missing = unsure = covered = 0
        for context in contexts:
            for criterion in criteria:
                if not _expected(criterion, context):
                    continue
                existing = findings.get((criterion.criterion_id, context.scope.key))
                finding, wrote = await self._judge(
                    tenant_id,
                    criterion,
                    context,
                    known,
                    existing=existing,
                    suggestion=suggestions.get(existing.finding_id) if existing else None,
                    settings=settings,
                    jira=jira,
                    run_id=run_id,
                    today=today,
                )
                changed += wrote
                if finding.applies and not finding.not_applicable:
                    missing += finding.state is FindingState.MISSING
                    unsure += finding.state is FindingState.UNSURE
                    covered += finding.state is FindingState.COVERED
        return RunSummary(
            status=RunStatus.OK,
            scopes=len(contexts),
            changed=changed,
            missing=missing,
            unsure=unsure,
            covered=covered,
        )

    async def _judge(
        self,
        tenant_id: str,
        criterion: ReleaseCriterion,
        context: ScopeContext,
        known: Mapping[str, ScopeIssue],
        *,
        existing: Finding | None,
        suggestion: Suggestion | None,
        settings: ReadinessSettings,
        jira: JiraData,
        run_id: str | None,
        today: date,
        actor: str = AGENT,
    ) -> tuple[Finding, bool]:
        """Evaluate one criterion on one scope; write only what changed."""
        person = existing.person if existing else None
        applies = applies_to_scope(criterion, context.issues)
        evaluation = evaluate_criterion(
            criterion, context.issues, scope_name=context.name, person=person, known=known
        )
        if (
            jira.stale
            and existing is not None
            and existing.state is FindingState.COVERED
            and evaluation.state is not FindingState.COVERED
        ):
            # Stale data never turns a covered finding missing.
            return existing, False
        held = (
            jira.stale
            and criterion.blocking
            and evaluation.state is not FindingState.COVERED
            and (existing is None or existing.held or existing.state is FindingState.COVERED)
        )
        urgency = _urgency(criterion, evaluation, person, context, today)
        updated = {issue.key: issue.updated_at for issue in context.issues}
        fingerprint = finding_fingerprint(
            criterion_version=criterion.version,
            scope=context.scope,
            evaluation=evaluation,
            urgency=urgency,
            person=person,
            applies=applies,
            held=held,
            updated=updated,
        )
        now = self._clock()
        finding = existing
        wrote = False
        if existing is None or existing.fingerprint != fingerprint:
            entered = (
                (existing.window_entered_at if existing and existing.window_entered_at else now)
                if urgency.kind in URGENT
                else None
            )
            finding = Finding(
                tenant_id=tenant_id,
                finding_id=existing.finding_id if existing else f"rf_{self._new_id()}",
                criterion_id=criterion.criterion_id,
                criterion_version=criterion.version,
                scope=context.scope,
                project_id=context.project_id,
                state=evaluation.state,
                done=evaluation.done,
                decided_by=evaluation.decided_by,
                evidence=evaluation.evidence,
                candidates=evaluation.candidates,
                reason=evaluation.reason,
                urgency=urgency,
                held=held,
                applies=applies,
                person=person,
                fingerprint=fingerprint,
                first_seen_at=existing.first_seen_at if existing else now,
                window_entered_at=entered,
                last_run_id=run_id or (existing.last_run_id if existing else None),
                updated_at=now,
            )
            await self._repository.save_finding(finding)
            await self._audit(
                tenant_id,
                actor,
                "state_changed",
                finding_id=finding.finding_id,
                criterion_id=criterion.criterion_id,
                before=_finding_json(existing) if existing else None,
                after=_finding_json(finding),
                correlation_id=run_id,
            )
            wrote = True
        assert finding is not None
        await self._keep_draft(tenant_id, criterion, context, finding, suggestion, settings)
        return finding, wrote

    async def _keep_draft(
        self,
        tenant_id: str,
        criterion: ReleaseCriterion,
        context: ScopeContext,
        finding: Finding,
        suggestion: Suggestion | None,
        settings: ReadinessSettings,
    ) -> None:
        """Draft an issue for a new gap; re-render an untouched open draft after an edit."""
        wanted = (
            finding.state is FindingState.MISSING
            and finding.applies
            and finding.person is None
            and settings.auto_suggest
        )
        if suggestion is None:
            if wanted:
                await self._draft(tenant_id, criterion, context, finding, settings, actor=AGENT)
            return
        if (
            suggestion.status is SuggestionStatus.OPEN
            and suggestion.edited_by is None
            and suggestion.criterion_version < criterion.version
        ):
            await self._repository.save_suggestion(
                replace(
                    suggestion,
                    draft=render_draft(criterion, settings, self._draft_context(context, finding)),
                    version=suggestion.version + 1,
                    criterion_version=criterion.version,
                    updated_at=self._clock(),
                )
            )
            await self._audit(
                tenant_id,
                AGENT,
                "draft_rerendered",
                finding_id=finding.finding_id,
                suggestion_id=suggestion.suggestion_id,
                criterion_id=criterion.criterion_id,
            )

    async def _draft(
        self,
        tenant_id: str,
        criterion: ReleaseCriterion,
        context: ScopeContext,
        finding: Finding,
        settings: ReadinessSettings,
        *,
        actor: str,
    ) -> Suggestion:
        suggestion_id = f"rs_{self._new_id()}"
        suggestion = Suggestion(
            tenant_id=tenant_id,
            suggestion_id=suggestion_id,
            finding_id=finding.finding_id,
            status=SuggestionStatus.OPEN,
            version=1,
            draft=render_draft(criterion, settings, self._draft_context(context, finding)),
            marker_label=marker_label(suggestion_id),
            criterion_version=criterion.version,
            updated_at=self._clock(),
        )
        await self._repository.save_suggestion(suggestion)
        await self._audit(
            tenant_id,
            actor,
            "drafted",
            finding_id=finding.finding_id,
            suggestion_id=suggestion_id,
            criterion_id=criterion.criterion_id,
            after={"summary": suggestion.draft.summary},
        )
        return suggestion

    def _draft_context(self, context: ScopeContext, finding: Finding) -> DraftContext:
        return DraftContext(
            scope_kind=context.scope.kind,
            scope_name=context.name,
            project_name=context.project_name,
            project_key=context.project_key,
            release_name=context.release_name,
            pod_name=context.pod_name,
            due_on=finding.urgency.due_on,
            delivery_date=finding.urgency.delivery_date,
            console_link=f"{self._console}{context.console_path}" if self._console else "",
        )

    # ---- the board -------------------------------------------------------------------

    async def project_board(
        self, tenant_id: str, project_id: str, viewer: Viewer, *, release: Release | None = None
    ) -> BoardView:
        project = await self._graph.get_node(tenant_id, project_id)
        if project is None or project.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"No project {project_id!r}.")
        today = self._today()
        releases = await self._forecast.releases(tenant_id, project_id)
        scopes: list[tuple[ScopeRef, str]] = [
            (ScopeRef(kind=ScopeKind.PROJECT, id=project_id), project.name)
        ]
        if release is not None:
            scopes.append((ScopeRef(kind=ScopeKind.RELEASE, id=release.release_id), release.name))
        else:
            scopes.extend(
                (ScopeRef(kind=ScopeKind.RELEASE, id=item.release_id), item.name)
                for item in releases
            )
            scopes.extend(
                (ScopeRef(kind=ScopeKind.POD, id=pod.id), pod.name)
                for pod in await self._project_pods(tenant_id, project_id, today)
            )
        views = await self._views(tenant_id, scopes, viewer, has_releases=bool(releases))
        shown = tuple(
            view
            for view in views
            if view.finding.scope.kind is not ScopeKind.RELEASE or release is not None
        )
        lines = tuple(
            ReleaseLine(
                release_id=item.release_id,
                name=item.name,
                missing=sum(
                    1
                    for view in views
                    if view.finding.scope.id == item.release_id
                    and view.shown_state == FindingState.MISSING.value
                ),
                unsure=sum(
                    1
                    for view in views
                    if view.finding.scope.id == item.release_id
                    and view.shown_state == FindingState.UNSURE.value
                ),
                total=sum(1 for view in views if view.finding.scope.id == item.release_id),
            )
            for item in (releases if release is None else ())
        )
        return await self._board(
            tenant_id,
            viewer,
            shown,
            gaps_from=views,
            project_id=project_id,
            pod_id=None,
            release_id=release.release_id if release else None,
            scope_name=release.name if release else project.name,
            releases=lines,
            can_run=viewer.may_act and viewer.project_reach,
        )

    async def pod_board(self, tenant_id: str, pod_id: str, viewer: Viewer) -> BoardView:
        pod = await self._graph.get_node(tenant_id, pod_id)
        if pod is None or pod.kind is not NodeKind.POD:
            raise GraphNotFound(f"No pod {pod_id!r}.")
        scope = ScopeRef(kind=ScopeKind.POD, id=pod_id)
        views = await self._views(tenant_id, [(scope, pod.name)], viewer, has_releases=False)
        return await self._board(
            tenant_id,
            viewer,
            tuple(views),
            gaps_from=views,
            project_id=None,
            pod_id=pod_id,
            release_id=None,
            scope_name=pod.name,
            releases=(),
            can_run=viewer.acts_on(scope),
        )

    async def _board(
        self,
        tenant_id: str,
        viewer: Viewer,
        views: tuple[FindingView, ...],
        *,
        gaps_from: Sequence[FindingView],
        project_id: str | None,
        pod_id: str | None,
        release_id: str | None,
        scope_name: str,
        releases: tuple[ReleaseLine, ...],
        can_run: bool,
    ) -> BoardView:
        settings = await self.settings(tenant_id)
        actors = {
            actor
            for view in views
            for actor in (
                view.finding.person.by if view.finding.person else None,
                view.suggestion.dismissed_by if view.suggestion else None,
                view.suggestion.created_by if view.suggestion else None,
                view.suggestion.edited_by if view.suggestion else None,
            )
            if actor
        }
        gaps = _gaps(gaps_from, self._today())
        return BoardView(
            project_id=project_id,
            pod_id=pod_id,
            release_id=release_id,
            scope_name=scope_name,
            settings=settings,
            last_run=await self._repository.last_run(tenant_id),
            jira=await self._jira_health(tenant_id),
            writeback_on=await self._writeback_gate(tenant_id),
            findings=tuple(sorted(views, key=_row_order)),
            releases=releases,
            important=ReportGaps(gaps=gaps).lines,
            can_run=can_run and settings.enabled,
            names=await self._names(tenant_id, actors),
        )

    async def _views(
        self,
        tenant_id: str,
        scopes: Sequence[tuple[ScopeRef, str]],
        viewer: Viewer,
        *,
        has_releases: bool | None,
    ) -> list[FindingView]:
        names = {scope.key: name for scope, name in scopes}
        criteria = {
            item.criterion_id: item
            for item in await self._repository.list_criteria(tenant_id)
            if item.enabled
        }
        findings = [
            item
            for item in await self._repository.list_findings(tenant_id, [s for s, _ in scopes])
            if item.applies
            and item.criterion_id in criteria
            and viewer.sees(item.scope)
            and _expected_kind(criteria[item.criterion_id], item.scope.kind, has_releases)
        ]
        suggestions = {
            item.finding_id: item
            for item in await self._repository.list_suggestions(
                tenant_id, [item.finding_id for item in findings]
            )
        }
        settings = await self.settings(tenant_id)
        writeback = await self._writeback_gate(tenant_id)
        today = self._today()
        views: list[FindingView] = []
        for finding in findings:
            criterion = criteria[finding.criterion_id]
            suggestion = suggestions.get(finding.finding_id)
            views.append(
                FindingView(
                    finding=finding,
                    criterion=criterion,
                    scope_name=names.get(finding.scope.key, finding.scope.id),
                    suggestion=suggestion if viewer.sees_drafts else None,
                    actions=finding_actions(
                        viewer, finding, criterion, suggestion, settings, writeback
                    ),
                    working_days_left=(
                        working_days_left(today, finding.urgency.due_on)
                        if finding.urgency.due_on
                        else None
                    ),
                )
            )
        return views

    async def scope_of_finding(
        self, tenant_id: str, finding_id: str
    ) -> tuple[ScopeRef, str | None]:
        """A finding's scope and the project it belongs to (None for a pod), for reach."""
        finding = await self._finding(tenant_id, finding_id)
        return finding.scope, await self._project_of(tenant_id, finding.scope)

    async def scope_of_suggestion(
        self, tenant_id: str, suggestion_id: str
    ) -> tuple[ScopeRef, str | None]:
        suggestion = await self._suggestion(tenant_id, suggestion_id)
        return await self.scope_of_finding(tenant_id, suggestion.finding_id)

    async def pods_run_by(self, tenant_id: str, project_id: str, subject: str) -> list[str]:
        """The project's pods a scrum master runs, by the rule a pod's date uses."""
        today = self._today()
        return [
            pod.id
            for pod in await self._project_pods(tenant_id, project_id, today)
            if await self._forecast.runs_pod(tenant_id, pod.id, subject, today)
        ]

    async def _project_of(self, tenant_id: str, scope: ScopeRef) -> str | None:
        if scope.kind is ScopeKind.PROJECT:
            return scope.id
        if scope.kind is ScopeKind.RELEASE:
            return (await self._forecast.release(tenant_id, scope.id)).project_id
        return None

    async def history(self, tenant_id: str, finding_id: str) -> list[ReadinessAction]:
        await self._finding(tenant_id, finding_id)
        return await self._repository.list_actions(tenant_id, finding_id)

    async def actor_names(self, tenant_id: str, actors: Iterable[str]) -> dict[str, str]:
        return await self._names(tenant_id, {actor for actor in actors if actor})

    # ---- a person's actions ----------------------------------------------------------

    async def link(
        self,
        tenant_id: str,
        finding_id: str,
        viewer: Viewer,
        *,
        issue_key: str | None = None,
        url: str | None = None,
        note: str = "",
    ) -> FindingView:
        finding = await self._finding(tenant_id, finding_id)
        self._ensure_acts(viewer, finding)
        if bool(issue_key) == bool(url):
            raise ReadinessError("Link either a Jira issue or a record outside Jira.")
        if issue_key:
            known = await self._known(tenant_id)
            key = _known_key(issue_key, known)
            decision = PersonDecision(
                kind=DecisionKind.LINKED, by=viewer.subject, at=self._clock(), issue_key=key
            )
        else:
            record, kept_note = validated_record(url or "", note)
            decision = PersonDecision(
                kind=DecisionKind.LINKED,
                by=viewer.subject,
                at=self._clock(),
                url=record,
                note=kept_note,
            )
        return await self._decide(tenant_id, finding, decision, viewer, "linked")

    async def not_applicable(
        self, tenant_id: str, finding_id: str, viewer: Viewer, *, reason: str
    ) -> FindingView:
        finding = await self._finding(tenant_id, finding_id)
        self._ensure_acts(viewer, finding)
        criterion = await self._criterion(tenant_id, finding.criterion_id)
        if criterion.blocking and not viewer.waives_blocking:
            raise AuthorizationDenied(_WAIVE)
        decision = PersonDecision(
            kind=DecisionKind.NOT_APPLICABLE,
            by=viewer.subject,
            at=self._clock(),
            reason=validated_reason(reason),
        )
        return await self._decide(tenant_id, finding, decision, viewer, "not_applicable")

    async def reopen(self, tenant_id: str, finding_id: str, viewer: Viewer) -> FindingView:
        finding = await self._finding(tenant_id, finding_id)
        self._ensure_acts(viewer, finding)
        suggestion = await self._suggestion_of(tenant_id, finding.finding_id)
        dismissed = suggestion is not None and suggestion.status is SuggestionStatus.DISMISSED
        if finding.person is None and not dismissed:
            raise ReadinessConflict("nothing_to_reopen", "Nothing here was decided by a person.")
        if dismissed and suggestion is not None:
            await self._repository.save_suggestion(
                replace(
                    suggestion,
                    status=SuggestionStatus.OPEN,
                    version=suggestion.version + 1,
                    dismissed_by=None,
                    dismissed_at=None,
                    dismissed_reason=None,
                    updated_at=self._clock(),
                )
            )
        cleared = replace(finding, person=None, fingerprint="")
        await self._repository.save_finding(cleared)
        await self._audit(
            tenant_id,
            viewer.subject,
            "reopened",
            finding_id=finding.finding_id,
            suggestion_id=suggestion.suggestion_id if suggestion else None,
            criterion_id=finding.criterion_id,
            before=_person_json(finding.person) if finding.person else {"draft": "dismissed"},
        )
        return await self._rejudge(tenant_id, cleared, viewer)

    async def draft_now(self, tenant_id: str, finding_id: str, viewer: Viewer) -> FindingView:
        """Draft an issue on request, for a tenant that drafts none by itself."""
        finding = await self._finding(tenant_id, finding_id)
        self._ensure_acts(viewer, finding)
        if await self._suggestion_of(tenant_id, finding_id) is None:
            if finding.state is not FindingState.MISSING or finding.person is not None:
                raise ReadinessConflict("not_missing", "Only a missing criterion gets a draft.")
            criterion = await self._criterion(tenant_id, finding.criterion_id)
            context, _known = await self._context_of(tenant_id, finding.scope)
            await self._draft(
                tenant_id,
                criterion,
                context,
                finding,
                await self.settings(tenant_id),
                actor=viewer.subject,
            )
        return await self._view(tenant_id, finding.finding_id, viewer)

    async def edit_draft(
        self, tenant_id: str, suggestion_id: str, viewer: Viewer, *, version: int, draft: Draft
    ) -> FindingView:
        suggestion = await self._suggestion(tenant_id, suggestion_id)
        finding = await self._finding(tenant_id, suggestion.finding_id)
        self._ensure_acts(viewer, finding)
        self._ensure_open(suggestion, version)
        checked = validated_draft(draft)
        await self._repository.save_suggestion(
            replace(
                suggestion,
                draft=checked,
                version=suggestion.version + 1,
                edited_by=viewer.subject,
                updated_at=self._clock(),
            )
        )
        await self._audit(
            tenant_id,
            viewer.subject,
            "draft_edited",
            finding_id=finding.finding_id,
            suggestion_id=suggestion_id,
            criterion_id=finding.criterion_id,
            before={"version": suggestion.version, "summary": suggestion.draft.summary},
            after={"version": suggestion.version + 1, "summary": checked.summary},
        )
        return await self._view(tenant_id, finding.finding_id, viewer)

    async def dismiss(
        self, tenant_id: str, suggestion_id: str, viewer: Viewer, *, reason: str
    ) -> FindingView:
        suggestion = await self._suggestion(tenant_id, suggestion_id)
        finding = await self._finding(tenant_id, suggestion.finding_id)
        self._ensure_acts(viewer, finding)
        self._ensure_open(suggestion, None)
        kept = validated_reason(reason)
        now = self._clock()
        await self._repository.save_suggestion(
            replace(
                suggestion,
                status=SuggestionStatus.DISMISSED,
                dismissed_by=viewer.subject,
                dismissed_reason=kept,
                dismissed_at=now,
                updated_at=now,
            )
        )
        await self._audit(
            tenant_id,
            viewer.subject,
            "dismissed",
            finding_id=finding.finding_id,
            suggestion_id=suggestion_id,
            criterion_id=finding.criterion_id,
            reason=kept,
        )
        return await self._view(tenant_id, finding.finding_id, viewer)

    async def create(
        self, tenant_id: str, suggestion_id: str, viewer: Viewer, *, version: int
    ) -> CreateResult:
        """Create one approved draft in Jira, through every guardrail, in order."""
        suggestion = await self._suggestion(tenant_id, suggestion_id)
        finding = await self._finding(tenant_id, suggestion.finding_id)
        self._ensure_acts(viewer, finding)
        if suggestion.status is SuggestionStatus.CREATED and suggestion.created_issue_key:
            view = await self._view(tenant_id, finding.finding_id, viewer)
            return CreateResult(issue_key=suggestion.created_issue_key, created=False, finding=view)
        self._ensure_open(suggestion, version)
        settings = await self.settings(tenant_id)
        if not settings.create_in_jira:
            raise ReadinessConflict(
                "create_disabled", "Creating issues from OpenProgram is off for this tenant."
            )
        if not await self._writeback_gate(tenant_id):
            raise ReadinessConflict(
                "create_disabled", "Creating issues from OpenProgram needs Jira write-back on."
            )
        # The matching-issue hold: read the scope again before anything is posted.
        fresh = (await self._rejudge(tenant_id, finding, viewer)).finding
        if fresh.state is not FindingState.MISSING or fresh.person is not None:
            raise ReadinessConflict("not_missing", _now_covers(fresh))
        if not await self._repository.claim_suggestion(
            tenant_id, suggestion_id, version=version, at=self._clock()
        ):
            raise ReadinessConflict("creating", "It is being created; refresh in a moment.")
        await self._audit(
            tenant_id,
            viewer.subject,
            "create_requested",
            finding_id=finding.finding_id,
            suggestion_id=suggestion_id,
            criterion_id=finding.criterion_id,
            after={"version": version, "project_key": suggestion.draft.project_key},
        )
        try:
            key, created = await self._post(tenant_id, suggestion, finding, viewer)
        except Exception as exc:
            failure = (
                exc if isinstance(exc, IssueCreateFailed) else IssueCreateFailed("unreachable")
            )
            # The draft stays open: nothing was created, or a later try adopts it by its label.
            await self._repository.save_suggestion(
                replace(suggestion, status=SuggestionStatus.OPEN, updated_at=self._clock())
            )
            await self._audit(
                tenant_id,
                viewer.subject,
                "create_failed",
                finding_id=finding.finding_id,
                suggestion_id=suggestion_id,
                criterion_id=finding.criterion_id,
                after={"category": failure.category, "fields": list(failure.fields)},
            )
            if failure is exc:
                raise
            raise failure from exc
        now = self._clock()
        await self._repository.save_suggestion(
            replace(
                suggestion,
                status=SuggestionStatus.CREATED,
                created_issue_key=key,
                created_by=viewer.subject,
                created_at=now,
                updated_at=now,
            )
        )
        await self._audit(
            tenant_id,
            viewer.subject,
            "created" if created else "create_adopted",
            finding_id=finding.finding_id,
            suggestion_id=suggestion_id,
            criterion_id=finding.criterion_id,
            after={"issue_key": key},
        )
        decision = PersonDecision(
            kind=DecisionKind.LINKED, by=viewer.subject, at=now, issue_key=key, created=True
        )
        current = await self._finding(tenant_id, finding.finding_id)
        view = await self._decide(tenant_id, current, decision, viewer, None)
        return CreateResult(issue_key=key, created=created, finding=view)

    async def _post(
        self, tenant_id: str, suggestion: Suggestion, finding: Finding, viewer: Viewer
    ) -> tuple[str, bool]:
        """Adopt an issue an earlier try made (by its marker label), else create one."""
        try:
            found = await self._tracker.list_issues_for_query(
                tenant_id, f'labels = "{suggestion.marker_label}"', SyncCursor()
            )
        except IssueCreateFailed:
            raise
        except ProviderUnavailable as exc:
            raise IssueCreateFailed("unreachable") from exc
        if found:
            return found[0].key, False
        names = await self._names(tenant_id, {viewer.subject})
        creator = names.get(viewer.subject, viewer.subject)
        context, _known = await self._context_of(tenant_id, finding.scope)
        link = f"{self._console}{context.console_path}" if self._console else ""
        draft = suggestion.draft
        labels = tuple(dict.fromkeys((*draft.labels, suggestion.marker_label)))
        description = "\n\n".join(
            part for part in (draft.description.strip(), created_footer(creator, link)) if part
        )
        key = await self._tracker.create_issue(
            tenant_id,
            NewIssue(
                project_key=draft.project_key,
                issue_type=draft.issue_type,
                summary=draft.summary,
                description=description,
                labels=labels,
                reporter_account_id=await self._tracker_account(tenant_id, viewer.subject),
            ),
        )
        await self._record_created(tenant_id, key, draft, labels)
        return key, True

    async def _record_created(
        self, tenant_id: str, key: str, draft: Draft, labels: tuple[str, ...]
    ) -> None:
        """Store the new issue as the next sync will, so the board need not wait for it."""
        try:
            issue = await self._tracker.get_issue(tenant_id, key)
        except ProviderUnavailable:
            issue = Issue(
                tenant_id=tenant_id,
                key=key,
                title=draft.summary,
                state=IssueState.TODO,
                metadata={
                    "project_key": draft.project_key,
                    "issue_type": draft.issue_type,
                    "labels": ", ".join(labels),
                    "status": "To Do",
                },
            )
        try:
            await record_issue_as_synced(
                issue,
                graph_repository=self._graph,
                time_series_repository=self._facts,
                identity_link_repository=self._identity_links,
                observed_at=self._clock(),
            )
        except Exception as exc:  # noqa: BLE001 - the issue exists; the next sync records it
            _log_failure(tenant_id, exc)

    async def _decide(
        self,
        tenant_id: str,
        finding: Finding,
        decision: PersonDecision,
        viewer: Viewer,
        action: str | None,
    ) -> FindingView:
        decided = replace(finding, person=decision, fingerprint="")
        await self._repository.save_finding(decided)
        if action is not None:
            await self._audit(
                tenant_id,
                viewer.subject,
                action,
                finding_id=finding.finding_id,
                criterion_id=finding.criterion_id,
                before=_person_json(finding.person) if finding.person else None,
                after=_person_json(decision),
                reason=decision.reason or None,
            )
        return await self._rejudge(tenant_id, decided, viewer)

    async def _rejudge(self, tenant_id: str, finding: Finding, viewer: Viewer) -> FindingView:
        """Evaluate one finding again on fresh synced data, and return it as the viewer sees it."""
        criterion = await self._criterion(tenant_id, finding.criterion_id)
        context, known = await self._context_of(tenant_id, finding.scope)
        settings = await self.settings(tenant_id)
        await self._judge(
            tenant_id,
            criterion,
            context,
            known,
            existing=finding,
            suggestion=await self._suggestion_of(tenant_id, finding.finding_id),
            settings=settings,
            jira=await self._jira_health(tenant_id),
            run_id=None,
            today=self._today(),
            actor=viewer.subject,
        )
        return await self._view(tenant_id, finding.finding_id, viewer)

    async def _view(self, tenant_id: str, finding_id: str, viewer: Viewer) -> FindingView:
        finding = await self._finding(tenant_id, finding_id)
        name = await self._scope_name(tenant_id, finding.scope)
        views = await self._views(tenant_id, [(finding.scope, name)], viewer, has_releases=None)
        match = next((view for view in views if view.finding.finding_id == finding_id), None)
        if match is not None:
            return match
        criterion = await self._criterion(tenant_id, finding.criterion_id)
        suggestion = await self._suggestion_of(tenant_id, finding_id)
        return FindingView(
            finding=finding,
            criterion=criterion,
            scope_name=name,
            suggestion=suggestion if viewer.sees_drafts else None,
            actions=finding_actions(
                viewer,
                finding,
                criterion,
                suggestion,
                await self.settings(tenant_id),
                await self._writeback_gate(tenant_id),
            ),
            working_days_left=(
                working_days_left(self._today(), finding.urgency.due_on)
                if finding.urgency.due_on
                else None
            ),
        )

    # ---- the day report --------------------------------------------------------------

    async def report_gaps(
        self, tenant_id: str, project_id: str, release: Release | None
    ) -> ReportGaps:
        """The blocking gaps a project's (or a release's) day report raises.

        Missing or unsure, no person's decision, not held on stale data, and due
        soon, overdue or with the stage reached. Nothing at all while the agent is
        off or the tenant has no criteria, so such a report is what it always was.
        """
        stored = await self._repository.get_settings(tenant_id)
        if stored is None or not stored.enabled:
            return ReportGaps()
        criteria = {
            item.criterion_id: item
            for item in await self._repository.list_criteria(tenant_id)
            if item.enabled
        }
        if not criteria:
            return ReportGaps()
        project = await self._graph.get_node(tenant_id, project_id)
        if project is None:
            return ReportGaps()
        releases = await self._forecast.releases(tenant_id, project_id)
        scopes: list[tuple[ScopeRef, str]] = [
            (ScopeRef(kind=ScopeKind.PROJECT, id=project_id), project.name)
        ]
        if release is not None:
            scopes.append((ScopeRef(kind=ScopeKind.RELEASE, id=release.release_id), release.name))
        else:
            scopes.extend(
                (ScopeRef(kind=ScopeKind.RELEASE, id=item.release_id), item.name)
                for item in releases
            )
            scopes.extend(
                (ScopeRef(kind=ScopeKind.POD, id=pod.id), pod.name)
                for pod in await self._project_pods(tenant_id, project_id, self._today())
            )
        reader = Viewer(subject=AGENT, roles=frozenset(), may_act=False, sees_drafts=False)
        views = await self._views(tenant_id, scopes, reader, has_releases=bool(releases))
        return ReportGaps(gaps=_gaps(views, self._today()))

    # ---- reading scopes --------------------------------------------------------------

    async def _project_contexts(
        self, tenant_id: str, project_id: str, today: date
    ) -> tuple[list[ScopeContext], dict[str, ScopeIssue]]:
        """The project's scopes: each release (or the project itself while it has none,
        and always for project-wide criteria) and each pod working on it."""
        project = await self._graph.get_node(tenant_id, project_id)
        if project is None or project.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"No project {project_id!r}.")
        mapping = (await self._delivery.delivery_settings(tenant_id)).settings.mapping
        tasks = await self._graph.list_nodes(tenant_id, NodeKind.TASK)
        titles = {_key(task): task.name for task in tasks}
        known = {_key(task): _scope_issue(task, mapping, titles) for task in tasks}
        owned = (await owned_project_tasks(self._graph, tenant_id, [project_id], today)).get(
            project_id, ()
        )
        delivery = await self._forecast.project_delivery(tenant_id, project_id, today)
        releases = await self._forecast.releases(tenant_id, project_id)
        key = _text(project.metadata.get("jira_project_key")) or ""
        path = f"/reports/{project_id}/overall"
        contexts = [
            ScopeContext(
                scope=ScopeRef(kind=ScopeKind.PROJECT, id=project_id),
                name=project.name,
                project_id=project_id,
                project_name=project.name,
                project_key=key,
                issues=tuple(known[_key(task)] for task in owned),
                stages=_stages(owned, mapping),
                target=delivery.project.target,
                console_path=f"{path}#readiness",
                has_releases=bool(releases),
            )
        ]
        targets = {view.scope.id: view.target for view in delivery.releases}
        for release in releases:
            included = [task for task in owned if release.includes(task.metadata)]
            contexts.append(
                ScopeContext(
                    scope=ScopeRef(kind=ScopeKind.RELEASE, id=release.release_id),
                    name=release.name,
                    project_id=project_id,
                    project_name=project.name,
                    project_key=key,
                    release_name=release.name,
                    issues=tuple(known[_key(task)] for task in included),
                    stages=_stages(included, mapping),
                    target=targets.get(release.release_id),
                    console_path=f"{path}?release={release.release_id}#readiness",
                )
            )
        by_id = {task.id: task for task in tasks}
        for pod in delivery.pods:
            ids = await self._pod_task_ids(tenant_id, pod.scope.id, today)
            mine = [by_id[task_id] for task_id in ids if task_id in by_id]
            contexts.append(
                ScopeContext(
                    scope=ScopeRef(kind=ScopeKind.POD, id=pod.scope.id),
                    name=pod.name,
                    project_id=None,
                    project_name=project.name,
                    project_key=key,
                    pod_name=pod.name,
                    issues=tuple(known[_key(task)] for task in mine),
                    stages=_stages(mine, mapping),
                    target=pod.target,
                    console_path=f"{path}#readiness",
                )
            )
        return contexts, known

    async def _pod_context(
        self, tenant_id: str, pod_id: str, today: date
    ) -> tuple[ScopeContext, dict[str, ScopeIssue]]:
        pod = await self._graph.get_node(tenant_id, pod_id)
        if pod is None or pod.kind is not NodeKind.POD:
            raise GraphNotFound(f"No pod {pod_id!r}.")
        found: ScopeContext | None = None
        known: dict[str, ScopeIssue] = {}
        for project in await self._forecast.pod_projects(tenant_id, pod_id, today):
            contexts, known = await self._project_contexts(tenant_id, project.id, today)
            for context in contexts:
                if context.scope.kind is ScopeKind.POD and context.scope.id == pod_id:
                    found = _earlier(found, context)
        if found is None:
            mapping = (await self._delivery.delivery_settings(tenant_id)).settings.mapping
            tasks = await self._graph.list_nodes(tenant_id, NodeKind.TASK)
            titles = {_key(task): task.name for task in tasks}
            known = {_key(task): _scope_issue(task, mapping, titles) for task in tasks}
            by_id = {task.id: task for task in tasks}
            mine = [
                by_id[i] for i in await self._pod_task_ids(tenant_id, pod_id, today) if i in by_id
            ]
            found = ScopeContext(
                scope=ScopeRef(kind=ScopeKind.POD, id=pod_id),
                name=pod.name,
                project_id=None,
                pod_name=pod.name,
                issues=tuple(known[_key(task)] for task in mine),
                stages=_stages(mine, mapping),
            )
        return found, known

    async def _context_of(
        self, tenant_id: str, scope: ScopeRef
    ) -> tuple[ScopeContext, dict[str, ScopeIssue]]:
        today = self._today()
        if scope.kind is ScopeKind.POD:
            return await self._pod_context(tenant_id, scope.id, today)
        project_id = scope.id
        if scope.kind is ScopeKind.RELEASE:
            project_id = (await self._forecast.release(tenant_id, scope.id)).project_id
        contexts, known = await self._project_contexts(tenant_id, project_id, today)
        context = next((item for item in contexts if item.scope == scope), None)
        if context is None:
            raise GraphNotFound(f"No {scope.kind.value} {scope.id!r}.")
        return context, known

    async def _project_pods(self, tenant_id: str, project_id: str, today: date) -> list[GraphNode]:
        pods: list[GraphNode] = []
        for edge in await self._graph.list_edges(
            tenant_id, from_node_id=project_id, kind=EdgeKind.CONTAINS
        ):
            if not edge.is_active_on(today):
                continue
            node = await self._graph.get_node(tenant_id, edge.to_node_id)
            if node is not None and node.kind is NodeKind.POD:
                pods.append(node)
        return sorted(pods, key=lambda node: node.name.casefold())

    async def _scope_name(self, tenant_id: str, scope: ScopeRef) -> str:
        if scope.kind is ScopeKind.RELEASE:
            try:
                return (await self._forecast.release(tenant_id, scope.id)).name
            except GraphNotFound:
                return scope.id
        node = await self._graph.get_node(tenant_id, scope.id)
        return node.name if node is not None else scope.id

    async def _known(self, tenant_id: str) -> dict[str, ScopeIssue]:
        mapping = (await self._delivery.delivery_settings(tenant_id)).settings.mapping
        tasks = await self._graph.list_nodes(tenant_id, NodeKind.TASK)
        titles = {_key(task): task.name for task in tasks}
        return {_key(task): _scope_issue(task, mapping, titles) for task in tasks}

    async def _names(self, tenant_id: str, actors: set[str]) -> dict[str, str]:
        if not actors:
            return {}
        return {
            node.id: node.name
            for node in await self._graph.list_nodes(tenant_id, NodeKind.DEVELOPER)
            if node.id in actors
        }

    async def _tracker_account(self, tenant_id: str, member_id: str) -> str | None:
        if self._identity_links is None:
            return None
        link = await self._identity_links.get_identity_link(tenant_id, member_id)
        return link.jira_account_id if link is not None and link.jira_account_id else None

    # ---- small reads and checks ------------------------------------------------------

    async def _enabled(self, tenant_id: str) -> ReadinessSettings:
        settings = await self.settings(tenant_id)
        if not settings.enabled:
            raise ReadinessConflict("agent_off", "The readiness agent is off for this tenant.")
        return settings

    async def _criterion(self, tenant_id: str, criterion_id: str) -> ReleaseCriterion:
        criterion = next(
            (
                item
                for item in await self._repository.list_criteria(tenant_id)
                if item.criterion_id == criterion_id
            ),
            None,
        )
        if criterion is None:
            raise GraphNotFound(f"No criterion {criterion_id!r}.")
        return criterion

    async def _finding(self, tenant_id: str, finding_id: str) -> Finding:
        finding = await self._repository.get_finding(tenant_id, finding_id)
        if finding is None:
            raise GraphNotFound(f"No finding {finding_id!r}.")
        return finding

    async def _suggestion(self, tenant_id: str, suggestion_id: str) -> Suggestion:
        suggestion = await self._repository.get_suggestion(tenant_id, suggestion_id)
        if suggestion is None:
            raise GraphNotFound(f"No draft {suggestion_id!r}.")
        return suggestion

    async def _suggestion_of(self, tenant_id: str, finding_id: str) -> Suggestion | None:
        found = await self._repository.list_suggestions(tenant_id, [finding_id])
        return found[0] if found else None

    def _ensure_acts(self, viewer: Viewer, finding: Finding) -> None:
        if not viewer.acts_on(finding.scope):
            raise AuthorizationDenied(_NOT_YOURS)

    def _ensure_open(self, suggestion: Suggestion, version: int | None) -> None:
        if suggestion.status is SuggestionStatus.DISMISSED:
            raise ReadinessConflict("dismissed", "This draft was dismissed; reopen it first.")
        if suggestion.status is SuggestionStatus.CREATING:
            raise ReadinessConflict("creating", "It is being created; refresh in a moment.")
        if suggestion.status is not SuggestionStatus.OPEN:
            raise ReadinessConflict("created", "This draft is already in Jira.")
        if version is not None and version != suggestion.version:
            raise ReadinessConflict(
                "stale_version", "The draft changed since you opened it; check it again."
            )

    def _new_run(
        self,
        tenant_id: str,
        slot: str,
        trigger: RunTrigger,
        scope: ScopeRef | None,
        actor: str | None,
    ) -> ReadinessRun:
        return ReadinessRun(
            tenant_id=tenant_id,
            run_id=f"rr_{self._new_id()}",
            slot=slot,
            trigger=trigger,
            status=RunStatus.RUNNING,
            started_at=self._clock(),
            scope=scope,
            actor=actor,
        )

    async def _finish(
        self,
        run: ReadinessRun,
        summary: RunSummary,
        jira: JiraData | None,
        *,
        error: str | None = None,
    ) -> None:
        await self._repository.finish_run(
            replace(
                run,
                status=summary.status,
                finished_at=self._clock(),
                counts={
                    "scopes": summary.scopes,
                    "changed": summary.changed,
                    "missing": summary.missing,
                    "unsure": summary.unsure,
                    "covered": summary.covered,
                },
                data_as_of=jira.as_of if jira else None,
                error_category=error,
            )
        )

    async def _audit(
        self,
        tenant_id: str,
        actor: str,
        action: str,
        *,
        finding_id: str | None = None,
        suggestion_id: str | None = None,
        criterion_id: str | None = None,
        before: Mapping[str, object] | None = None,
        after: Mapping[str, object] | None = None,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        await self._repository.append_action(
            ReadinessAction(
                tenant_id=tenant_id,
                action_id=f"ra_{self._new_id()}",
                at=self._clock(),
                actor=actor,
                action=action,
                finding_id=finding_id,
                suggestion_id=suggestion_id,
                criterion_id=criterion_id,
                before=before,
                after=after,
                reason=reason,
                correlation_id=correlation_id,
            )
        )


# ---- pure helpers ---------------------------------------------------------------------


def finding_actions(
    viewer: Viewer,
    finding: Finding,
    criterion: ReleaseCriterion,
    suggestion: Suggestion | None,
    settings: ReadinessSettings,
    writeback_on: bool,
) -> FindingActions:
    """What this viewer may do with this finding now, worked out once on the server."""
    act = viewer.acts_on(finding.scope)
    if not act:
        return FindingActions()
    undecided = finding.person is None
    missing = undecided and finding.state is FindingState.MISSING
    draft_open = (
        viewer.sees_drafts and suggestion is not None and suggestion.status is SuggestionStatus.OPEN
    )
    off: str | None = None
    if missing and draft_open:
        if not settings.create_in_jira:
            off = "Creating issues from OpenProgram is off for this tenant."
        elif not writeback_on:
            off = "Creating issues from OpenProgram needs Jira write-back on."
    dismissed = suggestion is not None and suggestion.status is SuggestionStatus.DISMISSED
    return FindingActions(
        create=missing and draft_open and off is None,
        create_off_reason=off,
        link=undecided,
        not_applicable=undecided and (not criterion.blocking or viewer.waives_blocking),
        dismiss=missing and draft_open,
        edit=missing and draft_open,
        reopen=not undecided or dismissed,
        draft=missing and suggestion is None and not settings.auto_suggest,
    )


def _gaps(views: Sequence[FindingView], today: date) -> tuple[ReadinessGap, ...]:
    gaps: list[tuple[tuple[int, date, str], ReadinessGap]] = []
    for view in views:
        finding, criterion = view.finding, view.criterion
        if (
            not criterion.blocking
            or finding.person is not None
            or finding.held
            or not finding.applies
            or finding.state is FindingState.COVERED
            or finding.urgency.kind not in URGENT
        ):
            continue
        waited = (
            max(0, (today - finding.window_entered_at.date()).days)
            if finding.window_entered_at
            else None
        )
        gap = ReadinessGap(
            finding_id=finding.finding_id,
            scope=finding.scope,
            line=gap_line(view, today),
            ask=gap_ask(view),
            waited_days=waited,
            urgency=finding.urgency.kind,
            entered_on=finding.window_entered_at.date() if finding.window_entered_at else None,
        )
        order = (
            _URGENCY_ORDER[finding.urgency.kind],
            finding.urgency.due_on or date.max,
            criterion.name.casefold(),
        )
        gaps.append((order, gap))
    return tuple(gap for _order, gap in sorted(gaps, key=lambda item: item[0]))


def gap_line(view: FindingView, today: date) -> str:
    """Most important's line: about the scope and the work, never a person.

    "Release 1 needs a security review within 8 working days, and none is in Jira."
    "CHK-44 reached production without a load test for Release 1."
    """
    finding, criterion = view.finding, view.criterion
    what = with_article(criterion.name)
    urgency = finding.urgency
    scope = view.scope_name
    stage = stage_words(criterion.required_before)
    if urgency.kind is UrgencyKind.STAGE_REACHED and urgency.stage_key:
        return f"{urgency.stage_key} reached {stage} without {what} for {scope}."
    tail = (
        "and none is in Jira"
        if finding.state is FindingState.MISSING
        else "and Jira has only a possible match: "
        + ", ".join(item.issue_key for item in finding.candidates[:3])
    )
    if urgency.kind is UrgencyKind.OVERDUE and urgency.due_on:
        return f"{scope} needed {what} by {_day(urgency.due_on)}, {tail}."
    if urgency.due_on is None:
        return f"{scope} needs {what} before {stage}, {tail}."
    left = working_days_left(today, urgency.due_on)
    when = "by today" if left <= 0 else f"within {left} working {'day' if left == 1 else 'days'}"
    return f"{scope} needs {what} {when}, {tail}."


def gap_ask(view: FindingView) -> str:
    """The decision ask: "Release 1: no security review in Jira yet (due 21 Oct); create it or
    link one"."""
    finding, criterion = view.finding, view.criterion
    words = name_in_sentence(criterion.name)
    due = f" (due {_day(finding.urgency.due_on)})" if finding.urgency.due_on else ""
    if finding.state is FindingState.UNSURE and finding.candidates:
        key = finding.candidates[0].issue_key
        return f"{view.scope_name}: is {key} the {words}{due}? Link it, or create one"
    return f"{view.scope_name}: no {words} in Jira yet{due}; create it or link one"


def _now_covers(finding: Finding) -> str:
    if finding.evidence and finding.evidence[0].issue_key:
        return f"{finding.evidence[0].issue_key} now covers it; link it instead."
    if finding.candidates:
        return f"{finding.candidates[0].issue_key} may cover it; link it instead."
    return "It is no longer missing; check the board again."


def create_failure_words(exc: IssueCreateFailed) -> str:
    """A failed create, in a fixed sentence: never the tracker's own text."""
    if exc.category == "credentials":
        return (
            "Jira refused OpenProgram's sign-in, so nothing was created. Check the Jira connection."
        )
    if exc.category == "refused":
        fields = [_FIELD_WORDS.get(name, "a required field") for name in exc.fields]
        named = ", ".join(dict.fromkeys(fields)) or "the draft"
        return f"Jira refused it: check {named}. Nothing was created."
    return "Jira didn't answer; nothing was created. Try again."


def _urgency(
    criterion: ReleaseCriterion,
    evaluation: Evaluation,
    person: PersonDecision | None,
    context: ScopeContext,
    today: date,
) -> Urgency:
    return urgency_of(
        criterion,
        ready=is_ready(criterion, evaluation, person),
        target=context.target,
        stages=context.stages,
        today=today,
    )


def _expected(criterion: ReleaseCriterion, context: ScopeContext) -> bool:
    """Whether the criterion is judged on this scope: each release, or the project
    while it has no releases; each project; each pod."""
    return _expected_kind(criterion, context.scope.kind, context.has_releases)


def _expected_kind(criterion: ReleaseCriterion, kind: ScopeKind, has_releases: bool | None) -> bool:
    """``has_releases`` None takes a project-wide finding of a release criterion as it is."""
    if criterion.applies_to is AppliesTo.POD:
        return kind is ScopeKind.POD
    if criterion.applies_to is AppliesTo.PROJECT:
        return kind is ScopeKind.PROJECT
    if kind is ScopeKind.RELEASE:
        return True
    return kind is ScopeKind.PROJECT and not has_releases


def _row_order(view: FindingView) -> tuple[int, int, int, int, str]:
    finding = view.finding
    state_rank = {
        "missing": 0,
        "unsure": 1,
        "covered": 2,
        "not_applicable": 4,
    }[view.shown_state]
    if view.shown_state == "covered" and finding.done:
        state_rank = 3
    return (
        {ScopeKind.RELEASE: 0, ScopeKind.PROJECT: 1, ScopeKind.POD: 2}[finding.scope.kind],
        0 if view.criterion.blocking else 1,
        state_rank,
        _URGENCY_ORDER[finding.urgency.kind],
        f"{view.scope_name.casefold()}|{view.criterion.name.casefold()}",
    )


def _earlier(current: ScopeContext | None, other: ScopeContext) -> ScopeContext:
    """A pod in two projects is judged once, against its earliest date."""
    if current is None:
        return other
    if other.target is not None and (current.target is None or other.target < current.target):
        return other
    return current


def _stages(
    tasks: Iterable[GraphNode], mapping: StageMapping
) -> tuple[tuple[str, DeliveryStage], ...]:
    stages: list[tuple[str, DeliveryStage]] = []
    for task in tasks:
        if not mapping.counts_type(_text(task.metadata.get("issue_type"))):
            continue
        placement = place(
            mapping,
            status=_text(task.metadata.get("status")),
            state=_text(task.metadata.get("state")),
        )
        if placement.stage is not None:
            stages.append((_key(task), placement.stage))
    return tuple(stages)


def _scope_issue(task: GraphNode, mapping: StageMapping, titles: Mapping[str, str]) -> ScopeIssue:
    status = _text(task.metadata.get("status"))
    state = _text(task.metadata.get("state"))
    placement = place(mapping, status=status, state=state)
    parent = _text(task.metadata.get("parent_key"))
    updated = task.metadata.get("updated_at")
    return ScopeIssue(
        key=_key(task),
        title=task.name,
        issue_type=_text(task.metadata.get("issue_type")),
        labels=split_names(task.metadata.get("labels")),
        parent_key=parent,
        parent_title=titles.get(parent) if parent else None,
        status=status,
        done=state == "done" or placement.stage is DeliveryStage.PRODUCTION,
        counted=placement.stage is not None,
        updated_at=updated if isinstance(updated, str) else None,
    )


def _known_key(issue_key: str, known: Mapping[str, ScopeIssue]) -> str:
    wanted = " ".join(issue_key.split())
    if not wanted or len(wanted) > 80:
        raise ReadinessError("An issue key looks like CHK-12.")
    found = next((key for key in known if key.casefold() == wanted.casefold()), None)
    if found is None:
        raise ReadinessError(f"{wanted} is not among Jira's synced issues.")
    return found


def _key(task: GraphNode) -> str:
    key = task.metadata.get("key")
    return key if isinstance(key, str) and key else task.id


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _day(day: date) -> str:
    return f"{day.day} {day:%b}"


def _settings_json(settings: ReadinessSettings) -> dict[str, object]:
    return {
        "enabled": settings.enabled,
        "auto_suggest": settings.auto_suggest,
        "create_in_jira": settings.create_in_jira,
        "issue_type": settings.issue_type,
        "labels": list(settings.labels),
    }


def _finding_json(finding: Finding) -> dict[str, object]:
    return {
        "state": finding.state.value,
        "done": finding.done,
        "urgency": finding.urgency.kind.value,
        "due_on": finding.urgency.due_on.isoformat() if finding.urgency.due_on else None,
        "evidence": [item.issue_key or item.url for item in finding.evidence],
        "candidates": [item.issue_key for item in finding.candidates],
        "held": finding.held,
        "applies": finding.applies,
        "decided_by": finding.decided_by.value,
        "person": finding.person.kind.value if finding.person else None,
    }


def _person_json(person: PersonDecision) -> dict[str, object]:
    return {
        "kind": person.kind.value,
        "issue_key": person.issue_key or None,
        "url": person.url or None,
        "reason": person.reason or None,
        "created": person.created,
    }


def _log_failure(tenant_id: str, exc: BaseException) -> None:
    # Imported here: the workflow definitions reach this module, and the
    # workflow sandbox refuses structlog's import-time randomness.
    import structlog

    structlog.get_logger(__name__).warning(
        "release_readiness_failed", tenant_id=tenant_id, error_type=type(exc).__name__
    )


__all__ = [
    "AGENT",
    "BoardView",
    "ConfigView",
    "CreateResult",
    "DecidedBy",
    "FindingActions",
    "FindingView",
    "JiraData",
    "PreviewRow",
    "ReadinessConflict",
    "ReadinessGap",
    "ReleaseLine",
    "ReleaseReadinessService",
    "ReportGaps",
    "RunSummary",
    "ScopeContext",
    "Viewer",
    "create_failure_words",
    "finding_actions",
    "gap_ask",
    "gap_line",
]
