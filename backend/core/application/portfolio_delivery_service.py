"""Will we make the date? for every project a person reads, in one read.

From what is kept, never a live forecast per project: each scope's latest
``forecast_days`` row (the hourly run's, so its dates are "as of" the time it
was computed), the date changes, each project's latest stored snapshot and its
planning baseline, the day's stored rollup and check-ins. For every project:

- the three dates: committed (else Jira's release date), likely (the 50% and 85%
  dates) and the team's own date, with how many working days late it is;
- the verdict, and one reason line that counts work items (core/domain/portfolio);
- today's health in words, never as a second colour;
- done of total, for the project and each pod;
- the marks on its bar: scope added, removed or reopened, with the working days
  each moved the 85% date on its day, and the committed date's moves.

Search, the verdict filters and the order (worst first) run over the kept rows
alone; the per-project reads (snapshot, baseline) run only for the page asked
for, so 50 or 500 projects cost one page of work. The project page stays live.

The small-group rule holds for an executive: a pod of fewer than five people
has no row of its own (its work is in the project's), and a project of fewer
than five people says no reply counts in its health.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from core.application.baseline_service import BaselineService
from core.application.people_words import hides_names, is_small
from core.application.portfolio_index import IndexedProject, PortfolioIndex, PortfolioIndexReader
from core.domain.auth import Principal
from core.domain.baseline import BaselineScopeKind, since_baseline
from core.domain.delivery import DeliveryStage, RequirementsSnapshot
from core.domain.forecast import (
    CommitmentScopeKind,
    DateChange,
    ForecastDayChange,
    ForecastDayRecord,
    Verdict,
    split_names,
)
from core.domain.portfolio import (
    FILTER_VERDICTS,
    AgainstPlan,
    HealthCounts,
    PortfolioSort,
    ReasonInputs,
    VerdictFilter,
    health_words,
    reason_line,
    sort_key,
    verdict_counts,
    working_days_late,
)
from core.domain.rollup import FactorKind, NodeStatus, Rag
from core.domain.status import CheckInDay, local_date
from core.ports.delivery import RequirementsSnapshotRepository
from core.ports.forecast import CommitmentRepository, ForecastDayRepository, ReleaseRepository
from core.ports.repositories import RollupRepository, StatusRepository

#: How far back a bar reads its marks when the project has no planning baseline.
BAR_FALLBACK_DAYS = 30
#: The most days a bar reads back.
BAR_MAX_DAYS = 90
#: A day with no stored snapshot (a weekend) reads the last one kept within this many days.
SNAPSHOT_LOOKBACK_DAYS = 7
#: The most projects one page returns.
MAX_PAGE = 200
#: A mark moved the 85% date later by at least this many working days: it pushed the date.
PUSHED_FROM_DAYS = 0.5


@dataclass(frozen=True, kw_only=True)
class PodLine:
    id: str
    name: str
    done: int
    total: int
    verdict: Verdict | None
    target: date | None
    p85: date | None


@dataclass(frozen=True, kw_only=True)
class ReleaseLine:
    id: str
    name: str
    verdict: Verdict | None
    target: date | None
    p85: date | None


@dataclass(frozen=True, kw_only=True)
class BarMark:
    day: date
    #: scope_added, scope_removed, reopened or committed_date_changed.
    kind: str
    count: int
    #: Working days the 85% date moved for it that day (later is positive); None for a
    #: committed date's move, or a day not taken apart.
    working_days: float | None
    #: It moved the 85% date later: the bar marks it as a change that pushed the date.
    pushed: bool
    #: For a committed date's move: the date it moved to (None: cleared).
    moved_to: date | None = None


@dataclass(frozen=True, kw_only=True)
class PortfolioProjectView:
    id: str
    name: str
    program_id: str | None
    program_name: str | None
    verdict: Verdict | None
    target: date | None
    target_source: str | None
    p50: date | None
    p85: date | None
    team_date: date | None
    team_key: str | None
    days_late: int | None
    reason: str
    #: Today's health in words, and the day the rollup it is read from was kept.
    health: str
    health_day: date | None
    done: int
    total: int
    pods: tuple[PodLine, ...]
    releases: tuple[ReleaseLine, ...]
    marks: tuple[BarMark, ...]
    plan: AgainstPlan | None
    #: Where the bar starts: planning, else a month back.
    start: date
    #: The kept row's day and when it was computed.
    kept_on: date | None
    computed_at: datetime | None
    reconstructed: bool


@dataclass(frozen=True, kw_only=True)
class ProgramCounts:
    id: str
    name: str
    counts: Mapping[str, int]


@dataclass(frozen=True, kw_only=True)
class PortfolioDeliveryView:
    as_of: date
    #: The newest computed_at among the rows read: the "as of HH:MM" the page says.
    computed_at: datetime | None
    #: Each verdict's count over every project the search matches, before the verdict
    #: filter, so a tile is a filter; "none" is a project with no kept row yet.
    counts: Mapping[str, int]
    #: The projects the search and filters match, of which ``projects`` is one page.
    total: int
    offset: int
    limit: int
    projects: tuple[PortfolioProjectView, ...]
    programs: tuple[ProgramCounts, ...]


@dataclass(frozen=True, kw_only=True)
class PortfolioQuery:
    search: str = ""
    verdicts: frozenset[VerdictFilter] = frozenset()
    program_id: str | None = None
    sort: PortfolioSort = PortfolioSort.WORST
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, kw_only=True)
class _Kept:
    """What the kept rows say of one project, before anything per project is read."""

    project: IndexedProject
    row: ForecastDayRecord | None
    target_source: str | None
    days_late: int | None


class PortfolioDeliveryService:
    def __init__(
        self,
        *,
        index: PortfolioIndexReader,
        forecast_days: ForecastDayRepository,
        commitments: CommitmentRepository,
        releases: ReleaseRepository,
        snapshots: RequirementsSnapshotRepository,
        baselines: BaselineService,
        rollups: RollupRepository,
        statuses: StatusRepository,
        zone: ZoneInfo | None = None,
    ) -> None:
        self._index = index
        self._forecast_days = forecast_days
        self._commitments = commitments
        self._releases = releases
        self._snapshots = snapshots
        self._baselines = baselines
        self._rollups = rollups
        self._statuses = statuses
        # A committed date's change is dated in the tenant's zone, as the timeline
        # dates it, so both mark it on the same day; the router passes
        # ServiceRegistry.tenant_zone.
        self._zone = zone or ZoneInfo("UTC")

    async def read(
        self, principal: Principal, as_of: date, query: PortfolioQuery
    ) -> PortfolioDeliveryView:
        tenant_id = principal.tenant_id
        index = await self._index.read(principal, as_of)
        rows = await self._forecast_days.latest_per_scope(tenant_id, as_of)
        by_scope = {(row.scope.kind, row.scope.id, row.scope.project_id): row for row in rows}
        changes = _changes_until(
            await self._commitments.changes_for_tenant(tenant_id), as_of, self._zone
        )
        searched = [project for project in index.projects if project.matches(query.search)]
        if query.program_id:
            searched = [
                project
                for project in searched
                if query.program_id in {program.id for program in project.programs}
            ]
        kept = [_kept(project, by_scope, changes) for project in searched]
        wanted = {FILTER_VERDICTS[item] for item in query.verdicts}
        shown = [item for item in kept if not wanted or (item.row and item.row.verdict in wanted)]
        shown.sort(
            key=lambda item: sort_key(
                sort=query.sort,
                verdict=item.row.verdict if item.row else None,
                total=1 if item.row is None or item.row.remaining > 0 else 0,
                days_late=item.days_late,
                target=item.row.target_date if item.row else None,
                name=item.project.name,
            )
        )
        limit = max(1, min(query.limit, MAX_PAGE))
        offset = max(0, query.offset)
        page = shown[offset : offset + limit]
        day = _Day(
            index=index,
            by_scope=by_scope,
            changes=changes,
            marks=await self._marks(tenant_id, page, as_of),
            statuses={
                status.entity_ref.id: status
                for status in await self._rollups.list_node_statuses(tenant_id, as_of)
            },
            checkins={
                checkin.developer_id: checkin
                for checkin in await self._statuses.checkins_on_day(tenant_id, as_of)
            },
            release_names={
                release.release_id: release.name
                for release in await self._releases.list_all(tenant_id)
            },
            small_groups=hides_names(principal),
        )
        projects = await asyncio.gather(
            *(self._project(tenant_id, item, day, as_of) for item in page)
        )
        return PortfolioDeliveryView(
            as_of=as_of,
            computed_at=max(
                (item.row.computed_at for item in kept if item.row is not None), default=None
            ),
            counts=verdict_counts(item.row.verdict if item.row else None for item in kept),
            total=len(shown),
            offset=offset,
            limit=limit,
            projects=tuple(projects),
            programs=_program_counts(kept),
        )

    async def _marks(
        self, tenant_id: str, page: Sequence[_Kept], as_of: date
    ) -> Mapping[str, list[ForecastDayChange]]:
        if not page:
            return {}
        wanted = {item.project.id for item in page}
        found: dict[str, list[ForecastDayChange]] = {}
        for change in await self._forecast_days.changes_between(
            tenant_id,
            CommitmentScopeKind.PROJECT,
            as_of - timedelta(days=BAR_MAX_DAYS),
            as_of,
        ):
            if change.scope.project_id in wanted:
                found.setdefault(change.scope.project_id, []).append(change)
        return found

    async def _project(
        self, tenant_id: str, item: _Kept, day: _Day, as_of: date
    ) -> PortfolioProjectView:
        project, row = item.project, item.row
        snapshot = await self._snapshots.latest_before(
            tenant_id, project.id, as_of + timedelta(days=1)
        )
        if snapshot is not None and snapshot.day < as_of - timedelta(days=SNAPSHOT_LOOKBACK_DAYS):
            snapshot = None
        plan = await self._plan(tenant_id, project, snapshot, day, as_of)
        start = (
            plan.planned_on
            if plan is not None and plan.planned_on >= as_of - timedelta(days=BAR_MAX_DAYS)
            else as_of - timedelta(days=BAR_FALLBACK_DAYS)
        )
        marks = _bar_marks(
            day.marks.get(project.id, []),
            day.changes.get(_project_key(project.id), []),
            start,
            as_of,
            self._zone,
        )
        if plan is not None:
            plan = _with_added_days(plan, day.marks.get(project.id, []))
        status = day.statuses.get(project.id)
        program = project.programs[0] if project.programs else None
        return PortfolioProjectView(
            id=project.id,
            name=project.name,
            program_id=program.id if program else None,
            program_name=program.name if program else None,
            verdict=row.verdict if row else None,
            target=row.target_date if row else None,
            target_source=item.target_source,
            p50=row.p50 if row else None,
            p85=row.p85 if row else None,
            team_date=row.team_latest if row else None,
            team_key=row.team_latest_key if row else None,
            days_late=item.days_late,
            reason=reason_line(
                ReasonInputs(
                    verdict=row.verdict if row else None,
                    target=row.target_date if row else None,
                    target_source=item.target_source,
                    p50=row.p50 if row else None,
                    p85=row.p85 if row else None,
                    team_date=row.team_latest if row else None,
                    team_key=row.team_latest_key if row else None,
                    sample_days=row.sample_days if row else 0,
                    min_sample_days=row.min_sample_days if row else 0,
                    plan=plan,
                    as_of=as_of,
                )
            ),
            health=health_words(_health(status, _counted_people(project, day), day.checkins)),
            health_day=status.as_of if status is not None else None,
            done=_done(snapshot, None),
            total=_total(snapshot, None),
            pods=_pods(project, snapshot, day),
            releases=_releases(project, day),
            marks=marks,
            plan=plan,
            start=start,
            kept_on=row.day if row else None,
            computed_at=row.computed_at if row else None,
            reconstructed=row.reconstructed if row else False,
        )

    async def _plan(
        self,
        tenant_id: str,
        project: IndexedProject,
        snapshot: RequirementsSnapshot | None,
        day: _Day,
        as_of: date,
    ) -> AgainstPlan | None:
        baseline = await self._baselines.baseline_on(
            tenant_id, BaselineScopeKind.PROJECT, project.id, as_of
        )
        if baseline is None:
            return None
        current = frozenset(snapshot.items) if snapshot is not None else frozenset()
        labels = {
            key: split_names(task.metadata.get("labels"))
            for key in current - baseline.keys
            if (task := day.index.tasks.get(key)) is not None
        }
        since = since_baseline(baseline, current, labels)
        return AgainstPlan(
            planned_on=baseline.planned_on,
            planned=len(baseline.items),
            added=len(since.added),
            without_label=len(since.added) - len(since.added_with_label),
            change_label=baseline.change_label,
            removed=len(since.removed),
        )


@dataclass(frozen=True, kw_only=True)
class _Day:
    index: PortfolioIndex
    by_scope: Mapping[tuple[CommitmentScopeKind, str, str], ForecastDayRecord]
    changes: Mapping[str, list[DateChange]]
    marks: Mapping[str, list[ForecastDayChange]]
    statuses: Mapping[str, NodeStatus]
    checkins: Mapping[str, CheckInDay]
    release_names: Mapping[str, str]
    #: The reader keeps to the small-group rule: an executive.
    small_groups: bool = False


def _kept(
    project: IndexedProject,
    by_scope: Mapping[tuple[CommitmentScopeKind, str, str], ForecastDayRecord],
    changes: Mapping[str, list[DateChange]],
) -> _Kept:
    row = by_scope.get((CommitmentScopeKind.PROJECT, project.id, project.id))
    committed = _committed(changes.get(_project_key(project.id), []))
    source = (
        "committed"
        if committed is not None
        else "jira_release"
        if row is not None and row.target_date is not None
        else None
    )
    return _Kept(
        project=project,
        row=row,
        target_source=source,
        days_late=working_days_late(row.target_date, row.p85) if row else None,
    )


def _committed(changes: Sequence[DateChange]) -> date | None:
    return changes[-1].target_date if changes else None


def _project_key(project_id: str) -> str:
    return f"{CommitmentScopeKind.PROJECT.value}:{project_id}:{project_id}"


def _changes_until(
    changes: Sequence[DateChange], as_of: date, zone: ZoneInfo
) -> dict[str, list[DateChange]]:
    """Each scope's date changes made by the end of ``as_of`` in ``zone``, oldest first."""
    found: dict[str, list[DateChange]] = {}
    for change in sorted(changes, key=lambda item: item.changed_at):
        if local_date(change.changed_at, zone) <= as_of:
            found.setdefault(change.scope.key, []).append(change)
    return found


def _bar_marks(
    days: Sequence[ForecastDayChange],
    commitments: Sequence[DateChange],
    start: date,
    as_of: date,
    zone: ZoneInfo,
) -> tuple[BarMark, ...]:
    marks: list[BarMark] = []
    for change in days:
        if not start <= change.day <= as_of:
            continue
        for kind, count, working_days in (
            ("scope_added", change.added, change.added_days),
            ("scope_removed", change.removed, change.removed_days),
            ("reopened", change.reopened, change.reopened_days),
        ):
            if not count:
                continue
            known = round(working_days, 1) if change.taken_apart else None
            marks.append(
                BarMark(
                    day=change.day,
                    kind=kind,
                    count=count,
                    working_days=known,
                    pushed=known is not None and known >= PUSHED_FROM_DAYS,
                )
            )
    previous: date | None = None
    for index, commitment in enumerate(commitments):
        moved = index > 0 and commitment.target_date != previous
        previous = commitment.target_date
        on = local_date(commitment.changed_at, zone)
        if moved and start <= on <= as_of:
            marks.append(
                BarMark(
                    day=on,
                    kind="committed_date_changed",
                    count=1,
                    working_days=None,
                    pushed=False,
                    moved_to=commitment.target_date,
                )
            )
    return tuple(sorted(marks, key=lambda mark: (mark.day, mark.kind)))


def _with_added_days(plan: AgainstPlan, days: Sequence[ForecastDayChange]) -> AgainstPlan:
    """The working days the additions since planning moved the 85% date, from the kept days."""
    counted = [
        change.added_days
        for change in days
        if change.day > plan.planned_on and change.added and change.taken_apart
    ]
    return AgainstPlan(
        planned_on=plan.planned_on,
        planned=plan.planned,
        added=plan.added,
        without_label=plan.without_label,
        change_label=plan.change_label,
        removed=plan.removed,
        added_days=sum(counted) if counted else None,
    )


def _health(
    status: NodeStatus | None, people: Sequence[str], checkins: Mapping[str, CheckInDay]
) -> HealthCounts:
    asked = [checkins[person] for person in people if person in checkins]
    unanswered = sum(1 for checkin in asked if not checkin.answered)
    if status is None:
        return HealthCounts(reported=False, asked=len(asked), unanswered=unanswered)
    factors = [factor for factor in status.factors if factor.contributes is not Rag.GREEN]
    return HealthCounts(
        reported=status.rag is not Rag.UNKNOWN,
        blockers=len(
            {
                factor.blocker_id or factor.description
                for factor in factors
                if factor.kind is FactorKind.BLOCKER
            }
        ),
        blocked_tasks=len(
            {
                factor.source_ref.id
                for factor in factors
                if factor.kind is FactorKind.TASK and factor.contributes is Rag.RED
            }
        ),
        disagree=len(
            {
                factor.work_item_ref.id if factor.work_item_ref else factor.description
                for factor in factors
                if factor.kind is FactorKind.DRIFT
            }
        ),
        asked=len(asked),
        unanswered=unanswered,
    )


def _counted_people(project: IndexedProject, day: _Day) -> Sequence[str]:
    """The people whose replies the health counts: none of a small project's for an
    executive, whose health then says its work items only."""
    people = day.index.people_of(project)
    return () if day.small_groups and is_small(len(people)) else people


def _pods(
    project: IndexedProject, snapshot: RequirementsSnapshot | None, day: _Day
) -> tuple[PodLine, ...]:
    keys = day.index.pod_keys(project, snapshot.items) if snapshot is not None else {}
    lines: list[PodLine] = []
    for pod in project.pods:
        if day.small_groups and is_small(day.index.people_in(pod.id)):
            # Read as part of the project: a row of its own would tell its people apart.
            continue
        row = day.by_scope.get((CommitmentScopeKind.POD, pod.id, project.id))
        own = keys.get(pod.id, frozenset())
        lines.append(
            PodLine(
                id=pod.id,
                name=pod.name,
                done=_done(snapshot, own),
                total=_total(snapshot, own),
                verdict=row.verdict if row else None,
                target=row.target_date if row else None,
                p85=row.p85 if row else None,
            )
        )
    return tuple(lines)


def _releases(project: IndexedProject, day: _Day) -> tuple[ReleaseLine, ...]:
    lines = [
        ReleaseLine(
            id=scope_id,
            name=day.release_names.get(scope_id, scope_id),
            verdict=row.verdict,
            target=row.target_date,
            p85=row.p85,
        )
        for (kind, scope_id, project_id), row in day.by_scope.items()
        if kind is CommitmentScopeKind.RELEASE and project_id == project.id
    ]
    return tuple(sorted(lines, key=lambda line: (line.target or date.max, line.name.casefold())))


def _done(snapshot: RequirementsSnapshot | None, keys: frozenset[str] | None) -> int:
    if snapshot is None:
        return 0
    return sum(
        1
        for key, stage in snapshot.items.items()
        if stage is DeliveryStage.PRODUCTION and (keys is None or key in keys)
    )


def _total(snapshot: RequirementsSnapshot | None, keys: frozenset[str] | None) -> int:
    if snapshot is None:
        return 0
    return sum(1 for key in snapshot.items if keys is None or key in keys)


def _program_counts(kept: Sequence[_Kept]) -> tuple[ProgramCounts, ...]:
    """Each programme's projects by verdict, for its group header; by name."""
    grouped: dict[str, tuple[str, list[Verdict | None]]] = {}
    for item in kept:
        program = item.project.programs[0] if item.project.programs else None
        if program is None:
            continue
        name, verdicts = grouped.setdefault(program.id, (program.name, []))
        verdicts.append(item.row.verdict if item.row else None)
    return tuple(
        ProgramCounts(id=program_id, name=name, counts=verdict_counts(verdicts))
        for program_id, (name, verdicts) in sorted(
            grouped.items(), key=lambda entry: (entry[1][0].casefold(), entry[0])
        )
    )


__all__ = [
    "BarMark",
    "PodLine",
    "PortfolioDeliveryService",
    "PortfolioDeliveryView",
    "PortfolioProjectView",
    "PortfolioQuery",
    "ProgramCounts",
    "ReleaseLine",
]
