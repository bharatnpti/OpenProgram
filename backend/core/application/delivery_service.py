"""Requirements by delivery stage: counted live, kept daily, and drawn over time.

A project's requirements are the tracker issues it owns (the same ownership
its progress uses) whose type counts as a requirement. Each sits in one of six
stages, read from its tracker status through the tenant's stage mapping.
Today's counts are computed from the graph on every read; a snapshot of each
project is stored daily, so earlier days read what was true then and the
timeline needs no replay of history.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from core.application.persona_views import owned_project_tasks
from core.domain.delivery import (
    DEFAULT_STAGE_MAPPING,
    STAGE_LABELS,
    STAGE_ORDER,
    DeliverySettings,
    DeliveryStage,
    RequirementItem,
    RequirementsSnapshot,
    StageMapping,
    StageMove,
    place,
    stage_moves,
)
from core.domain.errors import GraphNotFound
from core.domain.forecast import Release
from core.domain.graph import EdgeKind, GraphNode, NodeKind
from core.ports.delivery import DeliverySettingsRepository, RequirementsSnapshotRepository
from core.ports.forecast import ReleaseRepository
from core.ports.repositories import GraphRepository

DEFAULT_TIMELINE_DAYS = 30
MAX_TIMELINE_DAYS = 180


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def release_snapshot_id(release_id: str) -> str:
    """Where a release's daily snapshots are kept, beside its project's."""
    return f"release:{release_id}"


@dataclass(frozen=True, kw_only=True)
class StageCountView:
    stage: DeliveryStage
    label: str
    count: int
    points: float
    #: Against the latest earlier snapshot; None when there is none.
    change: int | None


@dataclass(frozen=True, kw_only=True)
class TimelinePoint:
    day: date
    counts: Mapping[DeliveryStage, int]


@dataclass(frozen=True, kw_only=True)
class RequirementView:
    item: RequirementItem
    assignee_name: str | None
    #: The first day of the run of snapshots, up to today, in this stage.
    in_stage_since: date | None


@dataclass(frozen=True, kw_only=True)
class RequirementsView:
    project_id: str
    project_name: str
    as_of: date
    #: Set when the view covers one release of the project.
    release_id: str | None
    release_name: str | None
    #: Counted from the graph now (today), not read from a stored snapshot.
    live: bool
    #: False for an earlier day nothing was recorded on.
    available: bool
    total: int
    done: int
    percent_complete: float | None
    has_points: bool
    points_total: float
    points_done: float
    stages: tuple[StageCountView, ...]
    timeline: tuple[TimelinePoint, ...]
    moves: tuple[StageMove, ...]
    previous_day: date | None
    unmapped_statuses: tuple[str, ...]
    excluded: int
    requirements: tuple[RequirementView, ...]


@dataclass(frozen=True, kw_only=True)
class ObservedStatus:
    """A status the tenant's synced issues carry, and where the mapping puts it."""

    status: str
    issues: int
    issue_types: tuple[str, ...]
    stage: DeliveryStage | None
    mapped: bool


@dataclass(frozen=True, kw_only=True)
class DeliverySettingsView:
    settings: DeliverySettings
    #: True while the tenant has saved no mapping and the default applies.
    is_default: bool


class DeliveryService:
    def __init__(
        self,
        *,
        graph_repository: GraphRepository,
        settings_repository: DeliverySettingsRepository,
        snapshot_repository: RequirementsSnapshotRepository,
        release_repository: ReleaseRepository | None = None,
        clock: Callable[[], datetime] = _utc_now,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._graph = graph_repository
        self._settings = settings_repository
        self._snapshots = snapshot_repository
        self._releases = release_repository
        self._clock = clock
        self._today = today or (lambda: clock().date())

    # ---- stage mapping -------------------------------------------------

    async def delivery_settings(self, tenant_id: str) -> DeliverySettingsView:
        stored = await self._settings.get(tenant_id)
        if stored is None:
            return DeliverySettingsView(
                settings=DeliverySettings(tenant_id=tenant_id, mapping=DEFAULT_STAGE_MAPPING),
                is_default=True,
            )
        return DeliverySettingsView(settings=stored, is_default=False)

    async def save_stage_mapping(
        self, tenant_id: str, mapping: StageMapping, *, actor: str
    ) -> DeliverySettingsView:
        settings = DeliverySettings(
            tenant_id=tenant_id, mapping=mapping, updated_at=self._clock(), updated_by=actor
        )
        await self._settings.save(settings)
        return DeliverySettingsView(settings=settings, is_default=False)

    async def observed_statuses(
        self, tenant_id: str, mapping: StageMapping | None = None
    ) -> list[ObservedStatus]:
        """Every status on the tenant's synced issues, most common first, placed by ``mapping``.

        Without ``mapping`` the stored one (or the default) places them, so the
        console can preview an unsaved mapping against real statuses.
        """
        mapping = mapping or (await self.delivery_settings(tenant_id)).settings.mapping
        counts: Counter[str] = Counter()
        types: dict[str, set[str]] = {}
        states: dict[str, str] = {}
        for task in await self._graph.list_nodes(tenant_id, NodeKind.TASK):
            status = _text(task.metadata.get("status"))
            if status is None:
                continue
            counts[status] += 1
            issue_type = _text(task.metadata.get("issue_type"))
            if issue_type is not None:
                types.setdefault(status, set()).add(issue_type)
            states.setdefault(status, _text(task.metadata.get("state")) or "")
        observed: list[ObservedStatus] = []
        for status, issues in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            placement = place(mapping, status=status, state=states.get(status))
            observed.append(
                ObservedStatus(
                    status=status,
                    issues=issues,
                    issue_types=tuple(sorted(types.get(status, ()))),
                    stage=placement.stage,
                    mapped=placement.mapped,
                )
            )
        return observed

    # ---- snapshots -----------------------------------------------------

    async def compute_snapshots(
        self,
        tenant_id: str,
        day: date,
        project_ids: Sequence[str] | None = None,
        releases: Sequence[Release] = (),
    ) -> list[RequirementsSnapshot]:
        """Each project's and release's requirements as the graph holds them, dated ``day``."""
        mapping = (await self.delivery_settings(tenant_id)).settings.mapping
        if project_ids is None:
            project_ids = [
                node.id for node in await self._graph.list_nodes(tenant_id, NodeKind.PROJECT)
            ]
        wanted = list(dict.fromkeys([*project_ids, *(release.project_id for release in releases)]))
        owned = await owned_project_tasks(self._graph, tenant_id, wanted, day)
        assignees = await self._assignees(tenant_id, day)
        computed_at = self._clock()
        snapshots = [
            _snapshot(
                tenant_id,
                project_id,
                day,
                _requirements(tasks, mapping, assignees),
                excluded=_excluded_count(tasks, mapping),
                computed_at=computed_at,
            )
            for project_id, tasks in owned.items()
            if project_id in project_ids
        ]
        for release in releases:
            tasks = tuple(
                task
                for task in owned.get(release.project_id, ())
                if release.includes(task.metadata)
            )
            snapshots.append(
                _snapshot(
                    tenant_id,
                    release_snapshot_id(release.release_id),
                    day,
                    _requirements(tasks, mapping, assignees),
                    excluded=_excluded_count(tasks, mapping),
                    computed_at=computed_at,
                )
            )
        return snapshots

    async def record_snapshots(self, tenant_id: str, day: date) -> int:
        """Store today's snapshot of every project and release; a later run replaces it."""
        releases = await self._releases.list_all(tenant_id) if self._releases else []
        snapshots = await self.compute_snapshots(tenant_id, day, releases=releases)
        for snapshot in snapshots:
            await self._snapshots.save(snapshot)
        return len(snapshots)

    async def snapshot(
        self, tenant_id: str, project_id: str, day: date, release: Release | None = None
    ) -> RequirementsSnapshot | None:
        """The scope's snapshot for ``day``: live for today, stored for an earlier day."""
        if day >= self._today():
            computed = await self.compute_snapshots(
                tenant_id,
                day,
                [] if release is not None else [project_id],
                releases=[release] if release is not None else (),
            )
            return computed[0] if computed else None
        return await self._snapshots.get(tenant_id, _scope_id(project_id, release), day)

    async def previous_snapshot(
        self, tenant_id: str, project_id: str, day: date, release: Release | None = None
    ) -> RequirementsSnapshot | None:
        return await self._snapshots.latest_before(tenant_id, _scope_id(project_id, release), day)

    async def history(
        self,
        tenant_id: str,
        project_id: str,
        start: date,
        end: date,
        release: Release | None = None,
    ) -> list[RequirementsSnapshot]:
        """Stored snapshots of the scope from ``start`` to ``end``, oldest first."""
        return await self._snapshots.list_between(
            tenant_id, _scope_id(project_id, release), start, end
        )

    async def is_project(self, tenant_id: str, project_id: str) -> bool:
        node = await self._graph.get_node(tenant_id, project_id)
        return node is not None and node.kind is NodeKind.PROJECT

    async def scope_tasks(
        self, tenant_id: str, project_id: str, day: date, release: Release | None = None
    ) -> tuple[GraphNode, ...]:
        """The issues of the project (or of one of its releases) that count as requirements."""
        mapping = (await self.delivery_settings(tenant_id)).settings.mapping
        owned = await owned_project_tasks(self._graph, tenant_id, [project_id], day)
        return tuple(
            task
            for task in owned.get(project_id, ())
            if mapping.counts_type(_text(task.metadata.get("issue_type")))
            and (release is None or release.includes(task.metadata))
        )

    # ---- the requirements view -----------------------------------------

    async def requirements(
        self,
        tenant_id: str,
        project_id: str,
        as_of: date,
        *,
        days: int = DEFAULT_TIMELINE_DAYS,
        release: Release | None = None,
    ) -> RequirementsView:
        project = await self._graph.get_node(tenant_id, project_id)
        if project is None or project.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"project {project_id} not found for tenant {tenant_id}")
        if release is not None and release.project_id != project_id:
            raise GraphNotFound(f"release {release.release_id} is not in project {project_id}")
        scope_id = _scope_id(project_id, release)
        days = max(1, min(days, MAX_TIMELINE_DAYS))
        live = as_of >= self._today()
        mapping = (await self.delivery_settings(tenant_id)).settings.mapping
        items: tuple[RequirementItem, ...] = ()
        current: RequirementsSnapshot | None
        if live:
            owned = await owned_project_tasks(self._graph, tenant_id, [project_id], as_of)
            tasks = tuple(
                task
                for task in owned.get(project_id, ())
                if release is None or release.includes(task.metadata)
            )
            items = _requirements(tasks, mapping, await self._assignees(tenant_id, as_of))
            current = _snapshot(
                tenant_id,
                scope_id,
                as_of,
                items,
                excluded=_excluded_count(tasks, mapping),
                computed_at=self._clock(),
            )
        else:
            current = await self._snapshots.get(tenant_id, scope_id, as_of)
        history = await self._snapshots.list_between(
            tenant_id, scope_id, as_of - timedelta(days=days - 1), as_of
        )
        previous = await self._snapshots.latest_before(tenant_id, scope_id, as_of)
        timeline = [snapshot for snapshot in history if snapshot.day < as_of]
        if current is not None:
            timeline.append(current)
        names = await self._member_names(tenant_id)
        return _view(
            project=project,
            release=release,
            as_of=as_of,
            live=live,
            current=current,
            previous=previous,
            timeline=timeline,
            items=items or _items_from_snapshot(current),
            names=names,
        )

    async def _assignees(self, tenant_id: str, as_of: date) -> dict[str, str]:
        assigned: dict[str, str] = {}
        for edge in await self._graph.list_edges(tenant_id, kind=EdgeKind.ASSIGNED_TO):
            if edge.is_active_on(as_of):
                assigned.setdefault(edge.to_node_id, edge.from_node_id)
        return assigned

    async def _member_names(self, tenant_id: str) -> dict[str, str]:
        return {
            node.id: node.name
            for node in await self._graph.list_nodes(tenant_id, NodeKind.DEVELOPER)
        }


def _requirements(
    tasks: Iterable[GraphNode], mapping: StageMapping, assignees: Mapping[str, str]
) -> tuple[RequirementItem, ...]:
    items: list[RequirementItem] = []
    for task in tasks:
        if not mapping.counts_type(_text(task.metadata.get("issue_type"))):
            continue
        status = _text(task.metadata.get("status"))
        placement = place(mapping, status=status, state=_text(task.metadata.get("state")))
        if placement.stage is None:
            continue
        items.append(
            RequirementItem(
                key=_text(task.metadata.get("key")) or task.id,
                title=task.name,
                stage=placement.stage,
                status=status,
                mapped=placement.mapped,
                assignee_id=assignees.get(task.id),
                story_points=_number(task.metadata.get("story_points")),
                due_date=_text(task.metadata.get("due_date")),
            )
        )
    return tuple(sorted(items, key=lambda item: (STAGE_ORDER.index(item.stage), item.key)))


def _excluded_count(tasks: Iterable[GraphNode], mapping: StageMapping) -> int:
    count = 0
    for task in tasks:
        if not mapping.counts_type(_text(task.metadata.get("issue_type"))):
            continue
        placement = place(
            mapping,
            status=_text(task.metadata.get("status")),
            state=_text(task.metadata.get("state")),
        )
        if placement.stage is None:
            count += 1
    return count


def _snapshot(
    tenant_id: str,
    project_id: str,
    day: date,
    items: Sequence[RequirementItem],
    *,
    excluded: int,
    computed_at: datetime,
) -> RequirementsSnapshot:
    counts = {stage: 0 for stage in STAGE_ORDER}
    points = {stage: 0.0 for stage in STAGE_ORDER}
    for item in items:
        counts[item.stage] += 1
        points[item.stage] += item.story_points or 0.0
    return RequirementsSnapshot(
        tenant_id=tenant_id,
        project_id=project_id,
        day=day,
        stage_counts=counts,
        stage_points=points,
        has_points=bool(items) and all(item.story_points is not None for item in items),
        excluded=excluded,
        unmapped_statuses=tuple(
            sorted({item.status for item in items if not item.mapped and item.status})
        ),
        items={item.key: item.stage for item in items},
        titles={item.key: item.title for item in items},
        computed_at=computed_at,
    )


def _items_from_snapshot(snapshot: RequirementsSnapshot | None) -> tuple[RequirementItem, ...]:
    """An earlier day's requirements, from what its snapshot kept: key, title and stage."""
    if snapshot is None:
        return ()
    return tuple(
        sorted(
            (
                RequirementItem(
                    key=key,
                    title=snapshot.titles.get(key, key),
                    stage=stage,
                    status=None,
                    mapped=True,
                )
                for key, stage in snapshot.items.items()
            ),
            key=lambda item: (STAGE_ORDER.index(item.stage), item.key),
        )
    )


def _scope_id(project_id: str, release: Release | None) -> str:
    return release_snapshot_id(release.release_id) if release is not None else project_id


def _view(
    *,
    project: GraphNode,
    release: Release | None,
    as_of: date,
    live: bool,
    current: RequirementsSnapshot | None,
    previous: RequirementsSnapshot | None,
    timeline: Sequence[RequirementsSnapshot],
    items: Sequence[RequirementItem],
    names: Mapping[str, str],
) -> RequirementsView:
    stages = tuple(
        StageCountView(
            stage=stage,
            label=STAGE_LABELS[stage],
            count=current.stage_counts.get(stage, 0) if current else 0,
            points=current.stage_points.get(stage, 0.0) if current else 0.0,
            change=(
                current.stage_counts.get(stage, 0) - previous.stage_counts.get(stage, 0)
                if current is not None and previous is not None
                else None
            ),
        )
        for stage in STAGE_ORDER
    )
    since = _in_stage_since(timeline)
    return RequirementsView(
        project_id=project.id,
        project_name=project.name,
        as_of=as_of,
        release_id=release.release_id if release is not None else None,
        release_name=release.name if release is not None else None,
        live=live,
        available=current is not None,
        total=current.total if current else 0,
        done=current.done if current else 0,
        percent_complete=current.percent_complete if current else None,
        has_points=current.has_points if current else False,
        points_total=current.points_total if current else 0.0,
        points_done=current.points_done if current else 0.0,
        stages=stages,
        timeline=tuple(
            TimelinePoint(day=snapshot.day, counts=dict(snapshot.stage_counts))
            for snapshot in timeline
        ),
        moves=stage_moves(previous, current) if current is not None else (),
        previous_day=previous.day if previous is not None else None,
        unmapped_statuses=current.unmapped_statuses if current else (),
        excluded=current.excluded if current else 0,
        requirements=tuple(
            RequirementView(
                item=item,
                assignee_name=names.get(item.assignee_id) if item.assignee_id else None,
                in_stage_since=since.get(item.key),
            )
            for item in items
        ),
    )


def _in_stage_since(timeline: Sequence[RequirementsSnapshot]) -> dict[str, date]:
    """Per requirement, the first day of its latest unbroken run in today's stage."""
    if not timeline:
        return {}
    ordered = sorted(timeline, key=lambda snapshot: snapshot.day)
    latest = ordered[-1]
    since: dict[str, date] = {}
    for key, stage in latest.items.items():
        first = latest.day
        for snapshot in reversed(ordered[:-1]):
            if snapshot.items.get(key) != stage:
                break
            first = snapshot.day
        since[key] = first
    return since


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
