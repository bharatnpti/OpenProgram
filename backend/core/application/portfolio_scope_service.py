"""Scope control across the portfolio, in one read: what joined after planning, and is Jira tidy?

For every project the person reads:

- **against the plan**: the planning baseline's day and size, the requirements
  added since (keys, never who), how many of those lack the baseline's own
  change label (tenant data, never a hard-coded one) and their share, and what
  was removed. Read from each project's latest stored snapshot, so nothing is
  forecast. Its readers are the readers of a project's baselines
  (read_project_progress: product owner, manager, executive, admin);
- **Jira hygiene**: the open findings of the enabled rules, with the issue, the
  rule, who owns it and since when. Read once for the tenant, then kept to what
  each reader's project board would show them (readiness_access): an executive
  reads each rule's counts and no issue or owner, a scrum master the pods they
  run of their own projects.

A finding is never sent from here: a rule switched to ask its owner asks
through the day report, so a finding says so instead of offering a send.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.baseline_service import BaselineService
from core.application.forecast_service import ForecastService
from core.application.portfolio_index import IndexedProject, PortfolioIndex, PortfolioIndexReader
from core.application.readiness_access import project_viewer
from core.application.release_readiness_service import ReleaseReadinessService, Viewer
from core.domain.auth import Principal
from core.domain.baseline import BaselineScopeKind, since_baseline
from core.domain.errors import AuthorizationDenied
from core.domain.forecast import split_names
from core.domain.hygiene import HygieneFinding, HygieneRule, HygieneState
from core.domain.portfolio import AgainstPlan
from core.domain.release_readiness import ScopeKind
from core.domain.status import local_date
from core.ports.delivery import RequirementsSnapshotRepository
from core.ports.hygiene import HygieneRepository

#: The keys a row lists of the added requirements; the rest are counted.
MAX_KEYS = 10
#: The most findings one read lists; the rest are counted.
MAX_FINDINGS = 200
SNAPSHOT_LOOKBACK_DAYS = 7


@dataclass(frozen=True, kw_only=True)
class ScopeProjectView:
    id: str
    name: str
    program_id: str | None
    program_name: str | None
    #: None without a baseline, or for a reader who reads no baselines.
    plan: AgainstPlan | None
    added_keys: tuple[str, ...]
    without_label_keys: tuple[str, ...]
    #: The day the scope is read from: its latest stored snapshot.
    scope_day: date | None
    hygiene_open: int


@dataclass(frozen=True, kw_only=True)
class RuleCount:
    id: str
    name: str
    #: The rule asks each owner in the day report (an admin switched it to ask).
    asks: bool
    open: int


@dataclass(frozen=True, kw_only=True)
class FindingLine:
    id: str
    issue_key: str
    title: str
    rule_id: str
    rule_name: str
    project_id: str
    project_name: str
    #: The owner by name; None for a reader who sees no person, or when nobody is named.
    owner_name: str | None
    #: The team the issue sits in, for a reader who sees the team instead.
    owner_team: str | None
    found_on: date
    open_days: int
    #: The rule asks its owner in the day report: nothing to send from here.
    asks: bool
    #: Opened on stale Jira data: listed, but kept out of the day report until fresh.
    held: bool


@dataclass(frozen=True, kw_only=True)
class PortfolioScopeView:
    as_of: date
    reads_baselines: bool
    hygiene_enabled: bool
    #: The reader reads rule counts only: no issue and no owner.
    counts_only: bool
    projects: tuple[ScopeProjectView, ...]
    rules: tuple[RuleCount, ...]
    findings: tuple[FindingLine, ...]
    findings_total: int


class PortfolioScopeService:
    def __init__(
        self,
        *,
        index: PortfolioIndexReader,
        baselines: BaselineService,
        snapshots: RequirementsSnapshotRepository,
        hygiene: HygieneRepository,
        forecasts: ForecastService,
        readiness: ReleaseReadinessService,
        policy: AuthorizationPolicy | None = None,
        zone: ZoneInfo | None = None,
    ) -> None:
        self._index = index
        self._baselines = baselines
        self._snapshots = snapshots
        self._hygiene = hygiene
        self._forecasts = forecasts
        self._readiness = readiness
        self._policy = policy or AuthorizationPolicy()
        # A finding's first sighting is dated in the tenant's zone, the calendar
        # ``as_of`` is on; the router passes ServiceRegistry.tenant_zone.
        self._zone = zone or ZoneInfo("UTC")

    async def read(
        self,
        principal: Principal,
        as_of: date,
        *,
        search: str = "",
        program_id: str | None = None,
        project_id: str | None = None,
    ) -> PortfolioScopeView:
        tenant_id = principal.tenant_id
        index = await self._index.read(principal, as_of)
        projects = [project for project in index.projects if project.matches(search)]
        if program_id:
            projects = [p for p in projects if program_id in {g.id for g in p.programs}]
        if project_id:
            projects = [p for p in projects if p.id == project_id]
        reads_baselines = self._policy.can(principal, Capability.READ_PROJECT_PROGRESS)
        viewers = {
            project.id: viewer
            for project in projects
            if (viewer := await self._viewer(principal, project.id)) is not None
        }
        counts_only = any(not viewer.sees_drafts for viewer in viewers.values())
        settings = await self._hygiene.get_settings(tenant_id)
        enabled = settings is not None and settings.enabled
        rules = {
            rule.rule_id: rule for rule in await self._hygiene.list_rules(tenant_id) if rule.enabled
        }
        found = (
            _findings_by_project(
                await self._hygiene.list_findings(tenant_id, states=(HygieneState.OPEN,)),
                rules,
                projects,
                viewers,
                index,
            )
            if enabled and rules
            else {}
        )
        rows = [
            await self._row(tenant_id, project, index, as_of, reads_baselines, found)
            for project in projects
        ]
        rows.sort(key=_scope_order)
        listed = sorted(
            (
                _line(finding, rule, project, index, as_of, self._zone, named=not counts_only)
                for project in projects
                for finding, rule in found.get(project.id, [])
            ),
            key=lambda line: (line.found_on, line.project_name.casefold(), line.issue_key),
        )
        return PortfolioScopeView(
            as_of=as_of,
            reads_baselines=reads_baselines,
            hygiene_enabled=enabled,
            counts_only=counts_only,
            projects=tuple(rows),
            rules=tuple(
                RuleCount(
                    id=rule.rule_id,
                    name=rule.name,
                    asks=rule.asks,
                    open=sum(
                        1
                        for pairs in found.values()
                        for _finding, of in pairs
                        if of.rule_id == rule.rule_id
                    ),
                )
                for rule in sorted(rules.values(), key=lambda item: item.name.casefold())
            )
            if enabled
            else (),
            findings=() if counts_only else tuple(listed[:MAX_FINDINGS]),
            findings_total=len(listed),
        )

    async def _viewer(self, principal: Principal, project_id: str) -> Viewer | None:
        """How far the reader reaches on the project's hygiene board; None when not at all."""
        try:
            return await project_viewer(
                principal, self._forecasts, self._readiness, project_id, reading=True
            )
        except AuthorizationDenied:
            return None

    async def _row(
        self,
        tenant_id: str,
        project: IndexedProject,
        index: PortfolioIndex,
        as_of: date,
        reads_baselines: bool,
        found: Mapping[str, list[tuple[HygieneFinding, HygieneRule]]],
    ) -> ScopeProjectView:
        plan: AgainstPlan | None = None
        added: tuple[str, ...] = ()
        without: tuple[str, ...] = ()
        scope_day: date | None = None
        baseline = (
            await self._baselines.baseline_on(
                tenant_id, BaselineScopeKind.PROJECT, project.id, as_of
            )
            if reads_baselines
            else None
        )
        if baseline is not None:
            snapshot = await self._snapshots.latest_before(
                tenant_id, project.id, as_of + timedelta(days=1)
            )
            if snapshot is not None and snapshot.day < as_of - timedelta(
                days=SNAPSHOT_LOOKBACK_DAYS
            ):
                snapshot = None
            current = frozenset(snapshot.items) if snapshot is not None else frozenset()
            scope_day = snapshot.day if snapshot is not None else None
            labels = {
                key: split_names(task.metadata.get("labels"))
                for key in current - baseline.keys
                if (task := index.tasks.get(key)) is not None
            }
            since = since_baseline(baseline, current, labels)
            labelled = set(since.added_with_label)
            unlabelled = tuple(key for key in since.added if key not in labelled)
            plan = AgainstPlan(
                planned_on=baseline.planned_on,
                planned=len(baseline.items),
                added=len(since.added),
                without_label=len(unlabelled),
                change_label=baseline.change_label,
                removed=len(since.removed),
            )
            added = since.added[:MAX_KEYS]
            without = unlabelled[:MAX_KEYS]
        program = project.programs[0] if project.programs else None
        return ScopeProjectView(
            id=project.id,
            name=project.name,
            program_id=program.id if program else None,
            program_name=program.name if program else None,
            plan=plan,
            added_keys=added,
            without_label_keys=without,
            scope_day=scope_day,
            hygiene_open=len(found.get(project.id, [])),
        )


def _findings_by_project(
    findings: Sequence[HygieneFinding],
    rules: Mapping[str, HygieneRule],
    projects: Sequence[IndexedProject],
    viewers: Mapping[str, Viewer],
    index: PortfolioIndex,
) -> dict[str, list[tuple[HygieneFinding, HygieneRule]]]:
    """Each project's open findings that its board would show this reader.

    A project's or a release's finding names its project; a pod's belongs to every
    project the pod works on, as the pod's board sits under each.
    """
    pods_of: dict[str, list[str]] = {}
    for project in projects:
        for pod in project.pods:
            pods_of.setdefault(pod.id, []).append(project.id)
    found: dict[str, list[tuple[HygieneFinding, HygieneRule]]] = {}
    for finding in findings:
        rule = rules.get(finding.rule_id)
        if rule is None or rule.scope.kind is not finding.scope.kind:
            continue
        if finding.scope.kind is ScopeKind.POD:
            owners = pods_of.get(finding.scope.id, [])
        else:
            owners = [finding.project_id] if finding.project_id else []
        for project_id in owners:
            viewer = viewers.get(project_id)
            if viewer is not None and viewer.sees(finding.scope):
                found.setdefault(project_id, []).append((finding, rule))
    return found


def _line(
    finding: HygieneFinding,
    rule: HygieneRule,
    project: IndexedProject,
    index: PortfolioIndex,
    as_of: date,
    zone: ZoneInfo,
    *,
    named: bool,
) -> FindingLine:
    task = index.tasks.get(finding.issue_key)
    pods = index.pods_of_issue(finding.issue_key, project) or tuple(
        node for pod_id in finding.pod_ids if (node := index.nodes.get(pod_id)) is not None
    )
    return FindingLine(
        id=finding.finding_id,
        issue_key=finding.issue_key,
        title=task.name if task is not None else "",
        rule_id=rule.rule_id,
        rule_name=rule.name,
        project_id=project.id,
        project_name=project.name,
        owner_name=index.name(finding.owner_member_id) if named else None,
        owner_team=pods[0].name if pods else None,
        found_on=(found_on := local_date(finding.first_seen_at, zone)),
        open_days=max(0, (as_of - found_on).days),
        asks=rule.asks,
        held=finding.held,
    )


def _scope_order(row: ScopeProjectView) -> tuple[object, ...]:
    """Most added after planning first, then the most hygiene findings, then the name."""
    added = row.plan.added if row.plan is not None else -1
    return (-added, -row.hygiene_open, row.name.casefold())


__all__ = [
    "FindingLine",
    "PortfolioScopeService",
    "PortfolioScopeView",
    "RuleCount",
    "ScopeProjectView",
]
