from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from core.application.blocker_resolution import BlockerResolutionService, ResolvedBlocker
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
class BlockerDetailView:
    """Attribution-aware projection of one open blocker for a developer."""

    blocker_id: str
    description: str
    work_item_id: str | None
    work_item_name: str | None
    pod_id: str | None
    unattributed: bool
    first_seen_on: date
    age_days: int


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
    blocker_details: tuple[BlockerDetailView, ...]
    tasks: tuple[FocusTaskView, ...]
    focus: tuple[FocusItemView, ...]


@dataclass(frozen=True, kw_only=True)
class BlockerView:
    id: str
    blocker_id: str
    description: str
    age_days: int
    owner_id: str
    owner_name: str
    source: StatusSource
    status_as_of: date
    source_ref: EntityRef
    work_item_ref: EntityRef | None
    pod_ref: EntityRef | None
    unattributed: bool
    first_seen_on: date


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
    state: Literal["confirmed", "partial", "stale", "missing"]
    source: StatusSource
    status_as_of: date | None
    summary: str


@dataclass(frozen=True, kw_only=True)
class PodCheckinsView:
    pod_id: str
    pod_name: str
    as_of: date
    confirmed: int
    partial: int
    stale: int
    missing: int
    developers: tuple[CheckinDeveloperView, ...]


@dataclass(frozen=True, kw_only=True)
class PodTaskOwnerView:
    id: str
    name: str


@dataclass(frozen=True, kw_only=True)
class PodTaskBlockerView:
    blocker_id: str
    description: str
    first_seen_on: date
    age_days: int


@dataclass(frozen=True, kw_only=True)
class PodTaskView:
    id: str
    name: str
    rag: Rag
    source: StatusSource
    confidence: float | None
    deadline: date | None
    owners: tuple[PodTaskOwnerView, ...]
    #: Red, or carrying an open blocker attributed to the task or its work item.
    blocked: bool
    open_blockers: tuple[PodTaskBlockerView, ...]


@dataclass(frozen=True, kw_only=True)
class PodTasksView:
    pod_id: str
    pod_name: str
    as_of: date
    tasks: tuple[PodTaskView, ...]


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
    # Names of the nodes the factors cite, keyed by node id.
    source_names: Mapping[str, str]
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
    # Names of the nodes the factors cite, keyed by node id.
    source_names: Mapping[str, str]
    tasks: tuple[TaskProgressView, ...]


@dataclass(frozen=True, kw_only=True)
class PodRollupView:
    """A pod's rolled-up status and the factors behind it."""

    pod_id: str
    pod_name: str
    as_of: date
    rag: Rag
    source: StatusSource
    factors: tuple[RollupFactor, ...]
    # Names of the nodes the factors cite, keyed by node id.
    source_names: Mapping[str, str]


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


# RAG mapped to an ordinal for sparkline plotting (higher is healthier).
_RAG_SCORE: dict[Rag, int] = {
    Rag.UNKNOWN: 0,
    Rag.RED: 1,
    Rag.AMBER: 2,
    Rag.GREEN: 3,
}


@dataclass(frozen=True, kw_only=True)
class TrendPointView:
    as_of: date
    rag: Rag
    source: StatusSource
    score: int


@dataclass(frozen=True, kw_only=True)
class NodeTrendView:
    entity_ref: EntityRef
    window_days: int
    start: date
    end: date
    points: tuple[TrendPointView, ...]


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
        self._blocker_resolution = BlockerResolutionService(graph_repository, status_repository)
        self._rollup_service = RollupService(
            status_repository, rollup_repository, self._blocker_resolution
        )

    async def focus(self, tenant_id: str, developer_id: str, as_of: date) -> FocusView:
        status = await self._status_repository.latest_developer_status(
            tenant_id, developer_id, as_of
        )
        resolved_blockers = await self._blocker_resolution.open_blockers_for_developer(
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
                blocker_details=_blocker_details(resolved_blockers, {}, as_of),
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
            blocker_details=_blocker_details(
                resolved_blockers,
                {node.id: node.name for node in tree.nodes},
                as_of,
            ),
            tasks=tasks,
            focus=focus,
        )

    async def pod_blockers(self, tenant_id: str, pod_id: str, as_of: date) -> PodBlockersView:
        tree = await self._pod_tree(tenant_id, pod_id, as_of)
        developers = _developers(tree)
        blockers: list[BlockerView] = []
        for developer in developers:
            status = await self._status_repository.latest_developer_status(
                tenant_id, developer.id, as_of
            )
            resolved = await self._blocker_resolution.open_blockers_for_developer(
                tenant_id, developer.id, as_of
            )
            blockers.extend(
                _blocker_view(developer, status, blocker, as_of)
                for blocker in resolved
                if tree.root.id in blocker.pod_ids
            )
        return PodBlockersView(
            pod_id=tree.root.id,
            pod_name=tree.root.name,
            as_of=as_of,
            blockers=tuple(sorted(blockers, key=lambda item: (-item.age_days, item.owner_name))),
        )

    async def pod_checkins(self, tenant_id: str, pod_id: str, as_of: date) -> PodCheckinsView:
        tree = await self._pod_tree(tenant_id, pod_id, as_of)
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
            partial=sum(1 for item in developers if item.state == "partial"),
            stale=sum(1 for item in developers if item.state == "stale"),
            missing=sum(1 for item in developers if item.state == "missing"),
            developers=tuple(sorted(developers, key=lambda item: item.developer_name)),
        )

    async def pod_tasks(self, tenant_id: str, pod_id: str, as_of: date) -> PodTasksView:
        """The tasks a pod's panel lists: its members' work, inside the pod's remit.

        A task is the pod's when both hold on ``as_of``:

        1. It is assigned (``developer --assigned_to--> task``) to a member of
           the pod (``pod --contains--> developer``).
        2. Its nearest owner up the ``contains`` chain is in the pod's remit:
           the pod itself (``pod --contains--> task`` or work item), a
           workstream the pod serves (``pod --assigned_to--> workstream``), or
           a project that contains the pod. A task with no owner at all --
           assigned straight to a member, nothing in between -- is unclaimed
           rather than foreign and counts, as it does for project progress. A
           member in two pods therefore shows it in both.

        Membership alone is not enough. A member who also works in another pod
        brings that pod's tasks along, and the pod's tree reaches them over
        ``assigned_to`` with their own workstream out of sight, so a check
        against the tree would read them as unclaimed. The owner is resolved
        against the tenant's ``contains`` edges instead -- the same ownership
        rule as ``_owned_tasks``, which stops a project's progress absorbing
        another project's tasks through a shared pod. Tasks in a served
        workstream that no member is assigned are left out: the panel lists the
        members' work, and another pod serving the same workstream lists its
        own.

        A task is ``blocked`` when it is red, or when one of the pod's open
        blockers is attributed to it: to the task itself, or to the work item
        it implements. Blocked tasks come first, then worst status first.
        """
        tree = await self._graph_repository.get_program_tree(tenant_id, pod_id, as_of)
        pod = tree.root
        if pod.kind is not NodeKind.POD:
            raise GraphNotFound(f"{pod_id} is a {pod.kind.value}, not a pod")
        members = _pod_members(tree)
        assignees = _member_assignments(tree, members)
        if not assignees:
            return PodTasksView(pod_id=pod.id, pod_name=pod.name, as_of=as_of, tasks=())

        kinds = {node.id: node.kind for node in await self._graph_repository.list_nodes(tenant_id)}
        parents: dict[str, list[str]] = {}
        for edge in await self._graph_repository.list_edges(tenant_id, kind=EdgeKind.CONTAINS):
            if edge.is_active_on(as_of):
                parents.setdefault(edge.to_node_id, []).append(edge.from_node_id)
        remit = _pod_remit(tree, parents, kinds)

        blockers: list[ResolvedBlocker] = []
        for member in members.values():
            resolved = await self._blocker_resolution.open_blockers_for_developer(
                tenant_id, member.id, as_of
            )
            blockers.extend(blocker for blocker in resolved if pod.id in blocker.pod_ids)
        blockers.sort(key=lambda item: (item.first_seen_on, item.blocker_id))

        nodes = {node.id: node for node in tree.nodes}
        tasks: list[PodTaskView] = []
        for task_id, owners in assignees.items():
            owner_ids, attributable_ids = _task_owners(task_id, parents, kinds)
            if owner_ids and owner_ids.isdisjoint(remit):
                continue
            node = nodes[task_id]
            status = await self._task_status(node, as_of)
            open_blockers = tuple(
                PodTaskBlockerView(
                    blocker_id=blocker.blocker_id,
                    description=blocker.description,
                    first_seen_on=blocker.first_seen_on,
                    age_days=_blocker_age_days(blocker, as_of),
                )
                for blocker in blockers
                if blocker.work_item_ref is not None
                and blocker.work_item_ref.id in attributable_ids
            )
            tasks.append(
                PodTaskView(
                    id=node.id,
                    name=node.name,
                    rag=status.rag,
                    source=status.source,
                    confidence=status.confidence,
                    deadline=_deadline(node),
                    owners=tuple(
                        PodTaskOwnerView(id=owner.id, name=owner.name)
                        for owner in sorted(owners, key=lambda item: (item.name, item.id))
                    ),
                    blocked=status.rag is Rag.RED or bool(open_blockers),
                    open_blockers=open_blockers,
                )
            )
        tasks.sort(key=lambda task: (not task.blocked, _TRIAGE_RANK[task.rag], task.name, task.id))
        return PodTasksView(pod_id=pod.id, pod_name=pod.name, as_of=as_of, tasks=tuple(tasks))

    async def _pod_tree(self, tenant_id: str, pod_id: str, as_of: date) -> GraphTree:
        """The subtree under a pod, or GraphNotFound if `pod_id` is not a pod.

        The tree walk starts from any node, so without this a project or program
        id would return check-ins and blockers for everyone beneath it to a role
        that was granted them for one pod.
        """
        tree = await self._graph_repository.get_program_tree(tenant_id, pod_id, as_of)
        if tree.root.kind is not NodeKind.POD:
            raise GraphNotFound(f"pod {pod_id} not found for tenant {tenant_id}")
        return tree

    async def _project_tree(self, tenant_id: str, project_id: str, as_of: date) -> GraphTree:
        """The subtree under a project, or GraphNotFound if `project_id` is not a project.

        The tree walk starts from any node, so without this a pod or program
        id would return progress for everyone beneath it instead of just the project.
        """
        tree = await self._graph_repository.get_program_tree(tenant_id, project_id, as_of)
        if tree.root.kind is not NodeKind.PROJECT:
            raise GraphNotFound(f"project {project_id} not found for tenant {tenant_id}")
        return tree

    async def _workstream_tree(self, tenant_id: str, workstream_id: str, as_of: date) -> GraphTree:
        """The subtree under a workstream, or GraphNotFound if `workstream_id` is not a workstream.

        The tree walk starts from any node, so without this a pod or project
        id would return progress for everyone beneath it instead of just the workstream.
        """
        tree = await self._graph_repository.get_program_tree(tenant_id, workstream_id, as_of)
        if tree.root.kind is not NodeKind.WORKSTREAM:
            raise GraphNotFound(f"workstream {workstream_id} not found for tenant {tenant_id}")
        return tree

    async def project_progress(
        self, tenant_id: str, project_id: str, as_of: date
    ) -> ProjectProgressView:
        tree = await self._project_tree(tenant_id, project_id, as_of)
        statuses = await self._node_statuses_for_tree(tree, as_of)
        root_status = statuses.get((tree.root.kind, tree.root.id))
        tasks = tuple(
            [await self._task_progress(node, as_of) for node in _owned_tasks(tree)],
        )
        counts = _task_counts(tasks)
        factors = root_status.factors if root_status else ()
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
            factors=factors,
            source_names=_source_names(tree, factors),
            tasks=tasks,
        )

    async def workstream_progress(
        self,
        tenant_id: str,
        workstream_id: str,
        as_of: date,
    ) -> WorkstreamProgressView:
        tree = await self._workstream_tree(tenant_id, workstream_id, as_of)
        statuses = await self._node_statuses_for_tree(tree, as_of)
        root_status = statuses.get((tree.root.kind, tree.root.id))
        tasks = tuple(
            [await self._task_progress(node, as_of) for node in _owned_tasks(tree)],
        )
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
            source_names=_source_names(tree, factors),
            tasks=tasks,
        )

    async def pod_rollup(self, tenant_id: str, pod_id: str, as_of: date) -> PodRollupView:
        """Why a pod has its colour: its rollup status and the factors behind it.

        Read exactly as the project and workstream progress read their own: the
        stored rollup for ``as_of`` where there is one, computed (never
        recorded) where there is not. Only a pod is answered -- the tree lookup
        accepts any node id, and a project's reasons are not a scrum master's
        to read under the pod capabilities.
        """
        tree = await self._graph_repository.get_program_tree(tenant_id, pod_id, as_of)
        if tree.root.kind is not NodeKind.POD:
            raise GraphNotFound(f"pod {pod_id} not found for tenant {tenant_id}")
        statuses = await self._node_statuses_for_tree(tree, as_of)
        status = statuses.get((tree.root.kind, tree.root.id))
        factors = status.factors if status else ()
        return PodRollupView(
            pod_id=tree.root.id,
            pod_name=tree.root.name,
            as_of=as_of,
            rag=status.rag if status else Rag.UNKNOWN,
            source=status.source if status else StatusSource.UNKNOWN,
            factors=factors,
            source_names=_source_names(tree, factors),
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
        """Heat for one day, computed on the fly when nothing is stored yet.

        Reads never record. They used to: a missing rollup was computed *and
        persisted*, which meant any caller could write a row of derived history
        for whatever ``as_of`` it asked about -- one /ask question about a date
        the model invented left a rollup behind -- and made stored history
        depend on who happened to open which screen. The rollup schedule
        (``connector="rollup"``) owns that write now.
        """
        statuses = await self._rollup_repository.list_node_statuses(tenant_id, as_of)
        if program_root_id is not None:
            if not statuses:
                try:
                    tree = await self._graph_repository.get_program_tree(
                        tenant_id, program_root_id, as_of
                    )
                except GraphNotFound:
                    return PortfolioHeatmapView(as_of=as_of, rows=(), columns=(), cells=())
                statuses = list(await self._rollup_service.compute(tree, as_of))
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
                statuses = list(await self._rollup_service.compute(tree, as_of))
        cells = tuple(_heatmap_cell(status) for status in statuses)
        rows = tuple(dict.fromkeys(cell.row for cell in cells))
        columns = tuple(dict.fromkeys(cell.column for cell in cells))
        return PortfolioHeatmapView(as_of=as_of, rows=rows, columns=columns, cells=cells)

    async def node_trend(
        self,
        tenant_id: str,
        kind: NodeKind,
        entity_id: str,
        as_of: date,
        window_days: int,
    ) -> NodeTrendView:
        end = as_of
        start = as_of - timedelta(days=window_days - 1)
        entity_ref = EntityRef(tenant_id=tenant_id, kind=kind, id=entity_id)
        history = await self._rollup_repository.node_status_history(
            tenant_id, entity_ref, start, end
        )
        points = tuple(
            TrendPointView(
                as_of=status.as_of,
                rag=status.rag,
                source=status.source,
                score=_RAG_SCORE.get(status.rag, 0),
            )
            for status in history
        )
        return NodeTrendView(
            entity_ref=entity_ref,
            window_days=window_days,
            start=start,
            end=end,
            points=points,
        )

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
            computed = await self._rollup_service.compute(tree, as_of)
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


def _source_names(tree: GraphTree, factors: Iterable[RollupFactor]) -> dict[str, str]:
    """Names for the nodes these factors cite, so a screen need not show ids.

    Only cited nodes, and only ones in the tree just read: a stored factor
    citing a node no longer under the root stays unnamed, and the screen shows
    its id rather than a guess.
    """
    names = {node.id: node.name for node in tree.nodes}
    return {
        factor.source_ref.id: names[factor.source_ref.id]
        for factor in factors
        if factor.source_ref.id in names
    }


def _developers(tree: GraphTree) -> tuple[GraphNode, ...]:
    return tuple(node for node in _sorted_nodes(tree.nodes) if node.kind is NodeKind.DEVELOPER)


def _sorted_nodes(nodes: tuple[GraphNode, ...]) -> tuple[GraphNode, ...]:
    return tuple(sorted(nodes, key=lambda node: (node.kind.value, node.name, node.id)))


# The delivery hierarchy, as node kinds rather than edge kinds. `project
# --contains--> pod` is the *same* edge kind as `project --contains-->
# workstream` and only the second is ownership, so ownership cannot be matched
# on `EdgeKind` alone. Used only to find the workstream(s) a root directly
# owns -- `_owned_workstream_ids` below -- not to walk all the way to tasks:
# a task with no workstream ancestor at all (assigned straight to a developer,
# with no work item in between) is legitimately unclaimed rather than foreign,
# and stays counted.
_OWNED_WORKSTREAM_DESCENT: dict[NodeKind, frozenset[NodeKind]] = {
    NodeKind.PROGRAM: frozenset({NodeKind.PROJECT}),
    NodeKind.PROJECT: frozenset({NodeKind.WORKSTREAM}),
}


def _owned_workstream_ids(tree: GraphTree) -> frozenset[str]:
    """The workstream(s) the tree's root itself owns, by `contains` alone."""
    if tree.root.kind is NodeKind.WORKSTREAM:
        return frozenset({tree.root.id})
    nodes = {node.id: node for node in tree.nodes}
    owned: set[str] = set()
    seen: set[str] = {tree.root.id}
    queue: list[GraphNode] = [tree.root]
    while queue:
        node = queue.pop()
        allowed = _OWNED_WORKSTREAM_DESCENT.get(node.kind, frozenset())
        for edge in tree.edges:
            if edge.kind is not EdgeKind.CONTAINS or edge.from_node_id != node.id:
                continue
            child = nodes.get(edge.to_node_id)
            if child is None or child.kind not in allowed or child.id in seen:
                continue
            seen.add(child.id)
            if child.kind is NodeKind.WORKSTREAM:
                owned.add(child.id)
            else:
                queue.append(child)
    return frozenset(owned)


def _owned_tasks(tree: GraphTree) -> tuple[GraphNode, ...]:
    """The tasks that belong to the root, not to some other project sharing a pod.

    `get_program_tree` is an untyped transitive closure over `contains` and
    `assigned_to`: it returns everything reachable from the root by either.
    That is exactly right for a developer's focus, whose tasks arrive over
    `developer --assigned_to--> task` -- but a pod is also a `contains` child
    of its project *and* carries `assigned_to` edges to every workstream it
    serves, so a pod working across two projects let one project's tree reach
    the other's tasks:

        project-insights > pod-data > ws-cart > CHK-201 > task-chk-201

    Scanning that tree flat for `NodeKind.TASK` counted those four foreign
    tasks, so Customer Insights reported 83% over six tasks while owning two --
    and it skewed *optimistic*, absorbing another project's green work. Shared
    pods are a legitimate structure the product advertises ("pods: teams
    working across the hierarchy"), so a task is now excluded only when it
    resolves to a workstream ancestor that is *not* one of the root's own --
    `task-chk-201`'s ancestor is `ws-cart`, owned by Checkout, not Insights. A
    task with no workstream ancestor at all keeps counting exactly as before:
    that is how a project with work assigned straight to its people, and no
    workstream underneath it yet, has always been read.
    """
    nodes = {node.id: node for node in tree.nodes}
    contains_parent: dict[str, GraphNode] = {}
    for edge in tree.edges:
        if edge.kind is EdgeKind.CONTAINS:
            parent = nodes.get(edge.from_node_id)
            child = nodes.get(edge.to_node_id)
            if parent is not None and child is not None:
                contains_parent[child.id] = parent

    def owning_workstream_id(task_id: str) -> str | None:
        current = task_id
        visited: set[str] = set()
        while True:
            parent = contains_parent.get(current)
            if parent is None or parent.id in visited:
                return None
            if parent.kind is NodeKind.WORKSTREAM:
                return parent.id
            visited.add(parent.id)
            current = parent.id

    owned_workstreams = _owned_workstream_ids(tree)
    tasks = [
        node
        for node in tree.nodes
        if node.kind is NodeKind.TASK
        and ((ws_id := owning_workstream_id(node.id)) is None or ws_id in owned_workstreams)
    ]
    return _sorted_nodes(tuple(tasks))


def _pod_members(tree: GraphTree) -> dict[str, GraphNode]:
    """The developers the pod at the root of ``tree`` contains."""
    nodes = {node.id: node for node in tree.nodes}
    members: dict[str, GraphNode] = {}
    for edge in tree.edges:
        member = nodes.get(edge.to_node_id)
        if (
            edge.kind is EdgeKind.CONTAINS
            and edge.from_node_id == tree.root.id
            and member is not None
            and member.kind is NodeKind.DEVELOPER
        ):
            members[member.id] = member
    return members


def _member_assignments(
    tree: GraphTree, members: dict[str, GraphNode]
) -> dict[str, list[GraphNode]]:
    """Each task assigned to a member, with the members it is assigned to."""
    nodes = {node.id: node for node in tree.nodes}
    assignees: dict[str, dict[str, GraphNode]] = {}
    for edge in tree.edges:
        task = nodes.get(edge.to_node_id)
        member = members.get(edge.from_node_id)
        if (
            edge.kind is EdgeKind.ASSIGNED_TO
            and member is not None
            and task is not None
            and task.kind is NodeKind.TASK
        ):
            assignees.setdefault(task.id, {})[member.id] = member
    return {task_id: list(by_id.values()) for task_id, by_id in assignees.items()}


def _pod_remit(
    tree: GraphTree,
    parents: dict[str, list[str]],
    kinds: dict[str, NodeKind],
) -> frozenset[str]:
    """The owners whose work a pod may list: itself, its workstreams, its projects.

    Its workstreams are the ones it serves (``assigned_to``, what
    ``assign_pod_workstream`` writes) or contains outright. Its projects are
    the ones that contain it.
    """
    pod_id = tree.root.id
    remit = {pod_id}
    remit.update(
        edge.to_node_id
        for edge in tree.edges
        if edge.from_node_id == pod_id
        and edge.kind in {EdgeKind.CONTAINS, EdgeKind.ASSIGNED_TO}
        and kinds.get(edge.to_node_id) is NodeKind.WORKSTREAM
    )
    remit.update(
        parent_id
        for parent_id in parents.get(pod_id, ())
        if kinds.get(parent_id) is NodeKind.PROJECT
    )
    return frozenset(remit)


# The node kinds that own what they contain. Walking up from a task, the first
# of these on each `contains` path is its owner; anything passed on the way (a
# work item, usually) is part of the task for blocker attribution.
_OWNER_KINDS = frozenset({NodeKind.WORKSTREAM, NodeKind.POD, NodeKind.PROJECT, NodeKind.PROGRAM})

# Worst first, for a list someone triages. Unlike `_rag_severity`, unknown sorts
# ahead of green: silence is not a clean bill of health.
_TRIAGE_RANK: dict[Rag, int] = {Rag.RED: 0, Rag.AMBER: 1, Rag.UNKNOWN: 2, Rag.GREEN: 3}


def _task_owners(
    task_id: str,
    parents: dict[str, list[str]],
    kinds: dict[str, NodeKind],
) -> tuple[frozenset[str], frozenset[str]]:
    """A task's nearest owners up `contains`, and the ids attributable to it.

    The second set is the task itself plus every non-owner node between it and
    its owners -- the work item it implements -- so a blocker raised against
    the work item marks the task.
    """
    owners: set[str] = set()
    attributable: set[str] = {task_id}
    queue = [task_id]
    while queue:
        current = queue.pop()
        for parent_id in parents.get(current, ()):
            if parent_id in owners or parent_id in attributable:
                continue
            if kinds.get(parent_id) in _OWNER_KINDS:
                owners.add(parent_id)
            else:
                attributable.add(parent_id)
                queue.append(parent_id)
    return frozenset(owners), frozenset(attributable)


def _since_for_as_of(as_of: date) -> datetime:
    return datetime.combine(as_of - timedelta(days=TASK_FACT_LOOKBACK_DAYS), time.min, tzinfo=UTC)


def _blocker_view(
    developer: GraphNode,
    status: DeveloperStatus | None,
    blocker: ResolvedBlocker,
    as_of: date,
) -> BlockerView:
    return BlockerView(
        id=blocker.blocker_id,
        blocker_id=blocker.blocker_id,
        description=blocker.description,
        age_days=_blocker_age_days(blocker, as_of),
        owner_id=developer.id,
        owner_name=developer.name,
        source=status.source if status is not None else StatusSource.UNKNOWN,
        status_as_of=status.as_of if status is not None else as_of,
        source_ref=blocker.work_item_ref or developer.ref,
        work_item_ref=blocker.work_item_ref,
        pod_ref=blocker.explicit_pod_ref,
        unattributed=blocker.unattributed,
        first_seen_on=blocker.first_seen_on,
    )


def _blocker_details(
    blockers: tuple[ResolvedBlocker, ...],
    node_names: dict[str, str],
    as_of: date,
) -> tuple[BlockerDetailView, ...]:
    return tuple(
        BlockerDetailView(
            blocker_id=blocker.blocker_id,
            description=blocker.description,
            work_item_id=blocker.work_item_ref.id if blocker.work_item_ref is not None else None,
            # The resolver knows the name because it fetched the node; the
            # node_names map only covers the developer's own tasks, so a blocker
            # attributed to a work item fell back to None.
            work_item_name=(
                blocker.work_item_name or node_names.get(blocker.work_item_ref.id)
                if blocker.work_item_ref is not None
                else None
            ),
            pod_id=blocker.explicit_pod_ref.id if blocker.explicit_pod_ref is not None else None,
            unattributed=blocker.unattributed,
            first_seen_on=blocker.first_seen_on,
            age_days=_blocker_age_days(blocker, as_of),
        )
        for blocker in blockers
    )


def _blocker_age_days(blocker: ResolvedBlocker, as_of: date) -> int:
    return max((as_of - blocker.first_seen_on).days, 0)


def _checkin_state(
    status: DeveloperStatus | None,
    as_of: date,
) -> Literal["confirmed", "partial", "stale", "missing"]:
    if status is None or status.source is StatusSource.UNKNOWN:
        return "missing"
    if status.source is StatusSource.CONFIRMED and status.as_of == as_of:
        return "confirmed"
    if status.source is StatusSource.PARTIAL and status.as_of == as_of:
        return "partial"
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
    if StatusSource.PARTIAL in sources:
        return StatusSource.PARTIAL
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
