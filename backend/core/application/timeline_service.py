"""The timeline per project and pod: one row per scope, from planning to the committed date.

A project's timeline lists the project's row (or one release's), its releases,
then its pods worst verdict first; a pod's timeline lists the pod's row for each
project it works on, with that project's committed date as a reference. Each
row says:

- its span, from the planning baseline (else the first kept forecast day) to the
  committed date, and the committed date's earlier values with the day each was set;
- progress (done of total, by story points when every requirement has them) and
  the scope since planning;
- the kept forecast's 50% to 85% band and verdict, and the team's latest date;
- the impact markers on the day each thing happened (DeliveryImpactService.
  timeline_markers): scope added with or without the change label, bugs, scope
  removed, reopened, closing late, blockers and the committed date's moves;
- one sentence that speaks the row.

Stored rows only: the kept forecast_days, the baselines, the snapshots, the
committed date's changes, the facts and the blockers. Nothing is simulated on a
read; today's band is the one the hourly run last kept.

Who sees what is decided per row, on the levels the Delivery tree uses
(NodeAccess): a whole row (``panel``), dates only (``dates``: span, ticks and
band; no progress, scope or markers), or no row at all (``name``):

- a project's or release's row is for the project's readers (product owner,
  manager, executive, admin), whole;
- a pod's row is whole for those readers and for the people who run or belong to
  the pod (its scrum master, its developers); a scrum master reads the project's
  other pods' dates only; a developer reads no other pod;
- blocker markers need the pod blocker read (scrum master, manager, admin, and a
  developer on their own pod); "added by" is named for product owners, managers,
  admins and scrum masters, never for executives or developers (D14).

A developer reads no project's timeline: their own pod's row is on its pod panel.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.baseline_service import BaselineService
from core.application.delivery_scope import DeliveryScopeService, NodeAccess, reads_project
from core.application.forecast_service import ForecastService, OpenRequirement
from core.application.impact_service import (
    DeliveryImpactService,
    ImpactAccess,
    ImpactDetail,
    MarkerView,
)
from core.domain.auth import Principal, Role
from core.domain.baseline import BaselineScopeKind, PlanningBaseline
from core.domain.delivery import RequirementsSnapshot
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    ForecastDayRecord,
    OpenItem,
    Release,
    Verdict,
    team_forecast,
)
from core.domain.graph import GraphNode, NodeKind
from core.domain.impact import POINTS_UNIT
from core.domain.timeline import (
    Axis,
    SentenceParts,
    axis,
    no_forecast_reason,
    row_sentence,
    worst_first,
)
from core.ports.forecast import CommitmentRepository
from core.ports.repositories import GraphRepository

#: How far back a row looks for its kept forecast days (and, without a baseline, its start).
LOOKBACK_DAYS = 120
#: The words a project's or release's timeline is refused with: a developer's own pod's row
#: is on its pod panel.
NOT_ALLOWED = "Not allowed."


@dataclass(frozen=True, kw_only=True)
class RowAccess:
    """What of one row the reader sees."""

    level: NodeAccess
    #: Blockers opened and closed: the pod blocker read's readers only.
    blockers: bool = False
    #: Who brought an added item in (D14).
    added_by: bool = False
    #: Set date or Change date, as the date routes allow it.
    can_set_date: bool = False


@dataclass(frozen=True, kw_only=True)
class CommittedTick:
    """One value the committed date took, and the day it was set."""

    target: date
    on: date


@dataclass(frozen=True, kw_only=True)
class TimelineRow:
    scope: CommitmentScope
    name: str
    project_name: str
    level: NodeAccess
    verdict: Verdict | None
    baseline_day: date | None
    baseline_reconstructed: bool
    #: Where the row's span starts: the baseline's day, else its first kept forecast day.
    start: date
    committed: date | None
    #: Every committed date in order, the current one last; earlier ones are the hollow ticks.
    committed_history: tuple[CommittedTick, ...]
    jira_release_date: date | None
    p50: date | None
    p85: date | None
    #: The day of the kept forecast the band is from (today's, or the last kept before).
    forecast_on: date | None
    forecast_reason: str | None
    team_date: date | None
    team_key: str | None
    team_undated: int
    #: Done and total, by story points when every requirement has them; whole rows only.
    done: float | None
    total: float | None
    unit: str | None
    #: Requirements frozen at planning, and in scope now; whole rows with a baseline only.
    at_baseline: int | None
    in_scope: int | None
    markers: tuple[MarkerView, ...]
    can_set_date: bool
    #: A pod's row: its project's committed date, drawn as a reference tick.
    reference: date | None
    sentence: str

    @property
    def row_id(self) -> str:
        return f"{self.scope.kind.value}:{self.scope.id}"


@dataclass(frozen=True, kw_only=True)
class TimelineView:
    axis: Axis
    rows: tuple[TimelineRow, ...]
    change_label: str


class TimelineService:
    def __init__(
        self,
        *,
        forecast_service: ForecastService,
        impact_service: DeliveryImpactService,
        baseline_service: BaselineService,
        delivery_scope_service: DeliveryScopeService,
        commitment_repository: CommitmentRepository,
        graph_repository: GraphRepository,
        policy: AuthorizationPolicy | None = None,
        today: Callable[[], date] | None = None,
        zone: tzinfo = UTC,
    ) -> None:
        self._forecasts = forecast_service
        self._impact = impact_service
        self._baselines = baseline_service
        self._scope = delivery_scope_service
        self._commitments = commitment_repository
        self._graph = graph_repository
        self._policy = policy or AuthorizationPolicy()
        self._today = today or date.today
        self._zone = zone

    # ---- the two reads ------------------------------------------------------------

    async def project_timeline(
        self,
        principal: Principal,
        project_id: str,
        *,
        release_id: str | None = None,
        as_of: date,
    ) -> TimelineView:
        """The project's (or one release's) row, its releases, then its pods worst first.

        Raises AuthorizationDenied for a reader with no row to see (a developer) and
        GraphNotFound for a project or release that is not there.
        """
        tenant_id = principal.tenant_id
        as_of = min(as_of, self._today())
        project = await self._graph.get_node(tenant_id, project_id, as_of=as_of)
        if project is None or project.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"No project {project_id!r}.")
        reads = reads_project(self._policy, principal, own=False)
        if not reads and not self._policy.can(principal, Capability.SET_POD_DATES):
            raise AuthorizationDenied(NOT_ALLOWED)
        release = await self._release(tenant_id, project_id, release_id)
        whole_project = CommitmentScope(
            kind=CommitmentScopeKind.PROJECT, id=project_id, project_id=project_id
        )
        # A pod's row is drawn against its project's committed date.
        project_target = await self._committed(tenant_id, whole_project, as_of)
        rows: list[TimelineRow] = []
        if reads:
            head = self._scope_access(principal, project_row=True, runs=False)
            if release is not None:
                rows.append(await self._release_row(principal, release, project, head, as_of))
            else:
                rows.append(
                    await self._row(
                        principal,
                        whole_project,
                        name=project.name,
                        project_name=project.name,
                        access=head,
                        as_of=as_of,
                    )
                )
                for item in await self._forecasts.releases(tenant_id, project_id, as_of=as_of):
                    rows.append(await self._release_row(principal, item, project, head, as_of))
        pods: list[TimelineRow] = []
        for pod in await self._forecasts.project_pods(tenant_id, project_id, as_of):
            access = await self._pod_access(principal, pod.id)
            if access.level is NodeAccess.NAME:
                continue
            pods.append(
                await self._row(
                    principal,
                    CommitmentScope(kind=CommitmentScopeKind.POD, id=pod.id, project_id=project_id),
                    name=pod.name,
                    project_name=project.name,
                    access=access,
                    as_of=as_of,
                    reference=project_target,
                )
            )
        rows += sorted(pods, key=lambda row: worst_first(row.verdict, row.name))
        return await self._view(tenant_id, project_id, rows, as_of)

    async def pod_timeline(self, principal: Principal, pod_id: str, *, as_of: date) -> TimelineView:
        """The pod's row for each project it works on, with that project's date beside it.

        The gate is the pod's dates' (``GET /pods/{id}/delivery``): who sets pod dates
        or reads project progress, and a developer for their own pod.
        """
        tenant_id = principal.tenant_id
        as_of = min(as_of, self._today())
        await self._scope.ensure_pod_dates(principal, pod_id)
        pod = await self._graph.get_node(tenant_id, pod_id, as_of=as_of)
        if pod is None or pod.kind is not NodeKind.POD:
            raise GraphNotFound(f"No pod {pod_id!r}.")
        access = await self._pod_access(principal, pod_id)
        if access.level is NodeAccess.NAME:
            raise AuthorizationDenied(NOT_ALLOWED)
        rows: list[TimelineRow] = []
        first_project = ""
        for project in await self._forecasts.pod_projects(tenant_id, pod_id, as_of):
            first_project = first_project or project.id
            rows.append(
                await self._row(
                    principal,
                    CommitmentScope(kind=CommitmentScopeKind.POD, id=pod_id, project_id=project.id),
                    name=pod.name,
                    project_name=project.name,
                    access=access,
                    as_of=as_of,
                    reference=await self._committed(
                        tenant_id,
                        CommitmentScope(
                            kind=CommitmentScopeKind.PROJECT,
                            id=project.id,
                            project_id=project.id,
                        ),
                        as_of,
                    ),
                )
            )
        return await self._view(tenant_id, first_project, rows, as_of)

    # ---- who sees what ------------------------------------------------------------

    def _scope_access(self, principal: Principal, *, project_row: bool, runs: bool) -> RowAccess:
        """A whole row's flags: the blocker read, "added by" (D14) and the date route."""
        names = any(principal.has_role(role) for role in (Role.PO, Role.MGR, Role.ADMIN, Role.SM))
        blockers = self._policy.can(principal, Capability.READ_POD_BLOCKERS) or (
            runs and principal.has_role(Role.DEV)
        )
        if project_row:
            can_set = self._policy.can(principal, Capability.SET_PROJECT_DATES)
        else:
            manages = principal.has_role(Role.MGR) or principal.has_role(Role.ADMIN)
            can_set = self._policy.can(principal, Capability.SET_POD_DATES) and (manages or runs)
        return RowAccess(
            level=NodeAccess.PANEL, blockers=blockers, added_by=names, can_set_date=can_set
        )

    async def _pod_access(self, principal: Principal, pod_id: str) -> RowAccess:
        """A pod's row: whole for the project's readers and for who runs or belongs to it;
        dates only for a scrum master's other pods; none for a developer's other pods."""
        runs = await self._forecasts.runs_pod(
            principal.tenant_id, pod_id, principal.subject, self._today()
        )
        if reads_project(self._policy, principal, own=False) or (
            runs and (principal.has_role(Role.SM) or principal.has_role(Role.DEV))
        ):
            whole = self._scope_access(principal, project_row=False, runs=runs)
            # A developer's own pod: whole, but never who added an item (D14).
            if not reads_project(self._policy, principal, own=False) and not principal.has_role(
                Role.SM
            ):
                return RowAccess(
                    level=NodeAccess.PANEL,
                    blockers=whole.blockers,
                    added_by=False,
                    can_set_date=whole.can_set_date,
                )
            return whole
        if self._policy.can(principal, Capability.SET_POD_DATES):
            return RowAccess(level=NodeAccess.DATES)
        return RowAccess(level=NodeAccess.NAME)

    # ---- one row --------------------------------------------------------------

    async def _release_row(
        self,
        principal: Principal,
        release: Release,
        project: GraphNode,
        access: RowAccess,
        as_of: date,
    ) -> TimelineRow:
        return await self._row(
            principal,
            CommitmentScope(
                kind=CommitmentScopeKind.RELEASE, id=release.release_id, project_id=project.id
            ),
            name=release.name,
            project_name=project.name,
            access=access,
            as_of=as_of,
            release=release,
        )

    async def _row(
        self,
        principal: Principal,
        scope: CommitmentScope,
        *,
        name: str,
        project_name: str,
        access: RowAccess,
        as_of: date,
        reference: date | None = None,
        release: Release | None = None,
    ) -> TimelineRow:
        tenant_id = principal.tenant_id
        whole = access.level is NodeAccess.PANEL
        kept = await self._forecasts.kept_days(
            tenant_id, scope, as_of - timedelta(days=LOOKBACK_DAYS), as_of
        )
        latest: ForecastDayRecord | None = kept[-1] if kept else None
        history = await self._changes(tenant_id, scope, as_of)
        committed = await self._committed(tenant_id, scope, as_of)
        baseline = await self._baseline(tenant_id, scope, as_of)
        start = baseline.planned_on if baseline is not None else (kept[0].day if kept else as_of)
        snapshot = await self._forecasts.scope_snapshot(tenant_id, scope, as_of)
        requirements = await self._forecasts.open_requirements(tenant_id, scope, as_of, snapshot)
        team = team_forecast(
            OpenItem(key=item.key, eta=item.eta, due=item.due) for item in requirements
        )
        p50 = latest.p50 if latest is not None else None
        p85 = latest.p85 if latest is not None else None
        banded = p50 is not None and p85 is not None
        reason = (
            None
            if banded
            else no_forecast_reason(
                latest.sample_days if latest is not None else None,
                latest.min_sample_days if latest is not None else None,
            )
        )
        done, total, unit = _progress(snapshot) if whole else (None, None, None)
        at_baseline = (
            await self._at_baseline(tenant_id, scope, baseline, snapshot)
            if whole and baseline is not None
            else None
        )
        markers = (
            await self._markers(
                principal, scope, access, start, as_of, requirements, baseline, snapshot
            )
            if whole
            else ()
        )
        added = [marker for marker in markers if marker.kind == "scope_added"]
        verdict = latest.verdict if latest is not None else None
        sentence = row_sentence(
            SentenceParts(
                name=name,
                done=done,
                total=total,
                unit=unit or "requirements",
                verdict=verdict,
                p85=p85 if banded else None,
                forecast_reason=reason,
                committed=committed,
                committed_history=tuple(tick.target for tick in history),
                planned=(baseline is not None) if whole else None,
                added=sum(marker.count for marker in added),
                added_without_label=sum(
                    marker.count for marker in added if marker.change_label is False
                ),
                change_label=baseline.change_label if baseline is not None else "CR",
            )
        )
        return TimelineRow(
            scope=scope,
            name=name,
            project_name=project_name,
            level=access.level,
            verdict=verdict,
            baseline_day=baseline.planned_on if baseline is not None else None,
            baseline_reconstructed=baseline.reconstructed if baseline is not None else False,
            start=start,
            committed=committed,
            committed_history=history,
            jira_release_date=(
                await self._forecasts.jira_release_date(tenant_id, release, as_of)
                if release is not None
                else None
            ),
            p50=p50 if banded else None,
            p85=p85 if banded else None,
            forecast_on=latest.day if latest is not None else None,
            forecast_reason=reason,
            team_date=team.latest,
            team_key=team.latest_key,
            team_undated=team.undated,
            done=done,
            total=total,
            unit=unit,
            at_baseline=at_baseline,
            in_scope=snapshot.total if whole and baseline is not None and snapshot else None,
            markers=markers,
            can_set_date=access.can_set_date,
            reference=reference,
            sentence=sentence,
        )

    async def _markers(
        self,
        principal: Principal,
        scope: CommitmentScope,
        access: RowAccess,
        start: date,
        as_of: date,
        requirements: Sequence[OpenRequirement],
        baseline: PlanningBaseline | None,
        snapshot: RequirementsSnapshot | None,
    ) -> tuple[MarkerView, ...]:
        return await self._impact.timeline_markers(
            principal.tenant_id,
            scope,
            start=start,
            as_of=as_of,
            access=ImpactAccess(
                detail=ImpactDetail.FULL, added_by=access.added_by, blockers=access.blockers
            ),
            open_requirements=requirements,
            baseline=baseline,
            stages=dict(snapshot.items) if snapshot is not None else {},
        )

    async def _view(
        self, tenant_id: str, project_id: str, rows: Sequence[TimelineRow], as_of: date
    ) -> TimelineView:
        drawn = axis(
            (row.start for row in rows),
            [
                day
                for row in rows
                for day in (
                    row.committed,
                    *(tick.target for tick in row.committed_history),
                    row.p85,
                    row.team_date,
                    row.jira_release_date,
                    row.reference,
                )
            ],
            as_of,
        )
        baseline = (
            await self._baselines.baseline_on(
                tenant_id, BaselineScopeKind.PROJECT, project_id, as_of
            )
            if project_id
            else None
        )
        return TimelineView(
            axis=drawn,
            rows=tuple(rows),
            change_label=baseline.change_label if baseline is not None else "CR",
        )

    # ---- lookups ----------------------------------------------------------------

    async def _release(
        self, tenant_id: str, project_id: str, release_id: str | None
    ) -> Release | None:
        if not release_id:
            return None
        release = await self._forecasts.release(tenant_id, release_id)
        if release.project_id != project_id:
            raise GraphNotFound(f"No release {release_id!r} in project {project_id!r}.")
        return release

    async def _changes(
        self, tenant_id: str, scope: CommitmentScope, as_of: date
    ) -> tuple[CommittedTick, ...]:
        """The committed dates set by the end of ``as_of``, oldest first; a cleared date is
        no tick."""
        return tuple(
            CommittedTick(target=change.target_date, on=self._local_day(change.changed_at))
            for change in await self._commitments.changes(tenant_id, scope)
            if change.target_date is not None and self._local_day(change.changed_at) <= as_of
        )

    async def _committed(self, tenant_id: str, scope: CommitmentScope, as_of: date) -> date | None:
        """The committed date as it stood at the end of ``as_of`` (a cleared one is None)."""
        changes = [
            change
            for change in await self._commitments.changes(tenant_id, scope)
            if self._local_day(change.changed_at) <= as_of
        ]
        return changes[-1].target_date if changes else None

    async def _baseline(
        self, tenant_id: str, scope: CommitmentScope, as_of: date
    ) -> PlanningBaseline | None:
        """The release's own baseline, else the project's: a pod reads its project's."""
        if scope.kind is CommitmentScopeKind.RELEASE:
            own = await self._baselines.baseline_on(
                tenant_id, BaselineScopeKind.RELEASE, scope.id, as_of
            )
            if own is not None:
                return own
        return await self._baselines.baseline_on(
            tenant_id, BaselineScopeKind.PROJECT, scope.project_id, as_of
        )

    async def _at_baseline(
        self,
        tenant_id: str,
        scope: CommitmentScope,
        baseline: PlanningBaseline,
        snapshot: RequirementsSnapshot | None,
    ) -> int:
        """The requirements frozen at planning that are the scope's: all of a project's (or a
        release's own baseline's); a pod's or release's share of its project's baseline."""
        own = scope.kind is CommitmentScopeKind.PROJECT or (
            scope.kind is CommitmentScopeKind.RELEASE
            and baseline.scope_kind is BaselineScopeKind.RELEASE
        )
        if own:
            return len(baseline.keys)
        then = await self._forecasts.scope_snapshot(tenant_id, scope, baseline.planned_on)
        keys = set(then.items) if then is not None else set(snapshot.items if snapshot else {})
        return len(baseline.keys & keys)

    def _local_day(self, moment: datetime) -> date:
        return moment.astimezone(self._zone).date() if moment.tzinfo else moment.date()


def _progress(
    snapshot: RequirementsSnapshot | None,
) -> tuple[float | None, float | None, str | None]:
    """Done and total: by story points when every requirement carries them, else by count."""
    if snapshot is None:
        return None, None, None
    if snapshot.has_points and snapshot.points_total > 0:
        return snapshot.points_done, snapshot.points_total, POINTS_UNIT
    return float(snapshot.done), float(snapshot.total), "requirements"
