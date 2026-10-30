"""Planning complete: the baseline a project's or a release's later scope is read against.

A product owner or a manager marks planning complete for the whole project or
for one release (the router checks set_project_dates, the right that commits
their dates). Marked today, it freezes the requirements in scope now, read
live as the delivery view reads them, each with its stage and the story
points, due date and type its issue carries. Marked for an earlier day, it
freezes that day's stored snapshot, with the points and type the issue's
synced history held by the end of that day. Before snapshots were kept, the
issues in scope today that the tracker says were created by then stand in, and
the baseline says it was reconstructed.

Marking it again adds a new baseline and keeps the earlier ones. The read says
how today's scope differs from each: the keys added since (and which carry the
baseline's change label) and the keys gone, never who added them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from uuid import uuid4

from core.application.delivery_service import DeliveryService
from core.application.person_names import UNKNOWN_PERSON, PersonNames
from core.application.sync_services import ISSUE_FACT_SOURCE
from core.domain.baseline import (
    DEFAULT_GRACE_WORKING_DAYS,
    SNAPSHOT_LOOKBACK_DAYS,
    BaselineError,
    BaselineItem,
    BaselineKind,
    BaselineScopeKind,
    PlanningBaseline,
    ScopeSinceBaseline,
    since_baseline,
    validated_baseline_note,
    validated_change_label,
    validated_grace,
    validated_planned_on,
)
from core.domain.delivery import DeliveryStage, RequirementsSnapshot, StageMapping, place
from core.domain.errors import GraphNotFound
from core.domain.forecast import Release, split_names
from core.domain.graph import EntityRef, FactEvent, GraphNode, NodeKind
from core.ports.baselines import PlanningBaselineRepository
from core.ports.forecast import ReleaseRepository
from core.ports.repositories import GraphRepository, TimeSeriesRepository


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, kw_only=True)
class BaselineView:
    baseline: PlanningBaseline
    #: 1 for the scope's first baseline, in the order they were set.
    number: int
    #: The scope's baseline on the day read: set by then and not yet replaced.
    active: bool
    set_by_name: str
    #: The scope on the day read against this baseline.
    since: ScopeSinceBaseline


@dataclass(frozen=True, kw_only=True)
class BaselinesView:
    scope_kind: BaselineScopeKind
    scope_id: str
    project_id: str
    #: The project's or the release's name.
    name: str
    as_of: date
    #: Newest first: the active one, then the ones it replaced.
    baselines: tuple[BaselineView, ...]
    #: Requirements in scope on the day read.
    in_scope: int

    @property
    def active(self) -> BaselineView | None:
        return next((view for view in self.baselines if view.active), None)

    @property
    def first(self) -> BaselineView | None:
        """The scope's first baseline, when a later one replaced it."""
        return self.baselines[-1] if len(self.baselines) > 1 else None


class BaselineService:
    def __init__(
        self,
        *,
        graph_repository: GraphRepository,
        delivery_service: DeliveryService,
        release_repository: ReleaseRepository,
        baseline_repository: PlanningBaselineRepository,
        time_series_repository: TimeSeriesRepository,
        clock: Callable[[], datetime] = _utc_now,
        today: Callable[[], date] | None = None,
        zone: tzinfo = UTC,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._graph = graph_repository
        self._delivery = delivery_service
        self._releases = release_repository
        self._baselines = baseline_repository
        self._facts = time_series_repository
        self._clock = clock
        self._today = today or (lambda: clock().date())
        self._zone = zone
        self._new_id = new_id

    async def mark_planning_complete(
        self,
        tenant_id: str,
        project_id: str,
        *,
        release_id: str | None = None,
        planned_on: date | None = None,
        change_label: str | None = None,
        grace_working_days: int = DEFAULT_GRACE_WORKING_DAYS,
        note: str = "",
        actor: str,
    ) -> BaselineView:
        """Freeze the scope's requirements as the new active baseline; the earlier ones stay."""
        await self._project(tenant_id, project_id)
        release = await self._release(tenant_id, project_id, release_id)
        today = self._today()
        day = validated_planned_on(planned_on, today)
        label = validated_change_label(change_label)
        grace = validated_grace(grace_working_days)
        clean_note = validated_baseline_note(note)
        if day >= today:
            items = await self._items_now(tenant_id, project_id, release, today)
            kind, reconstructed, snapshot_day = BaselineKind.SNAPSHOT, False, None
        else:
            items, reconstructed, snapshot_day = await self._items_on(
                tenant_id, project_id, release, day
            )
            kind = BaselineKind.DATE
        if not items:
            raise BaselineError(
                "No requirements were in scope then, so there is nothing to freeze."
                if day < today
                else "No requirements are in scope yet, so there is nothing to freeze."
            )
        scope_kind, scope_id = _scope(project_id, release)
        baseline = await self._baselines.add(
            PlanningBaseline(
                tenant_id=tenant_id,
                baseline_id=self._new_id(),
                scope_kind=scope_kind,
                scope_id=scope_id,
                project_id=project_id,
                kind=kind,
                planned_on=day,
                items=items,
                reconstructed=reconstructed,
                snapshot_day=snapshot_day,
                change_label=label,
                grace_working_days=grace,
                set_by=actor,
                set_at=self._clock(),
                note=clean_note,
            )
        )
        view = await self.baselines(tenant_id, project_id, release_id=release_id, as_of=today)
        return next(
            item for item in view.baselines if item.baseline.baseline_id == baseline.baseline_id
        )

    async def baselines(
        self,
        tenant_id: str,
        project_id: str,
        *,
        release_id: str | None = None,
        as_of: date,
    ) -> BaselinesView:
        """The scope's baselines as they stood on ``as_of``, each against that day's scope."""
        project = await self._project(tenant_id, project_id, as_of)
        release = await self._release(tenant_id, project_id, release_id)
        scope_kind, scope_id = _scope(project_id, release)
        cutoff = self._end_of(as_of)
        listed = [
            baseline
            for baseline in await self._baselines.list_for_scope(tenant_id, scope_kind, scope_id)
            if baseline.set_at < cutoff
        ]
        snapshot = await self._delivery.snapshot(tenant_id, project_id, as_of, release)
        current = frozenset(snapshot.items) if snapshot is not None else frozenset()
        labels = _labels_by_key(
            await self._delivery.scope_tasks(tenant_id, project_id, as_of, release)
        )
        names = await PersonNames(self._graph).resolve(
            tenant_id, {baseline.set_by for baseline in listed}
        )
        views = [
            BaselineView(
                baseline=baseline,
                number=number,
                active=baseline.superseded_at is None or baseline.superseded_at >= cutoff,
                set_by_name=names.get(baseline.set_by) or UNKNOWN_PERSON,
                since=since_baseline(baseline, current, labels),
            )
            for number, baseline in enumerate(listed, start=1)
        ]
        return BaselinesView(
            scope_kind=scope_kind,
            scope_id=scope_id,
            project_id=project_id,
            name=release.name if release is not None else project.name,
            as_of=as_of,
            baselines=tuple(reversed(views)),
            in_scope=len(current),
        )

    async def active_baseline(
        self, tenant_id: str, scope_kind: BaselineScopeKind, scope_id: str
    ) -> PlanningBaseline | None:
        """The scope's baseline in force now, for the reads that measure scope against it."""
        listed = await self._baselines.list_for_scope(tenant_id, scope_kind, scope_id)
        return next((baseline for baseline in reversed(listed) if baseline.active), None)

    async def baseline_on(
        self, tenant_id: str, scope_kind: BaselineScopeKind, scope_id: str, day: date
    ) -> PlanningBaseline | None:
        """The scope's baseline in force at the end of ``day``: set by then, not yet replaced."""
        cutoff = self._end_of(day)
        listed = await self._baselines.list_for_scope(tenant_id, scope_kind, scope_id)
        return next(
            (
                baseline
                for baseline in reversed(listed)
                if baseline.set_at < cutoff
                and (baseline.superseded_at is None or baseline.superseded_at >= cutoff)
            ),
            None,
        )

    # ---- what is frozen ----------------------------------------------------

    async def _items_now(
        self, tenant_id: str, project_id: str, release: Release | None, today: date
    ) -> dict[str, BaselineItem]:
        """The requirements in scope now, as the delivery view places them."""
        snapshot = await self._delivery.snapshot(tenant_id, project_id, today, release)
        if snapshot is None:
            return {}
        tasks = {
            _task_key(task): task
            for task in await self._delivery.scope_tasks(tenant_id, project_id, today, release)
        }
        items: dict[str, BaselineItem] = {}
        for key, stage in snapshot.items.items():
            task = tasks.get(key)
            metadata = task.metadata if task is not None else {}
            items[key] = BaselineItem(
                stage=stage,
                points=_number(metadata.get("story_points")),
                due=_iso_date(metadata.get("due_date")),
                type=_text(metadata.get("issue_type")),
            )
        return items

    async def _items_on(
        self, tenant_id: str, project_id: str, release: Release | None, day: date
    ) -> tuple[dict[str, BaselineItem], bool, date | None]:
        """The requirements in scope at the end of ``day``, whether rebuilt, and the snapshot day.

        The scope's own snapshot of the day, or of the last day before it within
        a week. A release defined later reads the project's snapshot, kept to the
        release's requirements of today; with no snapshot at all, the issues in
        scope today created by then. Both are marked reconstructed.
        """
        cutoff = self._end_of(day)
        start = day - timedelta(days=SNAPSHOT_LOOKBACK_DAYS)
        own = await self._delivery.history(tenant_id, project_id, start, day, release)
        if own:
            snapshot = own[-1]
            return (
                await self._from_snapshot(tenant_id, snapshot, None, cutoff),
                False,
                snapshot.day if snapshot.day != day else None,
            )
        today = self._today()
        tasks = await self._delivery.scope_tasks(tenant_id, project_id, today, release)
        if release is not None:
            project_days = await self._delivery.history(tenant_id, project_id, start, day)
            if project_days:
                snapshot = project_days[-1]
                keys = frozenset(_task_key(task) for task in tasks)
                return (
                    await self._from_snapshot(tenant_id, snapshot, keys, cutoff),
                    True,
                    snapshot.day if snapshot.day != day else None,
                )
        mapping = (await self._delivery.delivery_settings(tenant_id)).settings.mapping
        created = [
            task
            for task in tasks
            if (created_on := _created_on(task, self._zone)) is not None and created_on <= day
        ]
        facts = await self._latest_issue_facts(
            tenant_id, [_task_key(task) for task in created], cutoff
        )
        return (
            {
                _task_key(task): _item_from_fact(facts.get(_task_key(task)), mapping)
                for task in created
            },
            True,
            None,
        )

    async def _from_snapshot(
        self,
        tenant_id: str,
        snapshot: RequirementsSnapshot,
        keys: frozenset[str] | None,
        cutoff: datetime,
    ) -> dict[str, BaselineItem]:
        wanted = [key for key in snapshot.items if keys is None or key in keys]
        facts = await self._latest_issue_facts(tenant_id, wanted, cutoff)
        items: dict[str, BaselineItem] = {}
        for key in wanted:
            payload = facts[key].payload if key in facts else {}
            items[key] = BaselineItem(
                stage=snapshot.items[key],
                points=_number(payload.get("story_points")),
                due=_iso_date(payload.get("due_date")),
                type=_text(payload.get("issue_type")),
            )
        return items

    async def _latest_issue_facts(
        self, tenant_id: str, keys: Iterable[str], cutoff: datetime
    ) -> dict[str, FactEvent]:
        """Each issue's last synced state before ``cutoff``: its issue fact."""
        latest: dict[str, FactEvent] = {}
        for key in keys:
            facts = await self._facts.list_facts(
                tenant_id, EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=key)
            )
            before = [
                fact
                for fact in facts
                if fact.source == ISSUE_FACT_SOURCE and fact.observed_at < cutoff
            ]
            if before:
                latest[key] = max(before, key=lambda fact: fact.observed_at)
        return latest

    # ---- lookups ---------------------------------------------------------

    async def _project(
        self, tenant_id: str, project_id: str, as_of: date | None = None
    ) -> GraphNode:
        project = await self._graph.get_node(tenant_id, project_id, as_of=as_of)
        if project is None or project.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"No project {project_id!r}.")
        return project

    async def _release(
        self, tenant_id: str, project_id: str, release_id: str | None
    ) -> Release | None:
        if release_id is None:
            return None
        release = await self._releases.get(tenant_id, release_id)
        if release is None or release.project_id != project_id:
            raise GraphNotFound(f"No release {release_id!r} in project {project_id!r}.")
        return release

    def _end_of(self, day: date) -> datetime:
        """The first moment after ``day`` in the tenant's zone."""
        return datetime.combine(day + timedelta(days=1), time(), tzinfo=self._zone)


def _scope(project_id: str, release: Release | None) -> tuple[BaselineScopeKind, str]:
    if release is not None:
        return BaselineScopeKind.RELEASE, release.release_id
    return BaselineScopeKind.PROJECT, project_id


def _item_from_fact(fact: FactEvent | None, mapping: StageMapping) -> BaselineItem:
    """A requirement rebuilt from its last synced state of the day; no stage if none was."""
    if fact is None:
        return BaselineItem(stage=None)
    payload = fact.payload
    placement = place(
        mapping, status=_text(payload.get("status")), state=_text(payload.get("state"))
    )
    stage: DeliveryStage | None = placement.stage
    return BaselineItem(
        stage=stage,
        points=_number(payload.get("story_points")),
        due=_iso_date(payload.get("due_date")),
        type=_text(payload.get("issue_type")),
    )


def _created_on(task: GraphNode, zone: tzinfo) -> date | None:
    value = task.metadata.get("created_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        created = datetime.fromisoformat(value)
    except ValueError:
        return _iso_date(value)
    if created.tzinfo is None:
        return created.date()
    return created.astimezone(zone).date()


def _task_key(task: GraphNode) -> str:
    key = task.metadata.get("key")
    return key if isinstance(key, str) and key else task.id


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _iso_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _labels_by_key(tasks: Iterable[GraphNode]) -> Mapping[str, tuple[str, ...]]:
    """Each requirement's tracker labels, by key."""
    return {_task_key(task): split_names(task.metadata.get("labels")) for task in tasks}
