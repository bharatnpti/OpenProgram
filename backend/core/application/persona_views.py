from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from core.application.rollup_service import RollupService
from core.domain.errors import GraphNotFound
from core.domain.graph import EdgeKind, EntityRef, FactEvent, GraphNode, GraphTree, NodeKind
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import DeveloperStatus, StatusSource
from core.ports.repositories import (
    GraphRepository,
    RollupRepository,
    StatusRepository,
    TimeSeriesRepository,
)

TASK_FACT_LOOKBACK_DAYS = 30


@dataclass(frozen=True, kw_only=True)
class TaskStatusView:
    rag: Rag
    source: StatusSource
    confidence: float | None
    source_ref: EntityRef


@dataclass(frozen=True, kw_only=True)
class FocusTaskView:
    id: str
    name: str
    rag: Rag
    source: StatusSource
    confidence: float | None
    deadline: date | None


@dataclass(frozen=True, kw_only=True)
class FocusItemView:
    kind: Literal["task", "blocker"]
    label: str
    source: StatusSource
    source_ref: EntityRef
    confidence: float | None = None
    deadline: date | None = None


@dataclass(frozen=True, kw_only=True)
class FocusView:
    developer_id: str
    developer_name: str
    as_of: date
    status_source: StatusSource
    developer_confirmed: bool
    status_as_of: date | None
    summary: str
    blockers: tuple[str, ...]
    tasks: tuple[FocusTaskView, ...]
    focus: tuple[FocusItemView, ...]


@dataclass(frozen=True, kw_only=True)
class BlockerView:
    id: str
    description: str
    age_days: int
    owner_id: str
    owner_name: str
    source: StatusSource
    status_as_of: date
    source_ref: EntityRef


@dataclass(frozen=True, kw_only=True)
class PodBlockersView:
    pod_id: str
    pod_name: str
    as_of: date
    blockers: tuple[BlockerView, ...]


@dataclass(frozen=True, kw_only=True)
class CheckinDeveloperView:
    developer_id: str
    developer_name: str
    state: Literal["confirmed", "stale", "missing"]
    source: StatusSource
    status_as_of: date | None
    summary: str


@dataclass(frozen=True, kw_only=True)
class PodCheckinsView:
    pod_id: str
    pod_name: str
    as_of: date
    confirmed: int
    stale: int
    missing: int
    developers: tuple[CheckinDeveloperView, ...]


@dataclass(frozen=True, kw_only=True)
class TaskProgressView:
    id: str
    name: str
    rag: Rag
    source: StatusSource
    confidence: float | None
    deadline: date | None


@dataclass(frozen=True, kw_only=True)
class ProjectProgressView:
    project_id: str
    project_name: str
    as_of: date
    rag: Rag
    source: StatusSource
    confidence: float | None
    percent_complete: float
    total_tasks: int
    green_tasks: int
    amber_tasks: int
    red_tasks: int
    unknown_tasks: int
    factors: tuple[RollupFactor, ...]
    tasks: tuple[TaskProgressView, ...]


@dataclass(frozen=True, kw_only=True)
class WorkstreamProgressView:
    workstream_id: str
    workstream_name: str
    as_of: date
    rag: Rag
    source: StatusSource
    confidence: float | None
    percent_complete: float
    total_tasks: int
    green_tasks: int
    amber_tasks: int
    red_tasks: int
    unknown_tasks: int
    factors: tuple[RollupFactor, ...]
    tasks: tuple[TaskProgressView, ...]


@dataclass(frozen=True, kw_only=True)
class TreeNodeView:
    id: str
    kind: NodeKind
    name: str
    rag: Rag | None
    source: StatusSource | None
    confidence: float | None
    factors: tuple[RollupFactor, ...]


@dataclass(frozen=True, kw_only=True)
class TreeEdgeView:
    from_node_id: str
    to_node_id: str
    kind: EdgeKind


@dataclass(frozen=True, kw_only=True)
class ProgramTreeView:
    root_id: str
    as_of: date
    nodes: tuple[TreeNodeView, ...]
    edges: tuple[TreeEdgeView, ...]


@dataclass(frozen=True, kw_only=True)
class HeatmapCellView:
    row: str
    column: str
    entity_ref: EntityRef
    rag: Rag
    source: StatusSource
    why: str
    source_ref: EntityRef


@dataclass(frozen=True, kw_only=True)
class PortfolioHeatmapView:
    as_of: date
    rows: tuple[str, ...]
    columns: tuple[str, ...]
    cells: tuple[HeatmapCellView, ...]


class PersonaViewService:
    def __init__(
        self,
        graph_repository: GraphRepository,
        status_repository: StatusRepository,
        rollup_repository: RollupRepository,
        time_series_repository: TimeSeriesRepository,
    ) -> None:
        self._graph_repository = graph_repository
        self._status_repository = status_repository
        self._rollup_repository = rollup_repository
        self._time_series_repository = time_series_repository
        self._rollup_service = RollupService(status_repository, rollup_repository)

    async def focus(self, tenant_id: str, developer_id: str, as_of: date) -> FocusView:
        status = await self._status_repository.latest_developer_status(
            tenant_id, developer_id, as_of
        )
        try:
            tree = await self._graph_repository.get_program_tree(tenant_id, developer_id, as_of)
        except GraphNotFound:
            status_source = status.source if status else StatusSource.UNKNOWN
            blockers = status.blockers if status else ()
            return FocusView(
                developer_id=developer_id,
                developer_name=developer_id,
                as_of=as_of,
                status_source=status_source,
                developer_confirmed=status.developer_confirmed if status else False,
                status_as_of=status.as_of if status else None,
                summary=status.summary if status else "No developer status data is available.",
                blockers=blockers,
                tasks=(),
                focus=tuple(
                    FocusItemView(
                        kind="blocker",
                        label=blocker,
                        source=status_source,
                        source_ref=EntityRef(
                            tenant_id=tenant_id,
                            kind=NodeKind.DEVELOPER,
                            id=developer_id,
                        ),
                    )
                    for blocker in blockers
                ),
            )
        developer = tree.root
        tasks_list: list[FocusTaskView] = []
        for node in _sorted_nodes(tree.nodes):
            if node.kind is NodeKind.TASK:
                tasks_list.append(await self._focus_task(node, as_of))
        tasks = tuple(tasks_list)
        status_source = status.source if status else StatusSource.UNKNOWN
        blockers = status.blockers if status else ()
        focus = tuple(
            FocusItemView(
                kind="blocker",
                label=blocker,
                source=status_source,
                source_ref=developer.ref,
            )
            for blocker in blockers
        ) + tuple(
            FocusItemView(
                kind="task",
                label=task.name,
                source=task.source,
                source_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=task.id),
                confidence=task.confidence,
                deadline=task.deadline,
            )
            for task in tasks
            if task.rag in {Rag.AMBER, Rag.RED, Rag.UNKNOWN}
        )
        return FocusView(
            developer_id=developer_id,
            developer_name=developer.name,
            as_of=as_of,
            status_source=status_source,
            developer_confirmed=status.developer_confirmed if status else False,
            status_as_of=status.as_of if status else None,
            summary=status.summary if status else "No developer status data is available.",
            blockers=blockers,
            tasks=tasks,
            focus=focus,
        )

    async def pod_blockers(self, tenant_id: str, pod_id: str, as_of: date) -> PodBlockersView:
        tree = await self._graph_repository.get_program_tree(tenant_id, pod_id, as_of)
        developers = _developers(tree)
        blockers: list[BlockerView] = []
        for developer in developers:
            status = await self._status_repository.latest_developer_status(
                tenant_id, developer.id, as_of
            )
            if status is None:
                continue
            blockers.extend(_blockers_for_status(developer, status, as_of))
        return PodBlockersView(
            pod_id=tree.root.id,
            pod_name=tree.root.name,
            as_of=as_of,
            blockers=tuple(sorted(blockers, key=lambda item: (-item.age_days, item.owner_name))),
        )

    async def pod_checkins(self, tenant_id: str, pod_id: str, as_of: date) -> PodCheckinsView:
        tree = await self._graph_repository.get_program_tree(tenant_id, pod_id, as_of)
        developers: list[CheckinDeveloperView] = []
        for developer in _developers(tree):
            status = await self._status_repository.latest_developer_status(
                tenant_id, developer.id, as_of
            )
            state = _checkin_state(status, as_of)
            developers.append(
                CheckinDeveloperView(
                    developer_id=developer.id,
                    developer_name=developer.name,
                    state=state,
                    source=status.source if status else StatusSource.UNKNOWN,
                    status_as_of=status.as_of if status else None,
                    summary=status.summary if status else "No check-in status is available.",
                )
            )
        return PodCheckinsView(
            pod_id=tree.root.id,
            pod_name=tree.root.name,
            as_of=as_of,
            confirmed=sum(1 for item in developers if item.state == "confirmed"),
            stale=sum(1 for item in developers if item.state == "stale"),
            missing=sum(1 for item in developers if item.state == "missing"),
            developers=tuple(sorted(developers, key=lambda item: item.developer_name)),
        )

    async def project_progress(
        self, tenant_id: str, project_id: str, as_of: date
    ) -> ProjectProgressView:
        tree = await self._graph_repository.get_program_tree(tenant_id, project_id, as_of)
        statuses = await self._node_statuses_for_tree(tree, as_of)
        root_status = statuses.get((tree.root.kind, tree.root.id))
        task_list: list[TaskProgressView] = []
        for node in _sorted_nodes(tree.nodes):
            if node.kind is NodeKind.TASK:
                task_list.append(await self._task_progress(node, as_of))
        tasks = tuple(task_list)
        counts = _task_counts(tasks)
        return ProjectProgressView(
            project_id=tree.root.id,
            project_name=tree.root.name,
            as_of=as_of,
            rag=root_status.rag if root_status else _aggregate_task_rag(tasks),
            source=root_status.source if root_status else _aggregate_task_source(tasks),
            confidence=_average_confidence(task.confidence for task in tasks),
            percent_complete=(counts["green"] / len(tasks) * 100.0) if tasks else 0.0,
            total_tasks=len(tasks),
            green_tasks=counts["green"],
            amber_tasks=counts["amber"],
            red_tasks=counts["red"],
            unknown_tasks=counts["unknown"],
            factors=root_status.factors if root_status else (),
            tasks=tasks,
        )

    async def workstream_progress(
        self,
        tenant_id: str,
        workstream_id: str,
        as_of: date,
    ) -> WorkstreamProgressView:
        tree = await self._graph_repository.get_program_tree(tenant_id, workstream_id, as_of)
        statuses = await self._node_statuses_for_tree(tree, as_of)
        root_status = statuses.get((tree.root.kind, tree.root.id))
        task_list: list[TaskProgressView] = []
        for node in _sorted_nodes(tree.nodes):
            if node.kind is NodeKind.TASK:
                task_list.append(await self._task_progress(node, as_of))
        tasks = tuple(task_list)
        counts = _task_counts(tasks)
        fallback_rag = _workstream_task_rag(tree.root, tasks, as_of)
        rag = _dominant_rag(root_status.rag if root_status else Rag.UNKNOWN, fallback_rag)
        use_root_status = (
            root_status is not None
            and root_status.rag is not Rag.UNKNOWN
            and _rag_severity(root_status.rag) >= _rag_severity(fallback_rag)
        )
        if use_root_status and root_status is not None:
            factors = root_status.factors
            source = root_status.source
        else:
            factors = _workstream_task_factors(tree.root, tasks, fallback_rag, as_of)
            source = _aggregate_task_source(tasks)
        return WorkstreamProgressView(
            workstream_id=tree.root.id,
            workstream_name=tree.root.name,
            as_of=as_of,
            rag=rag,
            source=source,
            confidence=_average_confidence(task.confidence for task in tasks),
            percent_complete=(counts["green"] / len(tasks) * 100.0) if tasks else 0.0,
            total_tasks=len(tasks),
            green_tasks=counts["green"],
            amber_tasks=counts["amber"],
            red_tasks=counts["red"],
            unknown_tasks=counts["unknown"],
            factors=factors,
            tasks=tasks,
        )

    async def program_tree(self, tenant_id: str, program_id: str, as_of: date) -> ProgramTreeView:
        tree = await self._graph_repository.get_program_tree(tenant_id, program_id, as_of)
        node_statuses = await self._node_statuses_for_tree(tree, as_of)
        nodes: list[TreeNodeView] = []
        for node in _sorted_nodes(tree.nodes):
            if node.kind is NodeKind.TASK:
                task_status = await self._task_status(node, as_of)
                nodes.append(
                    TreeNodeView(
                        id=node.id,
                        kind=node.kind,
                        name=node.name,
                        rag=task_status.rag,
                        source=task_status.source,
                        confidence=task_status.confidence,
                        factors=(),
                    )
                )
                continue
            status = node_statuses.get((node.kind, node.id))
            nodes.append(
                TreeNodeView(
                    id=node.id,
                    kind=node.kind,
                    name=node.name,
                    rag=status.rag if status else None,
                    source=status.source if status else None,
                    confidence=None,
                    factors=status.factors if status else (),
                )
            )
        return ProgramTreeView(
            root_id=tree.root.id,
            as_of=as_of,
            nodes=tuple(nodes),
            edges=tuple(
                TreeEdgeView(
                    from_node_id=edge.from_node_id,
                    to_node_id=edge.to_node_id,
                    kind=edge.kind,
                )
                for edge in sorted(
                    tree.edges,
                    key=lambda item: (item.from_node_id, item.to_node_id),
                )
            ),
        )

    async def portfolio_heatmap(
        self,
        tenant_id: str,
        as_of: date,
        program_root_id: str | None = None,
    ) -> PortfolioHeatmapView:
        statuses = await self._rollup_repository.list_node_statuses(tenant_id, as_of)
        if program_root_id is not None:
            if not statuses:
                try:
                    tree = await self._graph_repository.get_program_tree(
                        tenant_id, program_root_id, as_of
                    )
                except GraphNotFound:
                    return PortfolioHeatmapView(as_of=as_of, rows=(), columns=(), cells=())
                statuses = list(await self._rollup_service.compute_and_record(tree, as_of))
        else:
            resolved_root_id = await self._first_program_id(tenant_id)
            if resolved_root_id is None:
                return PortfolioHeatmapView(as_of=as_of, rows=(), columns=(), cells=())
            try:
                tree = await self._graph_repository.get_program_tree(
                    tenant_id, resolved_root_id, as_of
                )
            except GraphNotFound:
                return PortfolioHeatmapView(as_of=as_of, rows=(), columns=(), cells=())
            tree_ids = {node.id for node in tree.nodes}
            statuses = [status for status in statuses if status.entity_ref.id in tree_ids]
            if not statuses:
                statuses = list(await self._rollup_service.compute_and_record(tree, as_of))
        cells = tuple(_heatmap_cell(status) for status in statuses)
        rows = tuple(dict.fromkeys(cell.row for cell in cells))
        columns = tuple(dict.fromkeys(cell.column for cell in cells))
        return PortfolioHeatmapView(as_of=as_of, rows=rows, columns=columns, cells=cells)

    async def _first_program_id(self, tenant_id: str) -> str | None:
        programs = await self._graph_repository.list_nodes(tenant_id, NodeKind.PROGRAM)
        return programs[0].id if programs else None

    async def _node_statuses_for_tree(
        self, tree: GraphTree, as_of: date
    ) -> dict[tuple[NodeKind, str], NodeStatus]:
        statuses: dict[tuple[NodeKind, str], NodeStatus] = {}
        rollup_nodes = tuple(node for node in tree.nodes if node.kind is not NodeKind.TASK)
        for node in rollup_nodes:
            status = await self._rollup_repository.latest_node_status(
                node.tenant_id,
                node.ref,
                as_of,
            )
            if status is not None:
                statuses[(node.kind, node.id)] = status
        if len(statuses) < len(rollup_nodes):
            computed = await self._rollup_service.compute_and_record(tree, as_of)
            for status in computed:
                statuses.setdefault(
                    (status.entity_ref.kind, status.entity_ref.id),
                    status,
                )
        return statuses

    async def _focus_task(self, node: GraphNode, as_of: date) -> FocusTaskView:
        status = await self._task_status(node, as_of)
        return FocusTaskView(
            id=node.id,
            name=node.name,
            rag=status.rag,
            source=status.source,
            confidence=status.confidence,
            deadline=_deadline(node),
        )

    async def _task_progress(self, node: GraphNode, as_of: date) -> TaskProgressView:
        status = await self._task_status(node, as_of)
        return TaskProgressView(
            id=node.id,
            name=node.name,
            rag=status.rag,
            source=status.source,
            confidence=status.confidence,
            deadline=_deadline(node),
        )

    async def _task_status(self, node: GraphNode, as_of: date) -> TaskStatusView:
        facts = await self._time_series_repository.list_facts(
            node.tenant_id,
            node.ref,
            _since_for_as_of(as_of),
        )
        fact = max(facts, key=lambda item: item.observed_at) if facts else None
        return TaskStatusView(
            rag=_rag_from_fact_or_metadata(fact, node),
            source=_source_from_fact(fact),
            confidence=_confidence_from_fact(fact),
            source_ref=node.ref,
        )


def _developers(tree: GraphTree) -> tuple[GraphNode, ...]:
    return tuple(node for node in _sorted_nodes(tree.nodes) if node.kind is NodeKind.DEVELOPER)


def _sorted_nodes(nodes: tuple[GraphNode, ...]) -> tuple[GraphNode, ...]:
    return tuple(sorted(nodes, key=lambda node: (node.kind.value, node.name, node.id)))


def _since_for_as_of(as_of: date) -> datetime:
    return datetime.combine(as_of - timedelta(days=TASK_FACT_LOOKBACK_DAYS), time.min, tzinfo=UTC)


def _blockers_for_status(
    developer: GraphNode, status: DeveloperStatus, as_of: date
) -> list[BlockerView]:
    return [
        BlockerView(
            id=f"{developer.id}:{index}",
            description=blocker,
            age_days=max((as_of - status.as_of).days, 0),
            owner_id=developer.id,
            owner_name=developer.name,
            source=status.source,
            status_as_of=status.as_of,
            source_ref=developer.ref,
        )
        for index, blocker in enumerate(status.blockers, start=1)
    ]


def _checkin_state(
    status: DeveloperStatus | None,
    as_of: date,
) -> Literal["confirmed", "stale", "missing"]:
    if status is None or status.source is StatusSource.UNKNOWN:
        return "missing"
    if status.source is StatusSource.CONFIRMED and status.as_of == as_of:
        return "confirmed"
    return "stale"


def _task_counts(tasks: tuple[TaskProgressView, ...]) -> dict[str, int]:
    return {
        "green": sum(1 for task in tasks if task.rag is Rag.GREEN),
        "amber": sum(1 for task in tasks if task.rag is Rag.AMBER),
        "red": sum(1 for task in tasks if task.rag is Rag.RED),
        "unknown": sum(1 for task in tasks if task.rag is Rag.UNKNOWN),
    }


def _aggregate_task_rag(tasks: tuple[TaskProgressView, ...]) -> Rag:
    if any(task.rag is Rag.RED for task in tasks):
        return Rag.RED
    if any(task.rag is Rag.AMBER for task in tasks):
        return Rag.AMBER
    if tasks and all(task.rag is Rag.GREEN for task in tasks):
        return Rag.GREEN
    return Rag.UNKNOWN


def _aggregate_task_source(tasks: tuple[TaskProgressView, ...]) -> StatusSource:
    sources = {task.source for task in tasks}
    if not sources or StatusSource.UNKNOWN in sources:
        return StatusSource.UNKNOWN
    if StatusSource.STALE in sources:
        return StatusSource.STALE
    if StatusSource.INFERRED in sources:
        return StatusSource.INFERRED
    return StatusSource.CONFIRMED


def _workstream_task_rag(
    node: GraphNode,
    tasks: tuple[TaskProgressView, ...],
    as_of: date,
) -> Rag:
    metadata_rag = _rag_from_value(node.metadata.get("status"))
    if metadata_rag is Rag.RED or any(task.rag is Rag.RED for task in tasks):
        return Rag.RED
    if metadata_rag is Rag.AMBER or any(task.rag is Rag.AMBER for task in tasks):
        return Rag.AMBER
    if _approaching_target_date(node, as_of):
        return Rag.AMBER
    if any(task.rag is Rag.UNKNOWN for task in tasks):
        return Rag.AMBER
    if tasks and all(task.rag is Rag.GREEN for task in tasks):
        return Rag.GREEN
    if metadata_rag is Rag.GREEN:
        return Rag.GREEN
    return Rag.UNKNOWN


def _workstream_task_factors(
    node: GraphNode,
    tasks: tuple[TaskProgressView, ...],
    rag: Rag,
    as_of: date,
) -> tuple[RollupFactor, ...]:
    if not tasks and rag is Rag.UNKNOWN:
        return (
            RollupFactor(
                description="No child task status data is available.",
                contributes=Rag.UNKNOWN,
                source_ref=node.ref,
            ),
        )
    factors = [
        RollupFactor(
            description=f"Task {task.name} is {task.rag.value}.",
            contributes=Rag.AMBER if task.rag is Rag.UNKNOWN else task.rag,
            source_ref=EntityRef(tenant_id=node.tenant_id, kind=NodeKind.TASK, id=task.id),
        )
        for task in tasks
        if task.rag is not Rag.GREEN
    ]
    if _approaching_target_date(node, as_of):
        target_date = _deadline(node)
        factors.append(
            RollupFactor(
                description=f"Target date {target_date} is approaching.",
                contributes=Rag.AMBER,
                source_ref=node.ref,
            )
        )
    if factors:
        return tuple(factors)
    return (
        RollupFactor(
            description="All child tasks show active progress with no blockers.",
            contributes=Rag.GREEN,
            source_ref=node.ref,
        ),
    )


def _dominant_rag(left: Rag, right: Rag) -> Rag:
    return left if _rag_severity(left) >= _rag_severity(right) else right


def _rag_severity(rag: Rag) -> int:
    return {
        Rag.UNKNOWN: 0,
        Rag.GREEN: 1,
        Rag.AMBER: 2,
        Rag.RED: 3,
    }[rag]


def _approaching_target_date(node: GraphNode, as_of: date) -> bool:
    phase = node.metadata.get("phase")
    if isinstance(phase, str) and phase.strip().lower() == "done":
        return False
    deadline = _deadline(node)
    if deadline is None:
        return False
    return as_of <= deadline <= as_of + timedelta(days=14)


def _average_confidence(values: Iterable[float | None]) -> float | None:
    numbers = [value for value in values if isinstance(value, int | float)]
    return sum(numbers) / len(numbers) if numbers else None


def _deadline(node: GraphNode) -> date | None:
    for key in ("deadline", "due_date", "target_date"):
        value = node.metadata.get(key)
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                continue
    return None


def _rag_from_fact_or_metadata(fact: FactEvent | None, node: GraphNode) -> Rag:
    if fact is not None:
        status = _rag_from_value(fact.payload.get("status"))
        if status is not None:
            return status
    return _rag_from_value(node.metadata.get("status")) or Rag.UNKNOWN


def _rag_from_value(value: object) -> Rag | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"done", "complete", "completed", "green"}:
        return Rag.GREEN
    if normalized in {"at_risk", "at-risk", "amber", "warning"}:
        return Rag.AMBER
    if normalized in {"blocked", "red"}:
        return Rag.RED
    if normalized == "unknown":
        return Rag.UNKNOWN
    return None


def _source_from_fact(fact: FactEvent | None) -> StatusSource:
    if fact is None:
        return StatusSource.UNKNOWN
    value = fact.payload.get("source")
    if isinstance(value, str):
        try:
            return StatusSource(value)
        except ValueError:
            pass
    return StatusSource.INFERRED


def _confidence_from_fact(fact: FactEvent | None) -> float | None:
    if fact is None:
        return None
    value = fact.payload.get("confidence")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return None


def _heatmap_cell(status: NodeStatus) -> HeatmapCellView:
    factor = status.factors[0] if status.factors else None
    return HeatmapCellView(
        row=status.entity_ref.kind.value,
        column=status.entity_ref.id,
        entity_ref=status.entity_ref,
        rag=status.rag,
        source=status.source,
        why=factor.description if factor else "No rollup factors are available.",
        source_ref=factor.source_ref if factor else status.entity_ref,
    )
