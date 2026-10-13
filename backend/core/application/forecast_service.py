"""Committed delivery dates, releases, and whether each will be met.

A project's date is set by its product owner or manager, a pod's part of a
project by the pod's scrum master, and a release's by the project's owners;
a release without one uses its Jira release date. The forecast for each scope
puts what history says beside what the team's own dates say, and names every
reason the verdict is what it is: dates that moved, pods committed later than
their project, a Jira release date that disagrees, requirements with no date.

How many working days of history a forecast needs is the tenant's setting (an
admin's, else the deployment's default); the window its samples are read from
follows it, so every scope, the history read and the day report use one rule.

Each project's, pod's and release's forecast is kept per day (forecast_days):
the hourly snapshot run keeps today's, and a one-off reconstruction rebuilds
earlier days from the stored snapshots, with each day's committed date and ETAs
as they stood that day. The history read reads those rows and replays only the
days nobody kept, and today, which is always live. A pod's row is its part of one
project's work, and the store's key has the project, so a pod that works on more
than one project keeps a row under each, read only in that project's context.

Each kept day also says why its forecast moved since the scope's day before
with a snapshot (``attribution``, core/domain/impact.py): the move replayed one
input at a time, under the day's own seed, so its parts add up exactly.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from uuid import uuid4

from core.application.checkin_drift import CHECKIN_DRIFT_FACT_SOURCE, ETA_STATED
from core.application.delivery_service import DeliveryService
from core.application.person_names import UNKNOWN_PERSON
from core.domain.delivery import DeliveryStage, RequirementsSnapshot
from core.domain.errors import GraphNotFound
from core.domain.forecast import (
    HIGHEST_MIN_SAMPLE_DAYS,
    HISTORY_DAYS,
    LOWEST_MIN_SAMPLE_DAYS,
    MIN_SAMPLE_DAYS,
    Commitment,
    CommitmentError,
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    ForecastDayRecord,
    ForecastSettings,
    ForecastSettingsError,
    HistoryForecast,
    OpenItem,
    Release,
    ReleaseMatch,
    ReleaseMatchKind,
    TeamForecast,
    Verdict,
    changes_until,
    commitment_from_changes,
    dated_completions,
    fix_version_dates,
    history_forecast,
    history_window_days,
    split_names,
    team_forecast,
    validated_min_sample_days,
    validated_note,
    validated_target,
    verdict,
)
from core.domain.graph import EdgeKind, FactEvent, GraphEdge, GraphNode, NodeKind
from core.domain.impact import ScopeDay, attribution_payload, decompose
from core.ports.forecast import (
    CommitmentRepository,
    ForecastDayRepository,
    ForecastSettingsRepository,
    ReleaseRepository,
)
from core.ports.repositories import GraphRepository, TimeSeriesRepository

#: Past this many working days apart, history and the team disagree.
DISAGREEMENT_WORKING_DAYS = 5
#: The most days of the forecast's own history one read replays.
MAX_FORECAST_HISTORY_DAYS = 90
#: The days before today a reconstruction rebuilds unless told otherwise, and the most.
RECONSTRUCT_DAYS = 90
MAX_RECONSTRUCT_DAYS = 365
_ETA_FACT_LIMIT = 5000
#: How far back a breakdown looks for the scope's day before with a snapshot.
PREVIOUS_SNAPSHOT_DAYS = 7


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
class ForecastDay:
    """What history forecast on one day with a snapshot: p50 and p85 when it could."""

    day: date
    p50: date | None
    p85: date | None
    sample_days: int
    #: Rebuilt later from the stored snapshots, not kept on the day itself.
    reconstructed: bool = False


@dataclass(frozen=True, kw_only=True)
class ForecastHistoryView:
    scope: CommitmentScope
    #: Oldest first; a day without a snapshot is left out, never guessed.
    days: tuple[ForecastDay, ...]
    #: The working days of history each day's forecast needed.
    needed_days: int = MIN_SAMPLE_DAYS


@dataclass(frozen=True, kw_only=True)
class ForecastSettingsView:
    """The forecast settings in force for a tenant, and where they come from."""

    #: Working days of history before a forecast gives dates: the tenant's, else the default.
    min_sample_days: int
    #: The deployment's default (OPENPROGRAM_FORECAST_MIN_HISTORY_DAYS).
    default_min_sample_days: int
    #: True while the tenant has set none and the default applies.
    is_default: bool
    #: The calendar days the samples are read from; they follow the minimum.
    window_days: int
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReconstructionSummary:
    """What one reconstruction of the forecast's earlier days did."""

    first_day: date
    last_day: date
    #: Projects, pods and releases reconstructed.
    scopes: int
    #: Days written, each a scope and a day with a stored snapshot.
    written: int
    #: Days left as they were, because the hourly run kept them on the day.
    kept: int


@dataclass(frozen=True, kw_only=True)
class ReleaseCandidate:
    """A fix version or label the project's issues carry, as a release could be defined."""

    kind: ReleaseMatchKind
    value: str
    issues: int
    release_date: date | None


@dataclass(frozen=True, kw_only=True)
class OpenRequirement:
    """An open requirement of a scope on a day, with the dates the team gave it."""

    key: str
    title: str
    stage: DeliveryStage
    #: The latest check-in ETA stated by the end of the day.
    eta: date | None
    #: The tracker's due date that day (its snapshot's, else the issue's now).
    due: date | None


@dataclass(frozen=True, kw_only=True)
class ScopeRequirements:
    """A scope's forecast on a day, and its open requirements with their dates."""

    view: ScopeDeliveryView
    open: tuple[OpenRequirement, ...]


class ForecastService:
    def __init__(
        self,
        *,
        graph_repository: GraphRepository,
        delivery_service: DeliveryService,
        commitment_repository: CommitmentRepository,
        release_repository: ReleaseRepository,
        time_series_repository: TimeSeriesRepository,
        settings_repository: ForecastSettingsRepository | None = None,
        forecast_day_repository: ForecastDayRepository | None = None,
        default_min_sample_days: int = MIN_SAMPLE_DAYS,
        clock: Callable[[], datetime] = _utc_now,
        today: Callable[[], date] | None = None,
        zone: tzinfo = UTC,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._graph = graph_repository
        self._delivery = delivery_service
        self._commitments = commitment_repository
        self._releases = release_repository
        self._facts = time_series_repository
        self._settings = settings_repository
        self._forecast_days = forecast_day_repository
        #: The tenant's zone: a day's committed date and ETAs are those set before it ended there.
        self._zone = zone
        self._default_min_sample_days = validated_min_sample_days(default_min_sample_days)
        self._clock = clock
        self._today = today or (lambda: clock().date())
        self._new_id = new_id

    # ---- settings --------------------------------------------------------

    async def forecast_settings(self, tenant_id: str) -> ForecastSettingsView:
        """The working days of history the tenant's forecasts need, and the window they read."""
        stored = await self._settings.get(tenant_id) if self._settings is not None else None
        days = stored.min_sample_days if stored is not None else None
        # A value saved under wider bounds is held to today's.
        in_force = (
            self._default_min_sample_days
            if days is None
            else min(max(days, LOWEST_MIN_SAMPLE_DAYS), HIGHEST_MIN_SAMPLE_DAYS)
        )
        return ForecastSettingsView(
            min_sample_days=in_force,
            default_min_sample_days=self._default_min_sample_days,
            is_default=days is None,
            window_days=history_window_days(in_force),
            updated_at=stored.updated_at if stored is not None else None,
            updated_by=stored.updated_by if stored is not None else None,
        )

    async def save_forecast_settings(
        self, tenant_id: str, min_sample_days: int | None, *, actor: str
    ) -> ForecastSettingsView:
        """Set the working days of history a forecast needs; None goes back to the default."""
        if self._settings is None:
            raise ForecastSettingsError("Forecast settings cannot be saved on this server.")
        await self._settings.save(
            ForecastSettings(
                tenant_id=tenant_id,
                min_sample_days=(
                    validated_min_sample_days(min_sample_days)
                    if min_sample_days is not None
                    else None
                ),
                updated_at=self._clock(),
                updated_by=actor,
            )
        )
        return await self.forecast_settings(tenant_id)

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
        rule = await self.forecast_settings(tenant_id)
        context = await self._context(tenant_id, project_id, as_of, rule)
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

    async def forecast_history(
        self,
        tenant_id: str,
        project_id: str,
        as_of: date,
        *,
        release_id: str | None = None,
        days: int = HISTORY_DAYS,
    ) -> ForecastHistoryView:
        """The project's (or one release's) history forecast as it stood on each of the
        last ``days`` days that kept a snapshot, up to ``as_of``.

        A day before today reads its kept row (forecast_days) when there is one:
        the forecast as it stood that day, with that day's minimum. Today, and a
        day nobody kept, is forecast the way :meth:`project_delivery` forecasts it
        on that day, from the same snapshots, keys, seed and minimum, so the last
        day is the forecast the delivery view shows.
        """
        await self._project(tenant_id, project_id, as_of)
        release = await self.release(tenant_id, release_id) if release_id else None
        if release is not None and release.project_id != project_id:
            raise GraphNotFound(f"No release {release_id!r} in project {project_id!r}.")
        days = max(1, min(days, MAX_FORECAST_HISTORY_DAYS))
        rule = await self.forecast_settings(tenant_id)
        first = as_of - timedelta(days=days - 1)
        before = as_of - timedelta(days=1)
        stored = await self._delivery.history(
            tenant_id, project_id, first - timedelta(days=rule.window_days), before
        )
        own_days: dict[date, RequirementsSnapshot | None] = {
            item.day: item
            for item in (
                await self._delivery.history(tenant_id, project_id, first, before, release)
                if release is not None
                else stored
            )
        }
        own_days[as_of] = await self._delivery.snapshot(tenant_id, project_id, as_of, release)
        scope = (
            CommitmentScope(
                kind=CommitmentScopeKind.RELEASE, id=release.release_id, project_id=project_id
            )
            if release is not None
            else CommitmentScope(
                kind=CommitmentScopeKind.PROJECT, id=project_id, project_id=project_id
            )
        )
        kept = (
            {
                row.day: row
                for row in await self._forecast_days.list_between(
                    tenant_id, scope, first, min(as_of, self._today() - timedelta(days=1))
                )
            }
            if self._forecast_days is not None
            else {}
        )
        forecasts: list[ForecastDay] = []
        for offset in range(days):
            day = first + timedelta(days=offset)
            row = kept.get(day)
            if row is not None:
                forecasts.append(
                    ForecastDay(
                        day=day,
                        p50=row.p50,
                        p85=row.p85,
                        sample_days=row.sample_days,
                        reconstructed=row.reconstructed,
                    )
                )
                continue
            own = own_days.get(day)
            if own is None:
                continue
            window = [
                item for item in stored if day - timedelta(days=rule.window_days) <= item.day < day
            ]
            keys = frozenset(own.items) if release is not None else None
            if keys is not None:
                window = [_restricted(item, keys) for item in window]
            forecast = _scope_forecast(
                scope, own, window, keys, day, min_sample_days=rule.min_sample_days
            )
            forecasts.append(
                ForecastDay(
                    day=day, p50=forecast.p50, p85=forecast.p85, sample_days=forecast.sample_days
                )
            )
        return ForecastHistoryView(
            scope=scope, days=tuple(forecasts), needed_days=rule.min_sample_days
        )

    # ---- the forecast kept per day -------------------------------------------

    async def record_forecast_days(self, tenant_id: str, day: date) -> int:
        """Keep the day's forecast of every project, pod and release, as the delivery view
        reads it, each with why it moved since the scope's day before (its attribution).

        The hourly snapshot run calls it after storing the day's snapshots; each
        later run that day replaces the rows, so the day's last run stays.

        A pod's forecast is its part of one project's work (its keys, its date, its
        seed), so a pod that works on more than one project keeps one row under each,
        each from that project's own delivery view with its own breakdown: the store's
        key has the project (migration 0044).
        """
        if self._forecast_days is None:
            return 0
        projects = await self._graph.list_nodes(tenant_id, NodeKind.PROJECT, as_of=day)
        records: list[ForecastDayRecord] = []
        for project in projects:
            records += await self._day_records(tenant_id, project.id, day)
        await self._forecast_days.save(records)
        return len(records)

    async def forecast_day(
        self, tenant_id: str, scope: CommitmentScope, day: date
    ) -> ForecastDayRecord | None:
        """One scope's forecast of ``day`` as the hourly run would keep it, with its
        attribution, computed now: what a read of today uses, since today is always live."""
        records = await self._day_records(tenant_id, scope.project_id, day, only=scope)
        return next((record for record in records if record.scope == scope), None)

    async def live_days(
        self, tenant_id: str, project_id: str, day: date
    ) -> dict[CommitmentScope, ForecastDayRecord]:
        """Every scope of the project forecast for ``day``, computed now as forecast_day
        computes it (the same delivery read, scope and seed) but without the breakdown:
        what the timeline draws for today, so its band and the impact read's agree."""
        view = await self.project_delivery(tenant_id, project_id, day)
        computed_at = self._clock()
        return {
            item.scope: _record(tenant_id, item, day, computed_at)
            for item in (view.project, *view.pods, *view.releases)
        }

    async def kept_days(
        self, tenant_id: str, scope: CommitmentScope, start: date, end: date
    ) -> list[ForecastDayRecord]:
        """The scope's kept forecast_days rows from ``start`` to ``end``, oldest first."""
        if self._forecast_days is None:
            return []
        return await self._forecast_days.list_between(tenant_id, scope, start, end)

    async def tasks_by_key(
        self, tenant_id: str, project_id: str, day: date
    ) -> dict[str, GraphNode]:
        """The project's requirement issues on ``day``, by issue key (a node's id may differ)."""
        return {
            _task_key(task): task
            for task in await self._delivery.scope_tasks(tenant_id, project_id, day)
        }

    async def scope_requirements(
        self, tenant_id: str, scope: CommitmentScope, as_of: date
    ) -> ScopeRequirements:
        """The scope's forecast on ``as_of`` and its open requirements, each with the
        ETA stated by the end of that day and its due date that day."""
        view = await self.scope_delivery(tenant_id, scope, as_of)
        snapshot = await self.scope_snapshot(tenant_id, scope, as_of)
        return ScopeRequirements(
            view=view, open=await self.open_requirements(tenant_id, scope, as_of, snapshot)
        )

    async def open_requirements(
        self,
        tenant_id: str,
        scope: CommitmentScope,
        as_of: date,
        snapshot: RequirementsSnapshot | None,
    ) -> tuple[OpenRequirement, ...]:
        """The open requirements of the scope's ``snapshot`` of ``as_of``, each with the ETA
        stated by the end of that day and its due date that day. Stored reads only: no
        forecast is run, so the timeline reads it on every request."""
        if snapshot is None:
            return ()
        tasks = {
            _task_key(task): task
            for task in await self._delivery.scope_tasks(tenant_id, scope.project_id, as_of)
        }
        open_keys = _open_keys(snapshot)
        etas = _latest_etas(
            await self._eta_facts(tenant_id), set(open_keys), before=self._end_of(as_of)
        )
        listed: list[OpenRequirement] = []
        for key in open_keys:
            detail = snapshot.details.get(key)
            due = _iso_date(detail.due) if detail is not None else None
            listed.append(
                OpenRequirement(
                    key=key,
                    title=snapshot.titles.get(key) or _title(tasks.get(key)) or key,
                    stage=snapshot.items[key],
                    eta=etas.get(key),
                    due=due or _due(tasks.get(key)),
                )
            )
        return tuple(listed)

    async def project_pods(self, tenant_id: str, project_id: str, as_of: date) -> list[GraphNode]:
        """The pods working on the project on ``as_of``, by name."""
        return await self._project_pods(tenant_id, project_id, as_of)

    async def pod_keys(
        self, tenant_id: str, project_id: str, tasks: Mapping[str, GraphNode], as_of: date
    ) -> dict[str, frozenset[str]]:
        """Each of the project's pods' keys among ``tasks`` (by key): the ones below the pod."""
        return await self._pod_keys(tenant_id, project_id, tasks, as_of)

    async def uncounted_bugs(
        self, tenant_id: str, scope: CommitmentScope, as_of: date
    ) -> dict[str, GraphNode]:
        """The scope's bugs that are not counted as requirements on ``as_of``, by issue key:
        a pod's are the ones below it, a release's the ones it includes."""
        release = (
            await self.release(tenant_id, scope.id)
            if scope.kind is CommitmentScopeKind.RELEASE
            else None
        )
        bugs = {
            _task_key(task): task
            for task in await self._delivery.uncounted_bugs(
                tenant_id, scope.project_id, as_of, release
            )
        }
        if scope.kind is not CommitmentScopeKind.POD or not bugs:
            return bugs
        keys = (await self._pod_keys(tenant_id, scope.project_id, bugs, as_of)).get(
            scope.id, frozenset()
        )
        return {key: task for key, task in bugs.items() if key in keys}

    async def jira_release_date(self, tenant_id: str, release: Release, as_of: date) -> date | None:
        """The release's own date in Jira (a fix version's), from its issues on ``as_of``."""
        tasks = await self._delivery.scope_tasks(tenant_id, release.project_id, as_of)
        return _release_date(release, tasks)

    async def scope_snapshot(
        self, tenant_id: str, scope: CommitmentScope, day: date
    ) -> RequirementsSnapshot | None:
        """The scope's own snapshot of ``day``: a pod's is the project's, kept to its keys."""
        if scope.kind is CommitmentScopeKind.RELEASE:
            release = await self.release(tenant_id, scope.id)
            return await self._delivery.snapshot(tenant_id, scope.project_id, day, release)
        snapshot = await self._delivery.snapshot(tenant_id, scope.project_id, day)
        if scope.kind is CommitmentScopeKind.POD and snapshot is not None:
            tasks = {
                _task_key(task): task
                for task in await self._delivery.scope_tasks(tenant_id, scope.project_id, day)
            }
            keys = await self._pod_keys(tenant_id, scope.project_id, tasks, day)
            return _restricted(snapshot, keys.get(scope.id, frozenset()))
        return snapshot

    async def _day_records(
        self,
        tenant_id: str,
        project_id: str,
        day: date,
        *,
        only: CommitmentScope | None = None,
    ) -> list[ForecastDayRecord]:
        """The rows the hourly run keeps for the project's scopes on ``day`` (or ``only``),
        each pod's row and its breakdown from this one computation of the project."""
        view = await self.project_delivery(tenant_id, project_id, day)
        computed_at = self._clock()
        rule = await self.forecast_settings(tenant_id)
        days = await self._attribution_days(tenant_id, project_id, day, rule)
        records: list[ForecastDayRecord] = []
        for item in (view.project, *view.pods, *view.releases):
            if only is not None and item.scope != only:
                continue
            record = _record(tenant_id, item, day, computed_at)
            records.append(
                replace(record, attribution=await self._attribution(tenant_id, record, days))
            )
        return records

    async def _attribution_days(
        self, tenant_id: str, project_id: str, day: date, rule: ForecastSettingsView
    ) -> _AttributionDays:
        """What the day's breakdowns read: the project's day before, both windows, the keys."""
        tasks = {
            _task_key(task): task
            for task in await self._delivery.scope_tasks(tenant_id, project_id, day)
        }
        previous = await self._delivery.previous_snapshot(tenant_id, project_id, day)
        if previous is not None and previous.day < day - timedelta(days=PREVIOUS_SNAPSHOT_DAYS):
            previous = None
        first = (previous.day if previous is not None else day) - timedelta(days=rule.window_days)
        stored = await self._delivery.history(tenant_id, project_id, first, day - timedelta(days=1))
        return _AttributionDays(
            day=day,
            snapshot=await self._delivery.snapshot(tenant_id, project_id, day),
            previous=previous,
            stored=stored,
            tasks=tasks,
            pod_keys=await self._pod_keys(tenant_id, project_id, tasks, day),
            previous_pod_keys=(
                await self._pod_keys(tenant_id, project_id, tasks, previous.day)
                if previous is not None
                else {}
            ),
            window_days=rule.window_days,
            min_sample_days=rule.min_sample_days,
        )

    async def _attribution(
        self, tenant_id: str, record: ForecastDayRecord, days: _AttributionDays
    ) -> Mapping[str, object] | None:
        """Why the scope's forecast on the record's day moved since its day before."""
        scope = record.scope
        own_after: RequirementsSnapshot | None
        own_before: RequirementsSnapshot | None
        keys_after: frozenset[str] | None = None
        keys_before: frozenset[str] | None = None
        if scope.kind is CommitmentScopeKind.RELEASE:
            release = await self.release(tenant_id, scope.id)
            own_after = await self._delivery.snapshot(
                tenant_id, scope.project_id, days.day, release
            )
            own_before = await self._delivery.previous_snapshot(
                tenant_id, scope.project_id, days.day, release
            )
            if own_before is not None and (
                days.previous is None or own_before.day != days.previous.day
            ):
                own_before = None
            keys_after = frozenset(own_after.items) if own_after is not None else frozenset()
            keys_before = frozenset(own_before.items) if own_before is not None else frozenset()
        elif scope.kind is CommitmentScopeKind.POD:
            keys_after = days.pod_keys.get(scope.id, frozenset())
            keys_before = days.previous_pod_keys.get(scope.id, frozenset())
            own_after = _restricted(days.snapshot, keys_after) if days.snapshot else None
            own_before = _restricted(days.previous, keys_before) if days.previous else None
        else:
            own_after, own_before = days.snapshot, days.previous
        if own_after is None or own_before is None:
            return None
        kept = await self.kept_days(tenant_id, scope, own_before.day, own_before.day)
        before = _scope_day(
            scope,
            own_before,
            days.window(own_before.day, keys_before),
            keys_before,
            own_before.day,
            min_sample_days=days.min_sample_days,
            tasks=days.tasks,
            kept=kept[0] if kept else None,
        )
        after = _scope_day(
            scope,
            own_after,
            days.window(days.day, keys_after),
            keys_after,
            days.day,
            min_sample_days=days.min_sample_days,
            tasks=days.tasks,
            kept=record,
        )
        return attribution_payload(decompose(before, after))

    async def reconstruct_forecast_days(
        self, tenant_id: str, as_of: date, *, days: int = RECONSTRUCT_DAYS
    ) -> ReconstructionSummary:
        """Rebuild each project's, pod's and release's forecast for the ``days`` before ``as_of``.

        Each day with a stored snapshot is forecast as the history read replayed
        it (the same snapshots, keys, seed and today's minimum), against the
        committed date as it stood at the end of that day and the ETAs stated by
        then. Due dates are today's: their history is not kept. A pod's keys are
        its tasks as the graph held them that day, and a pod is rebuilt under each
        project it worked on, its part of that project, as the hourly run keeps it.
        Each row keeps why it moved since the scope's day before. The rows are marked
        reconstructed, and a day the hourly run kept is left as it is, except
        that it gains its breakdown when it kept none.
        """
        days = max(1, min(days, MAX_RECONSTRUCT_DAYS))
        first = as_of - timedelta(days=days)
        last = as_of - timedelta(days=1)
        total = ReconstructionSummary(first_day=first, last_day=last, scopes=0, written=0, kept=0)
        if self._forecast_days is None:
            return total
        rule = await self.forecast_settings(tenant_id)
        eta_facts = await self._eta_facts(tenant_id)
        projects = await self._graph.list_nodes(tenant_id, NodeKind.PROJECT, as_of=as_of)
        for project in projects:
            done = await self._reconstruct_project(
                tenant_id, project.id, first, last, rule, eta_facts
            )
            total = ReconstructionSummary(
                first_day=first,
                last_day=last,
                scopes=total.scopes + done.scopes,
                written=total.written + done.written,
                kept=total.kept + done.kept,
            )
        return total

    async def _reconstruct_project(
        self,
        tenant_id: str,
        project_id: str,
        first: date,
        last: date,
        rule: ForecastSettingsView,
        eta_facts: Sequence[FactEvent],
    ) -> ReconstructionSummary:
        repository = self._forecast_days
        if repository is None:
            return ReconstructionSummary(
                first_day=first, last_day=last, scopes=0, written=0, kept=0
            )
        # The days just before the first are read only as the first day's day before.
        lead = first - timedelta(days=PREVIOUS_SNAPSHOT_DAYS)
        stored = await self._delivery.history(
            tenant_id, project_id, lead - timedelta(days=rule.window_days), last
        )
        tasks = {
            _task_key(task): task
            for task in await self._delivery.scope_tasks(tenant_id, project_id, self._today())
        }
        scopes = await self._reconstruct_scopes(tenant_id, project_id, lead, last, stored, tasks)
        context = _ReconstructContext(
            first=first,
            stored=stored,
            tasks=tasks,
            changes=await self._commitments.changes_for_project(tenant_id, project_id),
            eta_facts=eta_facts,
            rule=rule,
            computed_at=self._clock(),
        )
        written = kept = 0
        for item in scopes:
            rows = {
                row.day: row
                for row in await repository.list_between(tenant_id, item.scope, lead, last)
            }
            records, fills, kept_here = self._reconstructed_rows(tenant_id, item, rows, context)
            await repository.save(records)
            if fills:
                await repository.fill_attribution(fills)
            written += len(records)
            kept += kept_here
        return ReconstructionSummary(
            first_day=first, last_day=last, scopes=len(scopes), written=written, kept=kept
        )

    async def _reconstruct_scopes(
        self,
        tenant_id: str,
        project_id: str,
        lead: date,
        last: date,
        stored: Sequence[RequirementsSnapshot],
        tasks: Mapping[str, GraphNode],
    ) -> list[_ReconstructScope]:
        """The project, its releases and its pods, each with its own snapshots of the days.

        A pod is the project's pod on the days it worked on the project, whatever other
        project it also worked on: its rows here are its part of this project only.
        """
        project_days = [item for item in stored if item.day >= lead]
        scopes: list[_ReconstructScope] = [
            _ReconstructScope(
                scope=CommitmentScope(
                    kind=CommitmentScopeKind.PROJECT, id=project_id, project_id=project_id
                ),
                own=project_days,
                jira_date=None,
                restricted=False,
            )
        ]
        for release in await self._releases.list_for_project(tenant_id, project_id):
            scopes.append(
                _ReconstructScope(
                    scope=CommitmentScope(
                        kind=CommitmentScopeKind.RELEASE,
                        id=release.release_id,
                        project_id=project_id,
                    ),
                    own=await self._delivery.history(tenant_id, project_id, lead, last, release),
                    jira_date=_release_date(release, tasks.values()),
                    restricted=True,
                )
            )
        membership = _PodMembership(
            project_id=project_id,
            edges=await self._graph.list_edges(tenant_id, kind=EdgeKind.CONTAINS),
            pods=frozenset(
                node.id for node in await self._graph.list_nodes(tenant_id, NodeKind.POD)
            ),
            tasks=tasks,
        )
        by_day = {item.day: membership.keys_on(item.day) for item in project_days}
        for pod_id in sorted({pod for keys in by_day.values() for pod in keys}):
            pod_days = [item for item in project_days if pod_id in by_day[item.day]]
            if not pod_days:
                continue
            scopes.append(
                _ReconstructScope(
                    scope=CommitmentScope(
                        kind=CommitmentScopeKind.POD, id=pod_id, project_id=project_id
                    ),
                    own=[_restricted(item, by_day[item.day][pod_id]) for item in pod_days],
                    jira_date=None,
                    restricted=True,
                    keys_by_day={item.day: by_day[item.day][pod_id] for item in pod_days},
                )
            )
        return scopes

    def _reconstructed_rows(
        self,
        tenant_id: str,
        item: _ReconstructScope,
        rows: Mapping[date, ForecastDayRecord],
        context: _ReconstructContext,
    ) -> tuple[list[ForecastDayRecord], list[ForecastDayRecord], int]:
        """One scope's rebuilt rows, the kept rows that gain a breakdown, and how many were kept."""
        rule = context.rule
        scope_changes = [change for change in context.changes if change.scope == item.scope]
        records: list[ForecastDayRecord] = []
        fills: list[ForecastDayRecord] = []
        kept = 0
        previous: ScopeDay | None = None
        for own in sorted(item.own, key=lambda snapshot: snapshot.day):
            keys = item.keys_on(own)
            window = [
                _restricted(snapshot, keys) if keys is not None else snapshot
                for snapshot in context.stored
                if own.day - timedelta(days=rule.window_days) <= snapshot.day < own.day
            ]
            row = rows.get(own.day)
            if own.day < context.first or (row is not None and not row.reconstructed):
                state = _scope_day(
                    item.scope,
                    own,
                    window,
                    keys,
                    own.day,
                    min_sample_days=rule.min_sample_days,
                    tasks=context.tasks,
                    kept=row,
                )
                if own.day >= context.first and row is not None:
                    kept += 1
                    if row.attribution is None and previous is not None:
                        breakdown = attribution_payload(decompose(previous, state))
                        fills.append(replace(row, attribution=breakdown))
                previous = state
                continue
            cutoff = self._end_of(own.day)
            forecast = _scope_forecast(
                item.scope, own, window, keys, own.day, min_sample_days=rule.min_sample_days
            )
            commitment = commitment_from_changes(item.scope, changes_until(scope_changes, cutoff))
            open_keys = _open_keys(own)
            etas = _latest_etas(context.eta_facts, set(open_keys), before=cutoff)
            team = team_forecast(
                OpenItem(key=key, eta=etas.get(key), due=_due(context.tasks.get(key)))
                for key in open_keys
            )
            target = commitment.target_date or item.jira_date
            record = ForecastDayRecord(
                tenant_id=tenant_id,
                scope=item.scope,
                day=own.day,
                p50=forecast.p50,
                p85=forecast.p85,
                verdict=verdict(target, forecast, team),
                target_date=target,
                team_latest=team.latest,
                team_latest_key=team.latest_key,
                remaining=forecast.remaining,
                unit=forecast.unit,
                sample_days=forecast.sample_days,
                completed_in_sample=forecast.completed_in_sample,
                min_sample_days=forecast.needed_days,
                computed_at=context.computed_at,
                reconstructed=True,
            )
            state = _scope_day(
                item.scope,
                own,
                window,
                keys,
                own.day,
                min_sample_days=rule.min_sample_days,
                tasks=context.tasks,
                kept=record,
            )
            attribution = (
                attribution_payload(decompose(previous, state)) if previous is not None else None
            )
            records.append(replace(record, attribution=attribution))
            previous = state
        return records, fills, kept

    def _end_of(self, day: date) -> datetime:
        """The first moment after ``day`` in the tenant's zone."""
        return datetime.combine(day + timedelta(days=1), time(), tzinfo=self._zone)

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
        open_keys = _open_keys(snapshot)
        forecast = _scope_forecast(
            scope, snapshot, history, keys, as_of, min_sample_days=context.min_sample_days
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

    async def _context(
        self, tenant_id: str, project_id: str, as_of: date, rule: ForecastSettingsView
    ) -> _Context:
        snapshot = await self._delivery.snapshot(tenant_id, project_id, as_of)
        history = await self._delivery.history(
            tenant_id,
            project_id,
            as_of - timedelta(days=rule.window_days),
            as_of - timedelta(days=1),
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
            min_sample_days=rule.min_sample_days,
        )

    async def _etas(self, tenant_id: str, keys: set[str]) -> dict[str, date]:
        """The last day of each issue's latest stated ETA, from a check-in or the console."""
        return _latest_etas(await self._eta_facts(tenant_id), keys)

    async def _eta_facts(self, tenant_id: str) -> list[FactEvent]:
        return await self._facts.list_recent_facts(
            tenant_id, sources=(CHECKIN_DRIFT_FACT_SOURCE,), limit=_ETA_FACT_LIMIT
        )

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
    min_sample_days: int


@dataclass(frozen=True, kw_only=True)
class _ReconstructScope:
    scope: CommitmentScope
    #: The scope's own stored snapshots in the days rebuilt.
    own: list[RequirementsSnapshot]
    jira_date: date | None
    #: A release or pod: its window is the project's, kept to the scope's keys of the day.
    restricted: bool
    #: A pod's keys per day (its tasks that day); a release's are its snapshot's own.
    keys_by_day: Mapping[date, frozenset[str]] | None = None

    def keys_on(self, own: RequirementsSnapshot) -> frozenset[str] | None:
        if not self.restricted:
            return None
        if self.keys_by_day is not None:
            return self.keys_by_day.get(own.day, frozenset(own.items))
        return frozenset(own.items)


def _latest_etas(
    facts: Iterable[FactEvent], keys: set[str], *, before: datetime | None = None
) -> dict[str, date]:
    """The last day of each issue's latest stated ETA among ``facts``, stated before ``before``.

    An ETA cleared in the console is recorded with no day: when it is the
    latest, the issue has no ETA, so an earlier one no longer counts.
    """
    latest: dict[str, tuple[datetime, date | None]] = {}
    for fact in sorted(facts, key=lambda item: item.observed_at):
        if before is not None and fact.observed_at >= before:
            continue
        payload = fact.payload
        key = payload.get("issue_key")
        if payload.get("kind") != ETA_STATED or not isinstance(key, str) or key not in keys:
            continue
        if "eta_date" in payload and payload["eta_date"] is None:
            latest[key] = (fact.observed_at, None)
            continue
        day = _iso_date(payload.get("eta_date"))
        if day is not None:
            latest[key] = (fact.observed_at, day)
    return {key: day for key, (_seen, day) in latest.items() if day is not None}


def _record(
    tenant_id: str, view: ScopeDeliveryView, day: date, computed_at: datetime
) -> ForecastDayRecord:
    """The row the hourly run keeps for one scope's delivery view of ``day``."""
    return ForecastDayRecord(
        tenant_id=tenant_id,
        scope=view.scope,
        day=day,
        p50=view.history.p50,
        p85=view.history.p85,
        verdict=view.verdict,
        target_date=view.target,
        team_latest=view.team.latest,
        team_latest_key=view.team.latest_key,
        remaining=view.history.remaining,
        unit=view.history.unit,
        sample_days=view.history.sample_days,
        completed_in_sample=view.history.completed_in_sample,
        min_sample_days=view.history.needed_days,
        computed_at=computed_at,
    )


def _open_keys(snapshot: RequirementsSnapshot | None) -> list[str]:
    if snapshot is None:
        return []
    return [key for key, stage in snapshot.items.items() if stage is not DeliveryStage.PRODUCTION]


def _scope_forecast(
    scope: CommitmentScope,
    snapshot: RequirementsSnapshot | None,
    history: Sequence[RequirementsSnapshot],
    keys: frozenset[str] | None,
    as_of: date,
    *,
    min_sample_days: int,
) -> HistoryForecast:
    """What history forecast for the scope on ``as_of``: the one rule the delivery view
    and the forecast's own history both use, seeded by the scope and the day, so a past
    day's forecast reads the same as the delivery view reads for that day."""
    inputs = _scope_inputs(snapshot, history, keys)
    return history_forecast(
        [value for _day, value in inputs.samples],
        inputs.remaining,
        start=as_of,
        seed=_seed(scope, as_of),
        unit=inputs.unit,
        min_sample_days=min_sample_days,
    )


@dataclass(frozen=True, kw_only=True)
class _ScopeInputs:
    """What a scope's forecast of a day replays: the window's samples and the work left."""

    samples: list[tuple[date, float]]
    remaining: float
    unit: str


def _scope_inputs(
    snapshot: RequirementsSnapshot | None,
    history: Sequence[RequirementsSnapshot],
    keys: frozenset[str] | None,
) -> _ScopeInputs:
    series = [*history, snapshot] if snapshot is not None else list(history)
    by_points = bool(series) and all(item.has_points for item in series)
    remaining: float
    if snapshot is None:
        remaining = 0
    elif by_points:
        remaining = snapshot.points_total - snapshot.points_done
    else:
        remaining = float(len(_open_keys(snapshot)))
    return _ScopeInputs(
        samples=dated_completions(series, keys=keys, by_points=by_points),
        remaining=remaining,
        unit="story points" if by_points else "requirements",
    )


def _seed(scope: CommitmentScope, day: date) -> str:
    return f"{scope.key}:{day.isoformat()}"


def _scope_day(
    scope: CommitmentScope,
    snapshot: RequirementsSnapshot,
    history: Sequence[RequirementsSnapshot],
    keys: frozenset[str] | None,
    day: date,
    *,
    min_sample_days: int,
    tasks: Mapping[str, GraphNode],
    kept: ForecastDayRecord | None,
) -> ScopeDay:
    """What the scope's forecast of ``day`` rested on, for the breakdown of a move.

    Its dates are the kept row's when there is one, so the breakdowns of days
    in a row add up to the kept forecasts' moves; else they are replayed. A
    requirement's points are its snapshot's, else the issue's now.
    """
    inputs = _scope_inputs(snapshot, history, keys)
    if kept is not None:
        p50, p85 = kept.p50, kept.p85
    else:
        forecast = _scope_forecast(
            scope, snapshot, history, keys, day, min_sample_days=min_sample_days
        )
        p50, p85 = forecast.p50, forecast.p85
    return ScopeDay(
        day=day,
        seed=_seed(scope, day),
        stages=dict(snapshot.items),
        titles={key: snapshot.titles.get(key) or _title(tasks.get(key)) for key in snapshot.items},
        types={key: _item_type(snapshot, key, tasks) for key in snapshot.items},
        points={key: _item_points(snapshot, key, tasks) for key in snapshot.items},
        samples=tuple(inputs.samples),
        remaining=inputs.remaining,
        unit=inputs.unit,
        min_sample_days=min_sample_days,
        p50=p50,
        p85=p85,
    )


def _item_points(
    snapshot: RequirementsSnapshot, key: str, tasks: Mapping[str, GraphNode]
) -> float | None:
    detail = snapshot.details.get(key)
    if detail is not None and detail.points is not None:
        return detail.points
    task = tasks.get(key)
    value = task.metadata.get("story_points") if task is not None else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _item_type(
    snapshot: RequirementsSnapshot, key: str, tasks: Mapping[str, GraphNode]
) -> str | None:
    detail = snapshot.details.get(key)
    if detail is not None and detail.type:
        return detail.type
    task = tasks.get(key)
    value = task.metadata.get("issue_type") if task is not None else None
    return value if isinstance(value, str) and value else None


def _title(task: GraphNode | None) -> str:
    return task.name if task is not None else ""


@dataclass(frozen=True, kw_only=True)
class _AttributionDays:
    """A project's day and its day before, as the day's breakdowns read them."""

    day: date
    snapshot: RequirementsSnapshot | None
    #: The project's last snapshot before the day, within PREVIOUS_SNAPSHOT_DAYS.
    previous: RequirementsSnapshot | None
    #: The project's stored snapshots, from the earlier day's window to the day before.
    stored: list[RequirementsSnapshot]
    tasks: Mapping[str, GraphNode]
    pod_keys: Mapping[str, frozenset[str]]
    previous_pod_keys: Mapping[str, frozenset[str]]
    window_days: int
    min_sample_days: int

    def window(self, day: date, keys: frozenset[str] | None) -> list[RequirementsSnapshot]:
        """The stored snapshots a forecast of ``day`` reads, kept to ``keys``."""
        found = [
            item for item in self.stored if day - timedelta(days=self.window_days) <= item.day < day
        ]
        return [_restricted(item, keys) for item in found] if keys is not None else found


@dataclass(frozen=True, kw_only=True)
class _ReconstructContext:
    """What every scope of a project's reconstruction reads."""

    first: date
    stored: Sequence[RequirementsSnapshot]
    tasks: Mapping[str, GraphNode]
    changes: Sequence[DateChange]
    eta_facts: Sequence[FactEvent]
    rule: ForecastSettingsView
    computed_at: datetime


@dataclass(frozen=True, kw_only=True)
class _PodMembership:
    """Which of the project's tasks sat under each of its pods on a day, from one edge read."""

    project_id: str
    edges: Sequence[GraphEdge]
    pods: frozenset[str]
    tasks: Mapping[str, GraphNode]

    def keys_on(self, day: date) -> dict[str, frozenset[str]]:
        """Each pod working on the project that day, and its requirement keys."""
        active = [edge for edge in self.edges if edge.is_active_on(day)]
        parents: dict[str, list[str]] = {}
        for edge in active:
            parents.setdefault(edge.to_node_id, []).append(edge.from_node_id)
        keys: dict[str, set[str]] = {
            edge.to_node_id: set()
            for edge in active
            if edge.from_node_id == self.project_id and edge.to_node_id in self.pods
        }
        for key, task in self.tasks.items():
            for ancestor in _ancestors(task.id, parents):
                if ancestor in keys:
                    keys[ancestor].add(key)
        return {pod_id: frozenset(found) for pod_id, found in keys.items()}


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
    line = f"Committed for {_day(target)} by {names.get(actor) or UNKNOWN_PERSON}"
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
            f"Team's own date: {_day(team.latest)} ({team.latest_key}){late}, the latest "
            "finish date the team gave (ETA or due date) across the open requirements."
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
        details={key: item for key, item in snapshot.details.items() if key in keys},
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
