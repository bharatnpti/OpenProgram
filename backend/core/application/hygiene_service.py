"""Jira hygiene: each scope's issues checked against the tenant's rules, and what people do.

A run reads only what OpenProgram already holds: the synced issues of each
project, release and pod (the sets release readiness builds), their history
facts, the planning baseline, the gates' scan and the pods' people. It never
calls Jira, and it never writes to Jira. While the issue sync is failing or
stale a finding never closes, and a new finding of an asking rule is held out
of the day report until fresh data confirms it. A finding whose fingerprint
did not change is not written and not audited.

Each finding attaches to one issue and names one owner: the rule's (the
assignee, the reporter, a profile's holder in the issue's pod, the pod's scrum
master or whoever decides), else the pod's scrum master, else the decider.
Nothing here counts or ranks people: an executive reads counts per rule only,
never who, and nothing keeps Jira text beyond keys, titles, field names and
dates. Every person's action and every change the scan makes is audited.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from core.application.baseline_service import BaselineService
from core.application.checkin_drift import member_roles
from core.application.delivery_scope import PROJECT_OWNER_KEY
from core.application.escalation_matrix_service import EscalationMatrixService
from core.application.forecast_service import ForecastService
from core.application.gate_service import GateService
from core.application.profile_service import ProfileService
from core.application.release_readiness_service import (
    JiraData,
    ReleaseReadinessService,
    ScopeContext,
    Viewer,
)
from core.application.sync_services import ISSUE_CHANGE_FACT_SOURCE, ISSUE_FACT_SOURCE
from core.domain.auth import Role
from core.domain.baseline import BaselineScopeKind
from core.domain.delivery import STAGE_ORDER, DeliveryStage
from core.domain.errors import AuthorizationDenied, GraphNotFound, OpenProgramError
from core.domain.escalation import escalation_contacts_from_metadata
from core.domain.forecast import Release
from core.domain.graph import EdgeKind, EntityRef, GraphNode, NodeKind
from core.domain.hygiene import (
    DECIDED,
    MAX_RULES,
    WAIVE_ROLES,
    BaselineFacts,
    Breach,
    HygieneAction,
    HygieneError,
    HygieneFinding,
    HygieneRule,
    HygieneRun,
    HygieneSettings,
    HygieneState,
    IssueContext,
    OwnerKind,
    PersonDecision,
    Resolution,
    RuleNeed,
    count_line,
    default_examples,
    evaluate_rule,
    finding_fingerprint,
    fix_values,
    needs_baseline,
    needs_gates,
    needs_history,
    render_fix,
    rule_slug,
    rule_words,
    selected,
    validated_reason,
    validated_rule,
    validated_settings,
)
from core.domain.hygiene import ask_text as hygiene_ask_text
from core.domain.integrations import IssueChangeField
from core.domain.release_readiness import (
    RunStatus,
    RunTrigger,
    ScopeIssue,
    ScopeKind,
    ScopeRef,
)
from core.domain.status import local_date
from core.ports.hygiene import HygieneRepository
from core.ports.repositories import GraphRepository, TimeSeriesRepository

AGENT = "agent"
#: How many issues "Try on a project" lists per scope.
MAX_PREVIEW_ISSUES = 25
#: The board's findings, at most; the rule rows still count every one.
MAX_BOARD_FINDINGS = 300

_NOT_YOURS = "You can act on Jira hygiene only for pods you run and the projects they work on."
_WAIVE = "Only a manager or an admin marks a finding of a blocking rule not applicable."
_SHOWN = frozenset({HygieneState.OPEN, *DECIDED})
#: The history changes that can bring an issue into a scope after planning.
_JOINING_FIELDS = frozenset(
    {
        IssueChangeField.FIX_VERSION.value,
        IssueChangeField.ISSUE_TYPE.value,
        IssueChangeField.PARENT.value,
        IssueChangeField.STATUS.value,
    }
)


class HygieneConflict(OpenProgramError):
    """The action cannot be taken in the finding's current state (409)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


JiraHealth = Callable[[str], Awaitable[JiraData]]


def reads_counts_only(viewer: Viewer) -> bool:
    """An executive reads counts per rule, never who: readiness_access gives them no
    drafts, no pods and nothing to act on."""
    return not viewer.sees_drafts


# ---- views --------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ConfigView:
    settings: HygieneSettings
    is_default: bool
    rules: tuple[HygieneRule, ...]
    examples: tuple[HygieneRule, ...]
    #: The gate item kinds a rule may look for, (key, label), from the gates in force.
    gate_kinds: tuple[tuple[str, str], ...]


@dataclass(frozen=True, kw_only=True)
class PreviewIssue:
    key: str
    title: str
    what: str
    owner_name: str | None
    suggested_fix: str


@dataclass(frozen=True, kw_only=True)
class PreviewScope:
    scope: ScopeRef
    scope_name: str
    #: The issues the rule looks at here.
    looked_at: int
    #: How many of them break it.
    found: int
    issues: tuple[PreviewIssue, ...]
    #: The rule needs a planning baseline and the scope has none.
    needs_baseline: bool = False


@dataclass(frozen=True, kw_only=True)
class RunSummary:
    status: RunStatus
    scopes: int = 0
    opened: int = 0
    resolved: int = 0
    changed: int = 0
    open: int = 0


@dataclass(frozen=True, kw_only=True)
class FindingActions:
    waive: bool = False
    not_applicable: bool = False
    recheck: bool = False
    reopen: bool = False


@dataclass(frozen=True, kw_only=True)
class FindingView:
    finding: HygieneFinding
    rule: HygieneRule
    title: str
    scope_name: str
    owner_name: str | None
    #: The profile's label, when the owner was found by a profile.
    owner_profile_label: str | None
    suggested_fix: str
    open_days: int
    actions: FindingActions
    decided_by_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class RuleRow:
    rule: HygieneRule
    #: The rule stated first, in words.
    stated: str
    open: int
    new_today: int
    #: Days the oldest open finding has been open; None with none open.
    oldest_days: int | None
    held: int
    decided: int


@dataclass(frozen=True, kw_only=True)
class BoardView:
    project_id: str | None
    pod_id: str | None
    release_id: str | None
    scope_name: str
    settings: HygieneSettings
    last_run: HygieneRun | None
    jira: JiraData
    counts_only: bool
    rules: tuple[RuleRow, ...]
    findings: tuple[FindingView, ...]
    #: The pods and releases a filter may pick, (id, name).
    pods: tuple[tuple[str, str], ...] = ()
    releases: tuple[tuple[str, str], ...] = ()
    can_run: bool = False

    @property
    def open(self) -> int:
        return sum(row.open for row in self.rules)

    @property
    def held(self) -> int:
        return sum(row.held for row in self.rules)


@dataclass(frozen=True, kw_only=True)
class HygieneAsk:
    """One ask of an asking rule: one owner, one rule, one scope, up to five keys named."""

    rule_id: str
    need: RuleNeed
    owner_member_id: str | None
    scope: ScopeRef
    #: The pod it climbs the escalation matrix from, when one is known.
    pod_id: str | None
    keys: tuple[str, ...]
    waits_from: date
    text: str


@dataclass(frozen=True, kw_only=True)
class HygieneReport:
    """What a project's (or a release's) day report says of Jira hygiene."""

    enabled: bool = False
    open: int = 0
    new_today: int = 0
    held: int = 0
    asks: tuple[HygieneAsk, ...] = ()

    @property
    def line(self) -> str | None:
        """The count line, while the scan is on: "Jira hygiene: 7 open (2 new today)."."""
        return count_line(self.open, self.new_today) if self.enabled else None


# ---- what one pass reads ------------------------------------------------------------------


@dataclass
class _People:
    """The tenant's members and pods' memberships, read once per pass."""

    names: dict[str, str]
    roles: dict[str, frozenset[str]]
    chat_ids: dict[str, str]
    #: Each pod's members in force today.
    members: dict[str, set[str]]


@dataclass(frozen=True, kw_only=True)
class _Org:
    """Who is who for one project's findings."""

    names: Mapping[str, str]
    profiles: Mapping[str, Mapping[str, str]]
    profile_labels: Mapping[str, str]
    scrum_masters: Mapping[str, str]
    decider: str | None
    #: The project's pods, by name.
    pods: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class _Scopes:
    """One project's scopes and what each issue belongs to."""

    project: GraphNode | None
    contexts: tuple[ScopeContext, ...]
    pods_of_key: Mapping[str, tuple[str, ...]]
    releases_of_key: Mapping[str, tuple[str, ...]]
    org: _Org


@dataclass
class _Pass:
    tenant_id: str
    today: date
    settings: HygieneSettings
    jira: JiraData
    run_id: str | None
    actor: str = AGENT
    people: _People | None = None
    history: dict[str, tuple[list[tuple[date, str]], list[date]]] = field(default_factory=dict)
    gates: dict[str, tuple[bool, frozenset[str]]] = field(default_factory=dict)
    baselines: dict[tuple[str, str], BaselineFacts | None] = field(default_factory=dict)
    gate_labels: dict[str, str] = field(default_factory=dict)
    #: Each gate's guarded stage, its kinds and its issue types.
    gate_templates: list[tuple[DeliveryStage, frozenset[str], frozenset[str]]] = field(
        default_factory=list
    )
    opened: int = 0
    resolved: int = 0
    changed: int = 0
    judged: list[ScopeRef] = field(default_factory=list)


class HygieneService:
    def __init__(
        self,
        *,
        repository: HygieneRepository,
        readiness: ReleaseReadinessService,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
        forecast_service: ForecastService,
        jira_health: JiraHealth,
        baselines: BaselineService | None = None,
        profiles: ProfileService | None = None,
        escalation: EscalationMatrixService | None = None,
        gates: GateService | None = None,
        clock: Callable[[], datetime] = _utc_now,
        today: Callable[[], date] | None = None,
        zone: ZoneInfo | None = None,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._repository = repository
        self._readiness = readiness
        self._graph = graph_repository
        self._facts = time_series_repository
        self._forecast = forecast_service
        self._jira_health = jira_health
        self._baselines = baselines
        self._profiles = profiles
        self._escalation = escalation
        self._gates = gates
        self._clock = clock
        self._today = today or (lambda: clock().date())
        # The zone a finding's first sighting and an issue's changes are dated in, so
        # "new today" and "open N days" count on the tenant's calendar, the one
        # ``today`` is on; the registry passes the tenant's zone.
        self._zone = zone or ZoneInfo("UTC")
        self._new_id = new_id

    # ---- configuration ---------------------------------------------------------------

    async def settings(self, tenant_id: str) -> HygieneSettings:
        return await self._repository.get_settings(tenant_id) or HygieneSettings(
            tenant_id=tenant_id
        )

    async def config(self, tenant_id: str) -> ConfigView:
        stored = await self._repository.get_settings(tenant_id)
        settings = stored or HygieneSettings(tenant_id=tenant_id)
        rules = await self._repository.list_rules(tenant_id)
        added = {rule.rule_id for rule in rules}
        examples = default_examples(tenant_id, stale_working_days=settings.stale_working_days)
        return ConfigView(
            settings=settings,
            is_default=stored is None,
            rules=tuple(rules),
            examples=tuple(rule for rule in examples if rule.rule_id not in added),
            gate_kinds=tuple(sorted((await self._gate_kind_labels(tenant_id)).items())),
        )

    async def save_settings(self, settings: HygieneSettings, *, actor: str) -> HygieneSettings:
        before = await self._repository.get_settings(settings.tenant_id)
        saved = replace(validated_settings(settings), updated_at=self._clock(), updated_by=actor)
        await self._repository.save_settings(saved)
        await self._audit(
            settings.tenant_id,
            actor,
            "settings_saved",
            detail={
                "before": _settings_json(before) if before else None,
                "after": _settings_json(saved),
            },
        )
        return saved

    async def save_rule(self, rule: HygieneRule, *, actor: str) -> HygieneRule:
        tenant_id = rule.tenant_id
        every = await self._repository.list_rules(tenant_id, include_deleted=True)
        current = next((item for item in every if item.rule_id == rule.rule_id), None)
        examples = {item.rule_id for item in default_examples(tenant_id)}
        if rule.rule_id and current is None and rule.rule_id not in examples:
            raise GraphNotFound(f"No rule {rule.rule_id!r}.")
        live = [item for item in every if item.deleted_at is None]
        if (current is None or current.deleted_at is not None) and len(live) >= MAX_RULES:
            raise HygieneError(f"A tenant has at most {MAX_RULES} Jira hygiene rules.")
        checked = validated_rule(rule)
        saved = replace(
            checked,
            rule_id=current.rule_id
            if current
            else rule.rule_id or rule_slug(checked.name, (item.rule_id for item in every)),
            version=current.version + 1 if current else 1,
            deleted_at=None,
            updated_at=self._clock(),
            updated_by=actor,
        )
        await self._repository.save_rule(saved)
        await self._audit(
            tenant_id,
            actor,
            "rule_saved",
            rule_id=saved.rule_id,
            detail={
                "version": saved.version,
                "name": saved.name,
                "enabled": saved.enabled,
                "severity": saved.severity.value,
            },
        )
        return saved

    async def remove_rule(self, tenant_id: str, rule_id: str, *, actor: str) -> None:
        rule = await self._rule(tenant_id, rule_id)
        await self._repository.save_rule(
            replace(rule, deleted_at=self._clock(), updated_at=self._clock(), updated_by=actor)
        )
        await self._audit(tenant_id, actor, "rule_removed", rule_id=rule_id)

    async def preview(self, rule: HygieneRule, project_id: str) -> tuple[PreviewScope, ...]:
        """What the scan would find on one project with this rule. Saves nothing."""
        checked = validated_rule(replace(rule, rule_id="preview"))
        tenant_id = checked.tenant_id
        settings = await self.settings(tenant_id)
        pass_ = await self._new_pass(tenant_id, settings, JiraData(), None)
        scopes = await self._scopes(pass_, project_id)
        rows: list[PreviewScope] = []
        for context in scopes.contexts:
            if context.scope.kind is not checked.scope.kind:
                continue
            looked = [issue for issue in context.issues if selected(checked, issue)]
            baseline = await self._baseline(pass_, context) if needs_baseline(checked) else None
            found: list[PreviewIssue] = []
            count = 0
            for issue in sorted(looked, key=lambda item: _natural(item.key)):
                breach = evaluate_rule(
                    checked, issue, await self._issue_context(pass_, checked, issue, context)
                )
                if breach is None:
                    continue
                count += 1
                if len(found) < MAX_PREVIEW_ISSUES:
                    owner, _via, _pod = self._owner(checked, issue, context, scopes)
                    found.append(
                        PreviewIssue(
                            key=issue.key,
                            title=issue.title,
                            what=breach.what,
                            owner_name=scopes.org.names.get(owner or ""),
                            suggested_fix=self._fix(
                                checked,
                                breach.detail,
                                issue.key,
                                issue.title,
                                context,
                                scopes,
                                pass_,
                            ),
                        )
                    )
            rows.append(
                PreviewScope(
                    scope=context.scope,
                    scope_name=context.name,
                    looked_at=len(looked),
                    found=count,
                    issues=tuple(found),
                    needs_baseline=needs_baseline(checked) and baseline is None,
                )
            )
        return tuple(rows)

    # ---- runs ------------------------------------------------------------------------

    async def run_tenant(self, tenant_id: str, *, slot: str) -> RunSummary | None:
        """The hourly pass over every scope, inside the readiness tick. None when the slot
        already ran or the tenant never set hygiene up: nothing is recorded for it."""
        stored = await self._repository.get_settings(tenant_id)
        if stored is None:
            return None
        run = self._new_run(tenant_id, slot, RunTrigger.SCHEDULE, None)
        if not await self._repository.claim_run(run):
            return None
        if not stored.enabled:
            await self._finish(run, RunSummary(status=RunStatus.OFF), None)
            return RunSummary(status=RunStatus.OFF)
        jira = await self._jira_health(tenant_id)
        try:
            rules = await self._enabled_rules(tenant_id)
            pass_ = await self._new_pass(tenant_id, stored, jira, run.run_id)
            seen: set[str] = set()
            scopes = 0
            for project in await self._graph.list_nodes(tenant_id, NodeKind.PROJECT):
                scopes += await self._judge_project(pass_, project.id, rules, seen)
            summary = await self._summary(pass_, scopes)
        except Exception as exc:
            _log_failure(tenant_id, exc)
            failed = RunSummary(status=RunStatus.FAILED)
            await self._finish(run, failed, jira, error="unexpected")
            return failed
        await self._finish(run, summary, jira)
        return summary

    async def run_project(self, tenant_id: str, project_id: str, viewer: Viewer) -> RunSummary:
        """Check now, for a project with its releases and pods, inline."""
        settings = await self.settings(tenant_id)
        if not settings.enabled:
            raise HygieneConflict("scan_off", "The Jira hygiene scan is off for this tenant.")
        if not viewer.may_act or not viewer.project_reach:
            raise AuthorizationDenied(_NOT_YOURS)
        now = self._clock()
        scope = ScopeRef(kind=ScopeKind.PROJECT, id=project_id)
        run = self._new_run(
            tenant_id, f"manual:{scope.key}:{now:%Y%m%d%H%M}", RunTrigger.MANUAL, viewer.subject
        )
        claimed = await self._repository.claim_run(run)
        await self._audit(tenant_id, viewer.subject, "run_now", detail={"scope": scope.key})
        jira = await self._jira_health(tenant_id)
        pass_ = await self._new_pass(tenant_id, settings, jira, run.run_id)
        rules = await self._enabled_rules(tenant_id)
        count = await self._judge_project(pass_, project_id, rules, set(), viewer=viewer)
        summary = await self._summary(pass_, count)
        if claimed:
            await self._finish(run, summary, jira)
        return summary

    async def _summary(self, pass_: _Pass, scopes: int) -> RunSummary:
        still = await self._repository.list_findings(
            pass_.tenant_id, pass_.judged, states=(HygieneState.OPEN,)
        )
        return RunSummary(
            status=RunStatus.OK,
            scopes=scopes,
            opened=pass_.opened,
            resolved=pass_.resolved,
            changed=pass_.changed,
            open=len(still),
        )

    async def _judge_project(
        self,
        pass_: _Pass,
        project_id: str,
        rules: Sequence[HygieneRule],
        seen_pods: set[str],
        *,
        viewer: Viewer | None = None,
    ) -> int:
        scopes = await self._scopes(pass_, project_id)
        contexts = [
            context for context in scopes.contexts if viewer is None or viewer.sees(context.scope)
        ]
        existing = await self._repository.list_findings(
            pass_.tenant_id, [context.scope for context in contexts], states=_SHOWN
        )
        judged = 0
        for context in contexts:
            if context.scope.kind is ScopeKind.POD:
                if context.scope.id in seen_pods:
                    continue
                seen_pods.add(context.scope.id)
            judged += 1
            pass_.judged.append(context.scope)
            for rule in rules:
                if rule.scope.kind is context.scope.kind:
                    await self._judge_scope(pass_, rule, context, scopes, existing)
        return judged

    async def _judge_scope(
        self,
        pass_: _Pass,
        rule: HygieneRule,
        context: ScopeContext,
        scopes: _Scopes,
        existing: Sequence[HygieneFinding],
        *,
        only_key: str | None = None,
    ) -> None:
        mine = [
            item
            for item in existing
            if item.rule_id == rule.rule_id
            and item.scope == context.scope
            and (only_key is None or item.issue_key == only_key)
        ]
        open_now = {item.issue_key: item for item in mine if item.state is HygieneState.OPEN}
        decided = {item.issue_key for item in mine if item.state in DECIDED}
        in_scope = {issue.key for issue in context.issues}
        breaking: set[str] = set()
        for issue in context.issues:
            if only_key is not None and issue.key != only_key:
                continue
            if not selected(rule, issue):
                continue
            breach = evaluate_rule(
                rule, issue, await self._issue_context(pass_, rule, issue, context)
            )
            if breach is None:
                continue
            breaking.add(issue.key)
            if issue.key in decided:
                # A person's waive or "not applicable" stands while the breach does.
                continue
            await self._keep(pass_, rule, context, scopes, issue, breach, open_now.get(issue.key))
        for key, finding in open_now.items():
            if key in breaking:
                continue
            if pass_.jira.stale:
                # Stale data never closes a finding.
                continue
            resolution = Resolution.FIXED if key in in_scope else Resolution.LEFT_SCOPE
            await self._close(pass_, finding, resolution)

    async def _keep(
        self,
        pass_: _Pass,
        rule: HygieneRule,
        context: ScopeContext,
        scopes: _Scopes,
        issue: ScopeIssue,
        breach: Breach,
        prior: HygieneFinding | None,
    ) -> None:
        owner, via, pod = self._owner(rule, issue, context, scopes)
        detail: dict[str, object] = {
            **breach.detail,
            "what": breach.what,
            "stage": issue.stage.value if issue.stage else None,
            "waits_from": breach.waits_from.isoformat() if breach.waits_from else None,
            "owner_via": via,
            "owner_profile": rule.owner.profile if via == OwnerKind.PROFILE.value else None,
            "owner_pod_id": pod,
            "pod_ids": list(scopes.pods_of_key.get(issue.key, ())),
            "release_ids": list(scopes.releases_of_key.get(issue.key, ())),
        }
        held = pass_.jira.stale and rule.asks and (prior is None or prior.held)
        fingerprint = finding_fingerprint(
            rule_version=rule.version,
            issue_key=issue.key,
            scope=context.scope,
            detail=detail,
            owner_member_id=owner,
            issue_updated_at=issue.updated_at,
            held=held,
        )
        if prior is not None and prior.fingerprint == fingerprint:
            return
        now = self._clock()
        finding = HygieneFinding(
            tenant_id=pass_.tenant_id,
            finding_id=prior.finding_id if prior else f"hf_{self._new_id()}",
            rule_id=rule.rule_id,
            rule_version=rule.version,
            issue_key=issue.key,
            scope=context.scope,
            project_id=context.project_id,
            state=HygieneState.OPEN,
            owner_member_id=owner,
            detail=detail,
            fingerprint=fingerprint,
            held=held,
            first_seen_at=prior.first_seen_at if prior else now,
            last_seen_at=now,
        )
        await self._repository.save_finding(finding)
        if prior is None:
            pass_.opened += 1
        pass_.changed += 1
        await self._audit(
            pass_.tenant_id,
            pass_.actor,
            "opened" if prior is None else "changed",
            rule_id=rule.rule_id,
            finding_id=finding.finding_id,
            detail={
                "key": issue.key,
                "scope": context.scope.key,
                "owner": owner,
                "held": held,
                "run": pass_.run_id,
            },
        )

    async def _close(self, pass_: _Pass, finding: HygieneFinding, resolution: Resolution) -> None:
        now = self._clock()
        await self._repository.save_finding(
            replace(
                finding,
                state=HygieneState.RESOLVED,
                resolution=resolution,
                resolved_at=now,
                last_seen_at=now,
            )
        )
        pass_.resolved += 1
        pass_.changed += 1
        await self._audit(
            pass_.tenant_id,
            pass_.actor,
            "resolved",
            rule_id=finding.rule_id,
            finding_id=finding.finding_id,
            detail={"key": finding.issue_key, "resolution": resolution.value, "run": pass_.run_id},
        )

    # ---- the board -------------------------------------------------------------------

    async def project_board(
        self,
        tenant_id: str,
        project_id: str,
        viewer: Viewer,
        *,
        release: Release | None = None,
        pod_id: str | None = None,
    ) -> BoardView:
        project = await self._graph.get_node(tenant_id, project_id)
        if project is None or project.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"No project {project_id!r}.")
        today = self._today()
        settings = await self.settings(tenant_id)
        releases = await self._forecast.releases(tenant_id, project_id)
        pods = await self._project_pods(tenant_id, project_id, today)
        scopes: list[tuple[ScopeRef, str]] = [
            (ScopeRef(kind=ScopeKind.PROJECT, id=project_id), project.name),
            *(
                (ScopeRef(kind=ScopeKind.RELEASE, id=item.release_id), item.name)
                for item in releases
            ),
            *((ScopeRef(kind=ScopeKind.POD, id=pod.id), pod.name) for pod in pods),
        ]
        if pod_id is not None and pod_id not in {pod.id for pod in pods}:
            raise GraphNotFound(f"No pod {pod_id!r} works on {project_id!r}.")

        def wanted(finding: HygieneFinding) -> bool:
            if release is not None and not (
                finding.scope == ScopeRef(kind=ScopeKind.RELEASE, id=release.release_id)
                or release.release_id in finding.release_ids
            ):
                return False
            return pod_id is None or (
                finding.scope == ScopeRef(kind=ScopeKind.POD, id=pod_id)
                or pod_id in finding.pod_ids
            )

        release_names = {item.release_id: item.name for item in releases}
        views, rows = await self._views(
            tenant_id, scopes, viewer, wanted, release_names, project.name
        )
        return BoardView(
            project_id=project_id,
            pod_id=pod_id,
            release_id=release.release_id if release else None,
            scope_name=release.name if release else project.name,
            settings=settings,
            last_run=await self._repository.last_run(tenant_id),
            jira=await self._jira_health(tenant_id),
            counts_only=reads_counts_only(viewer),
            rules=rows,
            findings=views,
            pods=tuple(
                (pod.id, pod.name)
                for pod in pods
                if viewer.sees(ScopeRef(kind=ScopeKind.POD, id=pod.id))
            ),
            releases=tuple((item.release_id, item.name) for item in releases),
            can_run=viewer.may_act and viewer.project_reach and settings.enabled,
        )

    async def pod_board(self, tenant_id: str, pod_id: str, viewer: Viewer) -> BoardView:
        pod = await self._graph.get_node(tenant_id, pod_id)
        if pod is None or pod.kind is not NodeKind.POD:
            raise GraphNotFound(f"No pod {pod_id!r}.")
        scope = ScopeRef(kind=ScopeKind.POD, id=pod_id)
        views, rows = await self._views(
            tenant_id, [(scope, pod.name)], viewer, lambda _finding: True, {}, pod.name
        )
        return BoardView(
            project_id=None,
            pod_id=pod_id,
            release_id=None,
            scope_name=pod.name,
            settings=await self.settings(tenant_id),
            last_run=await self._repository.last_run(tenant_id),
            jira=await self._jira_health(tenant_id),
            counts_only=reads_counts_only(viewer),
            rules=rows,
            findings=views,
        )

    async def _views(
        self,
        tenant_id: str,
        scopes: Sequence[tuple[ScopeRef, str]],
        viewer: Viewer,
        wanted: Callable[[HygieneFinding], bool],
        release_names: Mapping[str, str],
        default_release: str,
        *,
        states: Collection[HygieneState] = _SHOWN,
    ) -> tuple[tuple[FindingView, ...], tuple[RuleRow, ...]]:
        names = {scope.key: name for scope, name in scopes}
        rules = {rule.rule_id: rule for rule in await self._enabled_rules(tenant_id)}
        findings = [
            item
            for item in await self._repository.list_findings(
                tenant_id, [scope for scope, _ in scopes], states=states
            )
            if item.rule_id in rules
            and rules[item.rule_id].scope.kind is item.scope.kind
            and viewer.sees(item.scope)
            and wanted(item)
        ]
        today = self._today()
        gate_labels = await self._gate_kind_labels(tenant_id)
        rows = tuple(
            _rule_row(
                rule,
                [item for item in findings if item.rule_id == rule.rule_id],
                today,
                gate_labels,
                self._zone,
            )
            for rule in sorted(rules.values(), key=_rule_order)
        )
        if reads_counts_only(viewer):
            return (), rows
        titles = {
            _task_key(task): task.name
            for task in await self._graph.list_nodes(tenant_id, NodeKind.TASK)
        }
        people = await self._names(tenant_id)
        labels = await self._profile_labels(tenant_id)
        views: list[FindingView] = []
        for item in sorted(findings, key=lambda f: _finding_order(f, rules)):
            if len(views) >= MAX_BOARD_FINDINGS:
                break
            rule = rules[item.rule_id]
            release = (
                release_names.get(item.scope.id, item.scope.id)
                if item.scope.kind is ScopeKind.RELEASE
                else next(
                    (release_names[r] for r in item.release_ids if r in release_names),
                    default_release,
                )
            )
            title = titles.get(item.issue_key, "")
            profile = item.detail.get("owner_profile")
            views.append(
                FindingView(
                    finding=item,
                    rule=rule,
                    title=title,
                    scope_name=names.get(item.scope.key, item.scope.id),
                    owner_name=people.get(item.owner_member_id or ""),
                    owner_profile_label=labels.get(profile, profile)
                    if isinstance(profile, str) and profile
                    else None,
                    suggested_fix=render_fix(
                        rule,
                        fix_values(
                            rule,
                            item.detail,
                            key=item.issue_key,
                            title=title,
                            release=release,
                            gate_kind_label=gate_labels.get(rule.condition.gate_kind, ""),
                        ),
                    ),
                    open_days=max(0, (today - local_date(item.first_seen_at, self._zone)).days),
                    actions=finding_actions(viewer, item, rule),
                    decided_by_name=people.get(item.decision.by) if item.decision else None,
                )
            )
        return tuple(views), rows

    # ---- a person's actions ----------------------------------------------------------

    async def waive(
        self, tenant_id: str, finding_id: str, viewer: Viewer, *, reason: str
    ) -> HygieneFinding:
        return await self._decide(
            tenant_id, finding_id, viewer, HygieneState.WAIVED, reason, "waived"
        )

    async def not_applicable(
        self, tenant_id: str, finding_id: str, viewer: Viewer, *, reason: str
    ) -> HygieneFinding:
        finding = await self._finding(tenant_id, finding_id)
        rule = await self._rule(tenant_id, finding.rule_id)
        if rule.blocking and not _waives_blocking(viewer):
            raise AuthorizationDenied(_WAIVE)
        return await self._decide(
            tenant_id, finding_id, viewer, HygieneState.NOT_APPLICABLE, reason, "not_applicable"
        )

    async def _decide(
        self,
        tenant_id: str,
        finding_id: str,
        viewer: Viewer,
        state: HygieneState,
        reason: str,
        action: str,
    ) -> HygieneFinding:
        finding = await self._finding(tenant_id, finding_id)
        self._ensure_acts(viewer, finding)
        if finding.state is not HygieneState.OPEN:
            raise HygieneConflict("not_open", "Only an open finding is waived or set aside.")
        kept = validated_reason(reason)
        now = self._clock()
        decided = replace(
            finding,
            state=state,
            decision=PersonDecision(by=viewer.subject, at=now, reason=kept),
            last_seen_at=now,
        )
        await self._repository.save_finding(decided)
        await self._audit(
            tenant_id,
            viewer.subject,
            action,
            rule_id=finding.rule_id,
            finding_id=finding_id,
            note=kept,
        )
        return decided

    async def reopen(self, tenant_id: str, finding_id: str, viewer: Viewer) -> HygieneFinding:
        finding = await self._finding(tenant_id, finding_id)
        self._ensure_acts(viewer, finding)
        if finding.state not in DECIDED:
            raise HygieneConflict("nothing_to_reopen", "Nothing here was decided by a person.")
        reopened = replace(
            finding,
            state=HygieneState.OPEN,
            decision=None,
            fingerprint="",
            last_seen_at=self._clock(),
        )
        await self._repository.save_finding(reopened)
        await self._audit(
            tenant_id, viewer.subject, "reopened", rule_id=finding.rule_id, finding_id=finding_id
        )
        return await self._rejudge(tenant_id, reopened, viewer)

    async def recheck(self, tenant_id: str, finding_id: str, viewer: Viewer) -> HygieneFinding:
        """Read the issue again as the last sync left it, after a fix in Jira."""
        finding = await self._finding(tenant_id, finding_id)
        self._ensure_acts(viewer, finding)
        if finding.state is not HygieneState.OPEN:
            raise HygieneConflict("not_open", "Only an open finding is checked again.")
        await self._audit(
            tenant_id, viewer.subject, "rechecked", rule_id=finding.rule_id, finding_id=finding_id
        )
        return await self._rejudge(tenant_id, finding, viewer)

    async def _rejudge(
        self, tenant_id: str, finding: HygieneFinding, viewer: Viewer
    ) -> HygieneFinding:
        rule = await self._rule(tenant_id, finding.rule_id)
        settings = await self.settings(tenant_id)
        pass_ = await self._new_pass(tenant_id, settings, await self._jira_health(tenant_id), None)
        pass_.actor = viewer.subject
        project_id = await self._project_of(tenant_id, finding)
        if project_id is None:
            raise GraphNotFound(f"No project holds {finding.scope.key!r} any more.")
        scopes = await self._scopes(pass_, project_id)
        context = next((item for item in scopes.contexts if item.scope == finding.scope), None)
        if context is None:
            raise GraphNotFound(f"No {finding.scope.kind.value} {finding.scope.id!r}.")
        await self._judge_scope(pass_, rule, context, scopes, [finding], only_key=finding.issue_key)
        return await self._finding(tenant_id, finding.finding_id)

    async def scope_of_finding(
        self, tenant_id: str, finding_id: str
    ) -> tuple[ScopeRef, str | None]:
        """A finding's scope and the project it belongs to (None for a pod), for reach."""
        finding = await self._finding(tenant_id, finding_id)
        return finding.scope, finding.project_id

    async def history(self, tenant_id: str, finding_id: str) -> list[HygieneAction]:
        await self._finding(tenant_id, finding_id)
        return await self._repository.list_actions(tenant_id, finding_id)

    async def finding_view(
        self, tenant_id: str, finding: HygieneFinding, viewer: Viewer
    ) -> FindingView | None:
        """One finding as the viewer's board shows it; None when it is no longer shown."""
        project_id = await self._project_of(tenant_id, finding)
        name = await self._scope_name(tenant_id, finding.scope)
        releases = await self._forecast.releases(tenant_id, project_id) if project_id else []
        project = await self._graph.get_node(tenant_id, project_id) if project_id else None
        views, _rows = await self._views(
            tenant_id,
            [(finding.scope, name)],
            viewer,
            lambda item: item.finding_id == finding.finding_id,
            {item.release_id: item.name for item in releases},
            project.name if project is not None else name,
            states=tuple(HygieneState),
        )
        return views[0] if views else None

    # ---- the day report --------------------------------------------------------------

    async def report(
        self, tenant_id: str, project_id: str, release: Release | None, day: date
    ) -> HygieneReport:
        """What a project's (or a release's) day report says: the count line, and one ask
        per owner per asking rule per scope. Nothing while the scan is off or no rule is
        on, so such a report is what it always was."""
        stored = await self._repository.get_settings(tenant_id)
        if stored is None or not stored.enabled:
            return HygieneReport()
        rules = {rule.rule_id: rule for rule in await self._enabled_rules(tenant_id)}
        project = await self._graph.get_node(tenant_id, project_id)
        if not rules or project is None:
            return HygieneReport()
        releases = await self._forecast.releases(tenant_id, project_id)
        pods = await self._project_pods(tenant_id, project_id, day)
        names: dict[str, str] = {
            ScopeRef(kind=ScopeKind.PROJECT, id=project_id).key: project.name,
            **{ScopeRef(kind=ScopeKind.RELEASE, id=r.release_id).key: r.name for r in releases},
            **{ScopeRef(kind=ScopeKind.POD, id=pod.id).key: pod.name for pod in pods},
        }
        scopes = [
            ScopeRef(kind=ScopeKind(key.split(":", 1)[0]), id=key.split(":", 1)[1]) for key in names
        ]
        findings = [
            item
            for item in await self._repository.list_findings(
                tenant_id, scopes, states=(HygieneState.OPEN,)
            )
            if item.rule_id in rules
            and rules[item.rule_id].scope.kind is item.scope.kind
            and (
                release is None
                or item.scope == ScopeRef(kind=ScopeKind.RELEASE, id=release.release_id)
                or release.release_id in item.release_ids
            )
        ]
        groups: dict[tuple[str, str | None, str], list[HygieneFinding]] = {}
        for item in findings:
            if not rules[item.rule_id].asks or item.held or item.gate_asks:
                continue
            groups.setdefault((item.rule_id, item.owner_member_id, item.scope.key), []).append(item)
        asks: list[HygieneAsk] = []
        for (rule_id, owner, scope_key), items in sorted(
            groups.items(), key=lambda entry: (entry[0][0], entry[0][2], entry[0][1] or "")
        ):
            rule = rules[rule_id]
            keys = tuple(sorted({item.issue_key for item in items}, key=_natural))
            pod = next(
                (
                    value
                    for item in items
                    if isinstance(value := item.detail.get("owner_pod_id"), str)
                ),
                None,
            )
            scope = items[0].scope
            asks.append(
                HygieneAsk(
                    rule_id=rule_id,
                    need=rule.need,
                    owner_member_id=owner,
                    scope=scope,
                    pod_id=scope.id if scope.kind is ScopeKind.POD else pod,
                    keys=keys,
                    waits_from=min(item.waits_from for item in items),
                    text=hygiene_ask_text(rule, keys, names.get(scope_key, scope.id)),
                )
            )
        return HygieneReport(
            enabled=True,
            open=len(findings),
            new_today=sum(
                1 for item in findings if local_date(item.first_seen_at, self._zone) == day
            ),
            held=sum(1 for item in findings if item.held),
            asks=tuple(asks),
        )

    # ---- reading scopes --------------------------------------------------------------

    async def _new_pass(
        self, tenant_id: str, settings: HygieneSettings, jira: JiraData, run_id: str | None
    ) -> _Pass:
        pass_ = _Pass(
            tenant_id=tenant_id,
            today=self._today(),
            settings=settings,
            jira=jira,
            run_id=run_id,
        )
        if self._gates is not None:
            templates, _default = await self._gates.templates(tenant_id)
            for template in templates:
                if not template.enabled:
                    continue
                for kind in template.kinds:
                    pass_.gate_labels.setdefault(kind.key, kind.label)
                pass_.gate_templates.append(
                    (
                        template.guards_stage,
                        frozenset(kind.key for kind in template.kinds),
                        frozenset(name.casefold() for name in template.issue_types),
                    )
                )
        return pass_

    async def _scopes(self, pass_: _Pass, project_id: str) -> _Scopes:
        tenant_id = pass_.tenant_id
        contexts, _known = await self._readiness.scope_contexts(tenant_id, project_id, pass_.today)
        pods_of_key: dict[str, list[str]] = {}
        releases_of_key: dict[str, list[str]] = {}
        for context in contexts:
            for issue in context.issues:
                if context.scope.kind is ScopeKind.POD:
                    pods_of_key.setdefault(issue.key, []).append(context.scope.id)
                elif context.scope.kind is ScopeKind.RELEASE:
                    releases_of_key.setdefault(issue.key, []).append(context.scope.id)
        project = await self._graph.get_node(tenant_id, project_id)
        pod_ids = [context.scope.id for context in contexts if context.scope.kind is ScopeKind.POD]
        return _Scopes(
            project=project,
            contexts=tuple(contexts),
            pods_of_key={key: tuple(value) for key, value in pods_of_key.items()},
            releases_of_key={key: tuple(value) for key, value in releases_of_key.items()},
            org=await self._org(pass_, project, pod_ids, contexts),
        )

    async def _org(
        self,
        pass_: _Pass,
        project: GraphNode | None,
        pod_ids: Sequence[str],
        contexts: Sequence[ScopeContext],
    ) -> _Org:
        people = await self._people(pass_)
        names = {context.scope.id: context.name for context in contexts}
        pods = tuple(sorted(pod_ids, key=lambda pod: ((names.get(pod) or pod).casefold(), pod)))
        profiles: dict[str, dict[str, str]] = {}
        labels: dict[str, str] = {}
        if self._profiles is not None and pods:
            views = await self._profiles.profiles_on(pass_.tenant_id, pass_.today, pod_ids=pods)
            for view in views.values():
                profiles.setdefault(view.pod_id, {})[view.member_id] = view.profile
                labels.setdefault(view.profile, view.label)
        scrum_masters: dict[str, str] = {}
        for pod_id in pods:
            node = await self._graph.get_node(pass_.tenant_id, pod_id)
            if node is not None and (found := _scrum_master(node, people)) is not None:
                scrum_masters[pod_id] = found
        return _Org(
            names=people.names,
            profiles=profiles,
            profile_labels=labels,
            scrum_masters=scrum_masters,
            decider=await self._decider(pass_, project, pods, people),
            pods=pods,
        )

    async def _people(self, pass_: _Pass) -> _People:
        if pass_.people is not None:
            return pass_.people
        names: dict[str, str] = {}
        roles: dict[str, frozenset[str]] = {}
        chat_ids: dict[str, str] = {}
        for node in await self._graph.list_nodes(pass_.tenant_id, NodeKind.DEVELOPER):
            names[node.id] = node.name
            roles[node.id] = member_roles(node.metadata)
            chat = node.metadata.get("chat_external_id")
            if isinstance(chat, str) and chat:
                chat_ids[chat] = node.id
        members: dict[str, set[str]] = {}
        for edge in await self._graph.list_edges(pass_.tenant_id, kind=EdgeKind.CONTAINS):
            if edge.is_active_on(pass_.today) and edge.to_node_id in names:
                members.setdefault(edge.from_node_id, set()).add(edge.to_node_id)
        pass_.people = _People(names=names, roles=roles, chat_ids=chat_ids, members=members)
        return pass_.people

    async def _decider(
        self, pass_: _Pass, project: GraphNode | None, pods: Sequence[str], people: _People
    ) -> str | None:
        """Who decides for the project: its matrix's decision owner, else its recorded
        owner, else a product owner of its pods (first pod by name, then first by name)."""
        if project is None:
            return None
        if self._escalation is not None:
            matrix = (await self._escalation.matrix_for(pass_.tenant_id, project.id)).matrix
            if matrix.decision_owner_id and matrix.decision_owner_id in people.names:
                return matrix.decision_owner_id
        recorded = project.metadata.get(PROJECT_OWNER_KEY)
        if isinstance(recorded, str) and recorded:
            member = recorded if recorded in people.names else people.chat_ids.get(recorded)
            if member is not None:
                return member
        for pod_id in pods:
            owners = sorted(
                (
                    member
                    for member in people.members.get(pod_id, ())
                    if "po" in people.roles.get(member, frozenset())
                ),
                key=lambda member: ((people.names.get(member) or member).casefold(), member),
            )
            if owners:
                return owners[0]
        return None

    async def _issue_context(
        self, pass_: _Pass, rule: HygieneRule, issue: ScopeIssue, context: ScopeContext
    ) -> IssueContext:
        baseline = await self._baseline(pass_, context) if needs_baseline(rule) else None
        joined: date | None = None
        last: date | None = None
        if needs_history(rule):
            changes, observed = await self._history(pass_, issue.key)
            updated = _date_of(issue.updated_at)
            moments = [day for day, _field in changes] + observed + ([updated] if updated else [])
            last = max(moments) if moments else None
            if baseline is not None:
                joined = _joined_on(issue, changes, baseline.planned_on)
        scanned = False
        kinds: frozenset[str] = frozenset()
        asks: frozenset[str] = frozenset()
        if needs_gates(rule) and self._gates is not None:
            if issue.key not in pass_.gates:
                keys = [item.key for item in context.issues if item.key not in pass_.gates]
                pass_.gates.update(await self._gates.issue_kinds(pass_.tenant_id, keys))
            scanned, kinds = pass_.gates.get(issue.key, (False, frozenset()))
            asks = _gate_asks(issue, pass_.gate_templates)
        return IssueContext(
            today=pass_.today,
            baseline=baseline,
            change_labels=pass_.settings.change_labels,
            joined_on=joined,
            last_changed_on=last,
            gate_scanned=scanned,
            gate_kinds=kinds,
            gate_kind_labels=pass_.gate_labels,
            gate_asks=asks,
        )

    async def _history(self, pass_: _Pass, key: str) -> tuple[list[tuple[date, str]], list[date]]:
        """An issue's kept changes (day, field) and the days the sync saw it change."""
        if key not in pass_.history:
            facts = await self._facts.list_facts(
                pass_.tenant_id, EntityRef(tenant_id=pass_.tenant_id, kind=NodeKind.TASK, id=key)
            )
            changes: list[tuple[date, str]] = []
            observed: list[date] = []
            for fact in facts:
                if fact.source == ISSUE_CHANGE_FACT_SOURCE:
                    changed_on = local_date(fact.observed_at, self._zone)
                    changes.append((changed_on, str(fact.payload.get("field") or "")))
                elif fact.source == ISSUE_FACT_SOURCE:
                    observed.append(local_date(fact.observed_at, self._zone))
            pass_.history[key] = (sorted(changes), sorted(observed))
        return pass_.history[key]

    async def _baseline(self, pass_: _Pass, context: ScopeContext) -> BaselineFacts | None:
        """The planning baseline in force for a scope: a release's own, else its project's;
        a pod reads its project's."""
        if self._baselines is None:
            return None
        project_id = context.project_id
        if context.scope.kind is ScopeKind.POD:
            projects = await self._forecast.pod_projects(
                pass_.tenant_id, context.scope.id, pass_.today
            )
            project_id = projects[0].id if projects else None
        wanted: list[tuple[BaselineScopeKind, str]] = []
        if context.scope.kind is ScopeKind.RELEASE:
            wanted.append((BaselineScopeKind.RELEASE, context.scope.id))
        if project_id:
            wanted.append((BaselineScopeKind.PROJECT, project_id))
        for kind, scope_id in wanted:
            cache = (kind.value, scope_id)
            if cache not in pass_.baselines:
                baseline = await self._baselines.active_baseline(pass_.tenant_id, kind, scope_id)
                pass_.baselines[cache] = (
                    BaselineFacts(
                        planned_on=baseline.planned_on,
                        keys=baseline.keys,
                        change_label=baseline.change_label,
                        grace_working_days=baseline.grace_working_days,
                    )
                    if baseline is not None
                    else None
                )
            if pass_.baselines[cache] is not None:
                return pass_.baselines[cache]
        return None

    def _owner(
        self, rule: HygieneRule, issue: ScopeIssue, context: ScopeContext, scopes: _Scopes
    ) -> tuple[str | None, str | None, str | None]:
        """Who is asked about one finding, how they were found, and the pod they stand in.

        The rule's owner when there is one; else the pod's scrum master; else whoever
        decides for the project.
        """
        org = scopes.org
        if context.scope.kind is ScopeKind.POD:
            pods: tuple[str, ...] = (context.scope.id,)
        else:
            pods = scopes.pods_of_key.get(issue.key) or org.pods
        pod = pods[0] if pods else None
        named = _named_owner(rule, issue, pods, org)
        if named is not None:
            member, pod_id = named
            return member, rule.owner.kind.value, pod_id or pod
        for pod_id in pods:
            if pod_id in org.scrum_masters:
                return org.scrum_masters[pod_id], OwnerKind.POD_SCRUM_MASTER.value, pod_id
        if org.decider:
            return org.decider, OwnerKind.DECISION_OWNER.value, pod
        return None, None, pod

    def _fix(
        self,
        rule: HygieneRule,
        detail: Mapping[str, object],
        key: str,
        title: str,
        context: ScopeContext,
        scopes: _Scopes,
        pass_: _Pass,
    ) -> str:
        release = context.release_name or (scopes.project.name if scopes.project else context.name)
        return render_fix(
            rule,
            fix_values(
                rule,
                detail,
                key=key,
                title=title,
                release=release,
                gate_kind_label=pass_.gate_labels.get(rule.condition.gate_kind, ""),
            ),
        )

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

    async def _project_of(self, tenant_id: str, finding: HygieneFinding) -> str | None:
        if finding.project_id:
            return finding.project_id
        if finding.scope.kind is ScopeKind.POD:
            projects = await self._forecast.pod_projects(tenant_id, finding.scope.id, self._today())
            return projects[0].id if projects else None
        return None

    async def _scope_name(self, tenant_id: str, scope: ScopeRef) -> str:
        if scope.kind is ScopeKind.RELEASE:
            try:
                return (await self._forecast.release(tenant_id, scope.id)).name
            except GraphNotFound:
                return scope.id
        node = await self._graph.get_node(tenant_id, scope.id)
        return node.name if node is not None else scope.id

    async def _names(self, tenant_id: str) -> dict[str, str]:
        return {
            node.id: node.name
            for node in await self._graph.list_nodes(tenant_id, NodeKind.DEVELOPER)
        }

    async def _profile_labels(self, tenant_id: str) -> dict[str, str]:
        if self._profiles is None:
            return {}
        return {entry.slug: entry.label for entry in await self._profiles.catalog(tenant_id)}

    async def _gate_kind_labels(self, tenant_id: str) -> dict[str, str]:
        if self._gates is None:
            return {}
        labels: dict[str, str] = {}
        templates, _default = await self._gates.templates(tenant_id)
        for template in templates:
            for kind in template.kinds:
                labels.setdefault(kind.key, kind.label)
        return labels

    # ---- small reads and checks ------------------------------------------------------

    async def _enabled_rules(self, tenant_id: str) -> list[HygieneRule]:
        return [rule for rule in await self._repository.list_rules(tenant_id) if rule.enabled]

    async def _rule(self, tenant_id: str, rule_id: str) -> HygieneRule:
        rule = next(
            (
                item
                for item in await self._repository.list_rules(tenant_id)
                if item.rule_id == rule_id
            ),
            None,
        )
        if rule is None:
            raise GraphNotFound(f"No rule {rule_id!r}.")
        return rule

    async def _finding(self, tenant_id: str, finding_id: str) -> HygieneFinding:
        finding = await self._repository.get_finding(tenant_id, finding_id)
        if finding is None:
            raise GraphNotFound(f"No finding {finding_id!r}.")
        return finding

    def _ensure_acts(self, viewer: Viewer, finding: HygieneFinding) -> None:
        if not viewer.acts_on(finding.scope):
            raise AuthorizationDenied(_NOT_YOURS)

    def _new_run(
        self, tenant_id: str, slot: str, trigger: RunTrigger, actor: str | None
    ) -> HygieneRun:
        return HygieneRun(
            tenant_id=tenant_id,
            run_id=f"hr_{self._new_id()}",
            slot=slot,
            trigger=trigger,
            status=RunStatus.RUNNING,
            started_at=self._clock(),
            actor=actor,
        )

    async def _finish(
        self,
        run: HygieneRun,
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
                    "opened": summary.opened,
                    "resolved": summary.resolved,
                    "changed": summary.changed,
                    "open": summary.open,
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
        rule_id: str | None = None,
        finding_id: str | None = None,
        note: str | None = None,
        detail: Mapping[str, object] | None = None,
    ) -> None:
        await self._repository.append_action(
            HygieneAction(
                tenant_id=tenant_id,
                action_id=f"ha_{self._new_id()}",
                at=self._clock(),
                actor=actor,
                action=action,
                rule_id=rule_id,
                finding_id=finding_id,
                note=note,
                detail=detail,
            )
        )


# ---- pure helpers ---------------------------------------------------------------------


def finding_actions(viewer: Viewer, finding: HygieneFinding, rule: HygieneRule) -> FindingActions:
    """What this viewer may do with this finding now, worked out once on the server."""
    if not viewer.acts_on(finding.scope):
        return FindingActions()
    is_open = finding.state is HygieneState.OPEN
    return FindingActions(
        waive=is_open,
        not_applicable=is_open and (not rule.blocking or _waives_blocking(viewer)),
        recheck=is_open,
        reopen=finding.state in DECIDED,
    )


def _waives_blocking(viewer: Viewer) -> bool:
    return Role.ADMIN in viewer.roles or bool(viewer.roles & WAIVE_ROLES)


def _rule_row(
    rule: HygieneRule,
    findings: Sequence[HygieneFinding],
    today: date,
    gate_labels: Mapping[str, str],
    zone: ZoneInfo,
) -> RuleRow:
    open_items = [item for item in findings if item.state is HygieneState.OPEN]
    seen_on = [local_date(item.first_seen_at, zone) for item in open_items]
    ages = [max(0, (today - day).days) for day in seen_on]
    return RuleRow(
        rule=rule,
        stated=rule_words(rule, gate_kind_label=gate_labels.get(rule.condition.gate_kind, "")),
        open=len(open_items),
        new_today=sum(1 for day in seen_on if day == today),
        oldest_days=max(ages) if ages else None,
        held=sum(1 for item in open_items if item.held),
        decided=sum(1 for item in findings if item.state in DECIDED),
    )


def _rule_order(rule: HygieneRule) -> tuple[int, str]:
    return (0 if rule.asks else 1, rule.name.casefold())


def _finding_order(
    finding: HygieneFinding, rules: Mapping[str, HygieneRule]
) -> tuple[int, int, str, tuple[str, int, str]]:
    rule = rules[finding.rule_id]
    return (
        0 if finding.state is HygieneState.OPEN else 1,
        0 if rule.asks else 1,
        rule.name.casefold(),
        _natural(finding.issue_key),
    )


def _named_owner(
    rule: HygieneRule, issue: ScopeIssue, pods: Sequence[str], org: _Org
) -> tuple[str, str | None] | None:
    """The rule's own owner for an issue, and the pod they hold a profile in; None when the
    rule names nobody there."""
    kind = rule.owner.kind
    if kind is OwnerKind.ASSIGNEE and issue.assignee_id and issue.assignee_id in org.names:
        return issue.assignee_id, None
    if kind is OwnerKind.REPORTER and issue.reporter_id and issue.reporter_id in org.names:
        return issue.reporter_id, None
    if kind is OwnerKind.PROFILE:
        for pod_id in pods:
            holders = sorted(
                (
                    member
                    for member, slug in org.profiles.get(pod_id, {}).items()
                    if slug == rule.owner.profile and member in org.names
                ),
                key=lambda member: (org.names[member].casefold(), member),
            )
            if holders:
                return holders[0], pod_id
    if kind is OwnerKind.DECISION_OWNER and org.decider:
        return org.decider, None
    return None


def _scrum_master(pod: GraphNode, people: _People) -> str | None:
    """A pod's scrum master: its escalation contact, else a member holding the role."""
    contact = escalation_contacts_from_metadata(pod.metadata).scrum_master
    if contact is not None:
        if contact.member_id and contact.member_id in people.names:
            return contact.member_id
        if contact.chat_external_id and contact.chat_external_id in people.chat_ids:
            return people.chat_ids[contact.chat_external_id]
    holders = sorted(
        (
            member
            for member in people.members.get(pod.id, ())
            if "sm" in people.roles.get(member, frozenset())
        ),
        key=lambda member: ((people.names.get(member) or member).casefold(), member),
    )
    return holders[0] if holders else None


def _joined_on(
    issue: ScopeIssue, changes: Sequence[tuple[date, str]], planned_on: date
) -> date | None:
    """The day an issue joined its scope after planning: created after it, else the first
    change after it that can move an issue into a scope (release, type, parent, status)."""
    created = _date_of(issue.created_at)
    if created is not None and created > planned_on:
        return created
    return next(
        (day for day, name in changes if day > planned_on and name in _JOINING_FIELDS), None
    )


def _gate_asks(
    issue: ScopeIssue, templates: Iterable[tuple[DeliveryStage, frozenset[str], frozenset[str]]]
) -> frozenset[str]:
    """The item kinds a gate already asks for on this issue: it waits at that gate."""
    if issue.stage is None:
        return frozenset()
    position = STAGE_ORDER.index(issue.stage)
    kinds: set[str] = set()
    for guarded, gate_kinds, types in templates:
        if types and (issue.issue_type or "").casefold() not in types:
            continue
        if position == STAGE_ORDER.index(guarded) - 1:
            kinds |= gate_kinds
    return frozenset(kinds)


def _date_of(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _task_key(task: GraphNode) -> str:
    key = task.metadata.get("key")
    return key if isinstance(key, str) and key else task.id


def _natural(key: str) -> tuple[str, int, str]:
    head, _, tail = key.rpartition("-")
    return (head, int(tail), key) if tail.isdigit() else (key, 0, key)


def _settings_json(settings: HygieneSettings) -> dict[str, object]:
    return {
        "enabled": settings.enabled,
        "change_labels": list(settings.change_labels),
        "stale_working_days": settings.stale_working_days,
    }


def _log_failure(tenant_id: str, exc: BaseException) -> None:
    # Imported here: the workflow definitions reach this module, and the
    # workflow sandbox refuses structlog's import-time randomness.
    import structlog

    structlog.get_logger(__name__).warning(
        "jira_hygiene_failed", tenant_id=tenant_id, error_type=type(exc).__name__
    )


__all__ = [
    "AGENT",
    "BoardView",
    "ConfigView",
    "FindingActions",
    "FindingView",
    "HygieneAsk",
    "HygieneConflict",
    "HygieneReport",
    "HygieneService",
    "PreviewIssue",
    "PreviewScope",
    "RuleRow",
    "RunSummary",
    "finding_actions",
    "reads_counts_only",
]
