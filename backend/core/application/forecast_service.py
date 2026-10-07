"""Committed delivery dates, releases, and whether each will be met.

A project's date is set by its product owner or manager, a pod's part of a
project by the pod's scrum master, and a release's by the project's owners;
a release without one uses its Jira release date. The forecast for each scope
puts what history says beside what the team's own dates say, and names every
reason the verdict is what it is: dates that moved, pods committed later than
their project, a Jira release date that disagrees, requirements with no date.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from core.application.checkin_drift import CHECKIN_DRIFT_FACT_SOURCE, ETA_STATED
from core.application.delivery_service import DeliveryService
from core.domain.delivery import DeliveryStage, RequirementsSnapshot
from core.domain.errors import GraphNotFound
from core.domain.forecast import (
    HISTORY_DAYS,
    Commitment,
    CommitmentError,
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    HistoryForecast,
    OpenItem,
    Release,
    ReleaseMatch,
    ReleaseMatchKind,
    TeamForecast,
    Verdict,
    commitment_from_changes,
    daily_completions,
    fix_version_dates,
    history_forecast,
    split_names,
    team_forecast,
    validated_note,
    validated_target,
    verdict,
)
from core.domain.graph import EdgeKind, GraphNode, NodeKind
from core.ports.forecast import CommitmentRepository, ReleaseRepository
from core.ports.repositories import GraphRepository, TimeSeriesRepository

#: Past this many working days apart, history and the team disagree.
DISAGREEMENT_WORKING_DAYS = 5
_ETA_FACT_LIMIT = 5000


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, kw_only=True)
class ScopeDeliveryView:
    scope: CommitmentScope
    name: str
    commitment: Commitment
    #: The date the verdict is measured against.
    target: date | None
    #: "committed" or "jira_release"; None without a target.
    target_source: str | None
    jira_release_date: date | None
    history: HistoryForecast
    team: TeamForecast
    verdict: Verdict
    reasons: tuple[str, ...]
    total: int
    open: int
    #: Display names of the people who changed the date, by their id.
    actor_names: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ProjectDeliveryView:
    project: ScopeDeliveryView
    pods: tuple[ScopeDeliveryView, ...]
    releases: tuple[ScopeDeliveryView, ...]


@dataclass(frozen=True, kw_only=True)
class ReleaseCandidate:
    """A fix version or label the project's issues carry, as a release could be defined."""

    kind: ReleaseMatchKind
    value: str
    issues: int
    release_date: date | None


class ForecastService:
    def __init__(
        self,
        *,
        graph_repository: GraphRepository,
        delivery_service: DeliveryService,
        commitment_repository: CommitmentRepository,
        release_repository: ReleaseRepository,
        time_series_repository: TimeSeriesRepository,
        clock: Callable[[], datetime] = _utc_now,
        today: Callable[[], date] | None = None,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._graph = graph_repository
        self._delivery = delivery_service
        self._commitments = commitment_repository
        self._releases = release_repository
        self._facts = time_series_repository
        self._clock = clock
        self._today = today or (lambda: clock().date())
        self._new_id = new_id

    # ---- releases ------------------------------------------------------

    async def releases(
        self, tenant_id: str, project_id: str, *, as_of: date | None = None
    ) -> list[Release]:
        await self._project(tenant_id, project_id, as_of)
        return sorted(
            await self._releases.list_for_project(tenant_id, project_id),
            key=lambda release: release.name.casefold(),
        )

    async def release(self, tenant_id: str, release_id: str) -> Release:
        release = await self._releases.get(tenant_id, release_id)
        if release is None:
            raise GraphNotFound(f"No release {release_id!r}.")
        return release

    async def save_release(
        self,
        tenant_id: str,
        project_id: str,
        *,
        release_id: str | None,
        name: str,
        match: ReleaseMatch,
        actor: str,
    ) -> Release:
        await self._project(tenant_id, project_id)
        clean_name = " ".join(name.split())
        value = " ".join(match.value.split())
        if not clean_name or not value:
            raise CommitmentError("A release needs a name and the fix version or label it is.")
        if len(clean_name) > 120 or len(value) > 200:
            raise CommitmentError("That release name or fix version is too long.")
        if release_id is not None:
            existing = await self.release(tenant_id, release_id)
            if existing.project_id != project_id:
                raise GraphNotFound(f"No release {release_id!r} in project {project_id!r}.")
        release = Release(
            tenant_id=tenant_id,
            release_id=release_id or self._new_id(),
            project_id=project_id,
            name=clean_name,
            match=ReleaseMatch(kind=match.kind, value=value),
            updated_at=self._clock(),
            updated_by=actor,
        )
        await self._releases.save(release)
        return release

    async def remove_release(self, tenant_id: str, release_id: str) -> None:
        await self._releases.delete(tenant_id, release_id)

    async def release_candidates(self, tenant_id: str, project_id: str) -> list[ReleaseCandidate]:
        """The fix versions and labels on the project's requirements, most common first."""
        await self._project(tenant_id, project_id)
        tasks = await self._delivery.scope_tasks(tenant_id, project_id, self._today())
        versions: Counter[str] = Counter()
        labels: Counter[str] = Counter()
        dates: dict[str, date] = {}
        for task in tasks:
            versions.update(split_names(task.metadata.get("fix_versions")))
            labels.update(split_names(task.metadata.get("labels")))
            dates.update(fix_version_dates(task.metadata.get("fix_version_dates")))
        candidates = [
            ReleaseCandidate(
                kind=ReleaseMatchKind.FIX_VERSION,
                value=name,
                issues=count,
                release_date=dates.get(name),
            )
            for name, count in versions.most_common()
        ]
        candidates += [
            ReleaseCandidate(
                kind=ReleaseMatchKind.LABEL, value=name, issues=count, release_date=None
            )
            for name, count in labels.most_common()
        ]
        return candidates

    # ---- dates -----------------------------------------------------------

    async def set_date(
        self,
        tenant_id: str,
        scope: CommitmentScope,
        target: date | None,
        *,
        note: str,
        actor: str,
    ) -> Commitment:
        await self._check_scope(tenant_id, scope)
        change = DateChange(
            tenant_id=tenant_id,
            scope=scope,
            target_date=validated_target(target, self._today()),
            changed_at=self._clock(),
            changed_by=actor,
            note=validated_note(note),
        )
        await self._commitments.append(change)
        return commitment_from_changes(scope, await self._commitments.changes(tenant_id, scope))

    async def pod_projects(self, tenant_id: str, pod_id: str, as_of: date) -> list[GraphNode]:
        """The projects a pod works on, whose dates its scrum master sets."""
        pod = await self._graph.get_node(tenant_id, pod_id, as_of=as_of)
        if pod is None or pod.kind is not NodeKind.POD:
            raise GraphNotFound(f"No pod {pod_id!r}.")
        project_ids = {
            edge.from_node_id
            for edge in await self._graph.list_edges(
                tenant_id, to_node_id=pod_id, kind=EdgeKind.CONTAINS
            )
            if edge.is_active_on(as_of)
        }
        projects = [
            node
            for project_id in sorted(project_ids)
            if (node := await self._graph.get_node(tenant_id, project_id, as_of=as_of)) is not None
            and node.kind is NodeKind.PROJECT
        ]
        return projects

    async def runs_pod(self, tenant_id: str, pod_id: str, subject: str, as_of: date) -> bool:
        """Whether ``subject`` is the pod's scrum master contact or one of its members."""
        pod = await self._graph.get_node(tenant_id, pod_id, as_of=as_of)
        if pod is None or pod.kind is not NodeKind.POD:
            return False
        if pod.metadata.get("escalation_sm_member_id") == subject:
            return True
        return any(
            edge.to_node_id == subject and edge.is_active_on(as_of)
            for edge in await self._graph.list_edges(
                tenant_id, from_node_id=pod_id, kind=EdgeKind.CONTAINS
            )
        )

    async def runs_project_pod(self, tenant_id: str, project_id: str, subject: str) -> bool:
        """Whether ``subject`` runs a pod working on the project today, by :meth:`runs_pod`."""
        today = self._today()
        for pod in await self._project_pods(tenant_id, project_id, today):
            if await self.runs_pod(tenant_id, pod.id, subject, today):
                return True
        return False

    # ---- forecasts -------------------------------------------------------

    async def project_delivery(
        self, tenant_id: str, project_id: str, as_of: date
    ) -> ProjectDeliveryView:
        project = await self._project(tenant_id, project_id, as_of)
        changes = await self._commitments.changes_for_project(tenant_id, project_id)
        by_scope: dict[str, list[DateChange]] = {}
        for change in changes:
            by_scope.setdefault(change.scope.key, []).append(change)
        context = await self._context(tenant_id, project_id, as_of)
        project_scope = CommitmentScope(
            kind=CommitmentScopeKind.PROJECT, id=project_id, project_id=project_id
        )
        project_commitment = commitment_from_changes(
            project_scope, by_scope.get(project_scope.key, [])
        )
        pods = []
        for pod in await self._project_pods(tenant_id, project_id, as_of):
            scope = CommitmentScope(kind=CommitmentScopeKind.POD, id=pod.id, project_id=project_id)
            pods.append(
                await self._scope_view(
                    scope,
                    name=pod.name,
                    commitment=commitment_from_changes(scope, by_scope.get(scope.key, [])),
                    context=context,
                    keys=context.pod_keys.get(pod.id, frozenset()),
                    as_of=as_of,
                )
            )
        releases = []
        for release in await self.releases(tenant_id, project_id, as_of=as_of):
            scope = CommitmentScope(
                kind=CommitmentScopeKind.RELEASE, id=release.release_id, project_id=project_id
            )
            releases.append(
                await self._release_view(
                    tenant_id,
                    release,
                    commitment_from_changes(scope, by_scope.get(scope.key, [])),
                    context,
                    as_of,
                )
            )
        project_view = await self._scope_view(
            project_scope,
            name=project.name,
            commitment=project_commitment,
            context=context,
            keys=None,
            as_of=as_of,
            extra_reasons=_pod_reasons(project_commitment.target_date, pods),
        )
        return ProjectDeliveryView(project=project_view, pods=tuple(pods), releases=tuple(releases))

    async def scope_delivery(
        self, tenant_id: str, scope: CommitmentScope, as_of: date
    ) -> ScopeDeliveryView:
        """One scope's forecast, as the project view would show it."""
        view = await self.project_delivery(tenant_id, scope.project_id, as_of)
        for candidate in (view.project, *view.pods, *view.releases):
            if candidate.scope == scope:
                return candidate
        raise GraphNotFound(f"No {scope.kind.value} {scope.id!r} in project {scope.project_id!r}.")

    # ---- internals ---------------------------------------------------------

    async def _release_view(
        self,
        tenant_id: str,
        release: Release,
        commitment: Commitment,
        context: _Context,
        as_of: date,
    ) -> ScopeDeliveryView:
        snapshot = await self._delivery.snapshot(tenant_id, release.project_id, as_of, release)
        keys = frozenset(snapshot.items) if snapshot is not None else frozenset()
        # The project's history, kept to the release's issues: a release added
        # today forecasts from the project's past days like a pod does.
        history = [_restricted(item, keys) for item in context.history]
        jira_date = _release_date(release, context.tasks.values())
        scope = CommitmentScope(
            kind=CommitmentScopeKind.RELEASE, id=release.release_id, project_id=release.project_id
        )
        reasons: list[str] = []
        if jira_date is not None and commitment.target_date is not None:
            if jira_date != commitment.target_date:
                reasons.append(
                    f"Jira's release date is {_day(jira_date)}; the committed date is "
                    f"{_day(commitment.target_date)}."
                )
        return self._view(
            scope,
            name=release.name,
            commitment=commitment,
            jira_release_date=jira_date,
            snapshot=snapshot,
            history=history,
            context=context,
            keys=keys,
            as_of=as_of,
            extra_reasons=reasons,
        )

    async def _scope_view(
        self,
        scope: CommitmentScope,
        *,
        name: str,
        commitment: Commitment,
        context: _Context,
        keys: frozenset[str] | None,
        as_of: date,
        extra_reasons: Sequence[str] = (),
    ) -> ScopeDeliveryView:
        snapshot = context.snapshot
        if snapshot is not None and keys is not None:
            snapshot = _restricted(snapshot, keys)
        history = context.history
        if keys is not None:
            history = [_restricted(item, keys) for item in history]
        return self._view(
            scope,
            name=name,
            commitment=commitment,
            jira_release_date=None,
            snapshot=snapshot,
            history=history,
            context=context,
            keys=keys,
            as_of=as_of,
            extra_reasons=extra_reasons,
        )

    def _view(
        self,
        scope: CommitmentScope,
        *,
        name: str,
        commitment: Commitment,
        jira_release_date: date | None,
        snapshot: RequirementsSnapshot | None,
        history: Sequence[RequirementsSnapshot],
        context: _Context,
        keys: frozenset[str] | None,
        as_of: date,
        extra_reasons: Sequence[str],
    ) -> ScopeDeliveryView:
        series = [*history, snapshot] if snapshot is not None else list(history)
        by_points = bool(series) and all(item.has_points for item in series)
        open_keys = (
            [key for key, stage in snapshot.items.items() if stage is not DeliveryStage.PRODUCTION]
            if snapshot is not None
            else []
        )
        remaining: float
        if snapshot is None:
            remaining = 0
        elif by_points:
            remaining = snapshot.points_total - snapshot.points_done
        else:
            remaining = float(len(open_keys))
        forecast = history_forecast(
            daily_completions(series, keys=keys, by_points=by_points),
            remaining,
            start=as_of,
            seed=f"{scope.key}:{as_of.isoformat()}",
            unit="story points" if by_points else "requirements",
        )
        team = team_forecast(
            OpenItem(
                key=key,
                eta=context.etas.get(key),
                due=_due(context.tasks.get(key)),
            )
            for key in open_keys
        )
        target = commitment.target_date or jira_release_date
        target_source = (
            "committed"
            if commitment.target_date is not None
            else "jira_release"
            if jira_release_date is not None
            else None
        )
        outcome = verdict(target, forecast, team)
        reasons = _reasons(
            commitment,
            target,
            target_source,
            forecast,
            team,
            as_of,
            list(extra_reasons),
            context.names,
        )
        return ScopeDeliveryView(
            scope=scope,
            name=name,
            commitment=commitment,
            target=target,
            target_source=target_source,
            jira_release_date=jira_release_date,
            history=forecast,
            team=team,
            verdict=outcome,
            reasons=tuple(reasons),
            total=snapshot.total if snapshot is not None else 0,
            open=len(open_keys),
            actor_names={
                change.changed_by: context.names[change.changed_by]
                for change in commitment.changes
                if change.changed_by in context.names
            },
        )

    async def _context(self, tenant_id: str, project_id: str, as_of: date) -> _Context:
        snapshot = await self._delivery.snapshot(tenant_id, project_id, as_of)
        history = await self._delivery.history(
            tenant_id, project_id, as_of - timedelta(days=HISTORY_DAYS), as_of - timedelta(days=1)
        )
        tasks = {
            _task_key(task): task
            for task in await self._delivery.scope_tasks(tenant_id, project_id, as_of)
        }
        return _Context(
            snapshot=snapshot,
            history=history,
            tasks=tasks,
            etas=await self._etas(tenant_id, set(tasks)),
            pod_keys=await self._pod_keys(tenant_id, project_id, tasks, as_of),
            names={
                node.id: node.name
                for node in await self._graph.list_nodes(tenant_id, NodeKind.DEVELOPER, as_of=as_of)
            },
        )

    async def _etas(self, tenant_id: str, keys: set[str]) -> dict[str, date]:
        """The last day of each issue's latest check-in ETA, the assignee's first."""
        facts = await self._facts.list_recent_facts(
            tenant_id, sources=(CHECKIN_DRIFT_FACT_SOURCE,), limit=_ETA_FACT_LIMIT
        )
        latest: dict[str, tuple[datetime, date]] = {}
        for fact in sorted(facts, key=lambda item: item.observed_at):
            payload = fact.payload
            key = payload.get("issue_key")
            if payload.get("kind") != ETA_STATED or not isinstance(key, str) or key not in keys:
                continue
            day = _iso_date(payload.get("eta_date"))
            if day is not None:
                latest[key] = (fact.observed_at, day)
        return {key: day for key, (_seen, day) in latest.items()}

    async def _pod_keys(
        self, tenant_id: str, project_id: str, tasks: Mapping[str, GraphNode], as_of: date
    ) -> dict[str, frozenset[str]]:
        """Each of the project's pods' requirement keys: the tasks below the pod."""
        parents: dict[str, list[str]] = {}
        for edge in await self._graph.list_edges(tenant_id, kind=EdgeKind.CONTAINS):
            if edge.is_active_on(as_of):
                parents.setdefault(edge.to_node_id, []).append(edge.from_node_id)
        pods = {pod.id for pod in await self._project_pods(tenant_id, project_id, as_of)}
        keys: dict[str, set[str]] = {pod_id: set() for pod_id in pods}
        for key, task in tasks.items():
            for ancestor in _ancestors(task.id, parents):
                if ancestor in keys:
                    keys[ancestor].add(key)
        return {pod_id: frozenset(found) for pod_id, found in keys.items()}

    async def _project_pods(self, tenant_id: str, project_id: str, as_of: date) -> list[GraphNode]:
        pods = []
        for edge in await self._graph.list_edges(
            tenant_id, from_node_id=project_id, kind=EdgeKind.CONTAINS
        ):
            if not edge.is_active_on(as_of):
                continue
            node = await self._graph.get_node(tenant_id, edge.to_node_id, as_of=as_of)
            if node is not None and node.kind is NodeKind.POD:
                pods.append(node)
        return sorted(pods, key=lambda pod: pod.name.casefold())

    async def _check_scope(self, tenant_id: str, scope: CommitmentScope) -> None:
        await self._project(tenant_id, scope.project_id)
        if scope.kind is CommitmentScopeKind.PROJECT and scope.id != scope.project_id:
            raise GraphNotFound("A project's date is set on the project itself.")
        if scope.kind is CommitmentScopeKind.POD:
            pods = await self._project_pods(tenant_id, scope.project_id, self._today())
            if scope.id not in {pod.id for pod in pods}:
                raise GraphNotFound(f"Pod {scope.id!r} does not work on {scope.project_id!r}.")
        if scope.kind is CommitmentScopeKind.RELEASE:
            release = await self.release(tenant_id, scope.id)
            if release.project_id != scope.project_id:
                raise GraphNotFound(f"No release {scope.id!r} in project {scope.project_id!r}.")

    async def _project(
        self, tenant_id: str, project_id: str, as_of: date | None = None
    ) -> GraphNode:
        """The project, as of ``as_of`` when given: a past day still finds one deleted since."""
        project = await self._graph.get_node(tenant_id, project_id, as_of=as_of)
        if project is None or project.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"No project {project_id!r}.")
        return project


@dataclass(frozen=True, kw_only=True)
class _Context:
    snapshot: RequirementsSnapshot | None
    history: list[RequirementsSnapshot]
    tasks: Mapping[str, GraphNode]
    etas: Mapping[str, date]
    pod_keys: Mapping[str, frozenset[str]]
    names: Mapping[str, str]


def _reasons(
    commitment: Commitment,
    target: date | None,
    target_source: str | None,
    history: HistoryForecast,
    team: TeamForecast,
    as_of: date,
    extra: list[str],
    names: Mapping[str, str],
) -> list[str]:
    reasons = _target_reasons(commitment, target, target_source, history, names)
    if history.remaining <= 0:
        return [*reasons, "Every requirement is in production.", *extra]
    return [*reasons, *_forecast_reasons(history, team, as_of), *extra]


def _target_reasons(
    commitment: Commitment,
    target: date | None,
    target_source: str | None,
    history: HistoryForecast,
    names: Mapping[str, str],
) -> list[str]:
    if target_source == "jira_release" and target is not None:
        return [f"No date committed; Jira's release date {_day(target)} is used."]
    if target_source != "committed" or not commitment.changes or target is None:
        return ["No delivery date is committed yet."] if history.remaining > 0 else []
    actor = commitment.changes[-1].changed_by
    line = f"Committed for {_day(target)} by {names.get(actor, actor)}"
    if commitment.times_moved and commitment.moved_days:
        later = "later" if commitment.moved_days > 0 else "earlier"
        line += (
            f"; moved {_times(commitment.times_moved)}, {abs(commitment.moved_days)} days "
            f"{later} than first set"
        )
    return [line + "."]


def _forecast_reasons(history: HistoryForecast, team: TeamForecast, as_of: date) -> list[str]:
    reasons: list[str] = []
    if history.p50 is not None and history.p85 is not None:
        reasons.append(
            f"History: 50% likely by {_day(history.p50)}, 85% by {_day(history.p85)} "
            f"({_amount(history.remaining, history.unit)} to go; "
            f"{_amount(history.completed_in_sample, history.unit)} finished in the last "
            f"{history.sample_days} working days)."
        )
    elif history.reason:
        reasons.append(history.reason)
    if team.latest is not None:
        late = " (past its date)" if team.latest < as_of else ""
        reasons.append(
            f"Team dates: the latest open requirement is due {_day(team.latest)}"
            f" ({team.latest_key}){late}."
        )
    if team.undated:
        reasons.append(
            f"{_count(team.undated, 'open requirement has', 'open requirements have')} "
            "no ETA or due date."
        )
    if history.p85 is not None and team.latest is not None:
        apart = _working_days_between(history.p85, team.latest)
        if apart > DISAGREEMENT_WORKING_DAYS:
            reasons.append(
                f"History and the team's dates are {apart} working days apart; "
                "one of them is wrong."
            )
    return reasons


def _pod_reasons(project_target: date | None, pods: Sequence[ScopeDeliveryView]) -> list[str]:
    if project_target is None:
        return []
    return [
        f"{pod.name} committed {_day(pod.commitment.target_date)}, after the project's "
        f"{_day(project_target)}."
        for pod in pods
        if pod.commitment.target_date is not None and pod.commitment.target_date > project_target
    ]


def _release_date(release: Release, tasks: Iterable[GraphNode]) -> date | None:
    if release.match.kind is not ReleaseMatchKind.FIX_VERSION:
        return None
    wanted = release.match.value.casefold()
    for task in tasks:
        for name, released in fix_version_dates(task.metadata.get("fix_version_dates")).items():
            if name.casefold() == wanted:
                return released
    return None


def _restricted(snapshot: RequirementsSnapshot, keys: frozenset[str]) -> RequirementsSnapshot:
    """The snapshot with only ``keys``' requirements (one pod's part); counts by item."""
    items = {key: stage for key, stage in snapshot.items.items() if key in keys}
    counts = {stage: 0 for stage in DeliveryStage}
    for stage in items.values():
        counts[stage] += 1
    return RequirementsSnapshot(
        tenant_id=snapshot.tenant_id,
        project_id=snapshot.project_id,
        day=snapshot.day,
        stage_counts=counts,
        stage_points={stage: 0.0 for stage in DeliveryStage},
        has_points=False,
        excluded=0,
        unmapped_statuses=(),
        items=items,
        titles={key: title for key, title in snapshot.titles.items() if key in keys},
        computed_at=snapshot.computed_at,
    )


def _ancestors(node_id: str, parents: Mapping[str, Sequence[str]]) -> set[str]:
    seen: set[str] = set()
    queue = list(parents.get(node_id, ()))
    while queue:
        current = queue.pop()
        if current not in seen:
            seen.add(current)
            queue.extend(parents.get(current, ()))
    return seen


def _due(task: GraphNode | None) -> date | None:
    return _iso_date(task.metadata.get("due_date")) if task is not None else None


def _iso_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _task_key(task: GraphNode) -> str:
    key = task.metadata.get("key")
    return key if isinstance(key, str) and key else task.id


def _working_days_between(first: date, second: date) -> int:
    start, end = sorted((first, second))
    days = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def _day(day: date | None) -> str:
    if day is None:
        return "no date"
    return f"{day:%a} {day.day} {day:%b %Y}"


def _amount(value: float, unit: str) -> str:
    number = f"{value:g}"
    if unit == "requirements":
        return f"{number} {'requirement' if value == 1 else 'requirements'}"
    return f"{number} {'story point' if value == 1 else 'story points'}"


def _count(count: int, one: str, many: str) -> str:
    return f"1 {one}" if count == 1 else f"{count} {many}"


def _times(count: int) -> str:
    return "once" if count == 1 else "twice" if count == 2 else f"{count} times"
