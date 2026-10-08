from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Literal, Protocol

from core.application.attention import (
    AttentionDay,
    AttentionView,
    TeamGraph,
    attention_view,
    cell_reasons,
)
from core.application.blocker_resolution import BlockerResolutionService, ResolvedBlocker
from core.application.checkin_drift import CHECKIN_DRIFT_FACT_SOURCE
from core.application.rollup_service import (
    NO_WORK_REASON,
    DriftSignals,
    RollupService,
    is_outside_teams,
    people_outside_teams,
    task_rag,
)
from core.application.status_summaries import NO_REPLY_BLOCKER
from core.application.sync_services import ISSUE_FACT_SOURCE
from core.application.task_update_service import (
    TASK_UPDATE_FACT_SOURCE,
    TaskStatement,
    last_statement,
    own_eta,
    task_facts,
)
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphNode,
    GraphTree,
    JsonScalar,
    NodeKind,
    workstreams_in_use,
)
from core.domain.risk import DriftFinding, RiskFinding
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from core.domain.status import DeveloperStatus, StatusSource
from core.ports.repositories import (
    GraphRepository,
    IdentityLinkRepository,
    RollupRepository,
    StatusRepository,
    TimeSeriesRepository,
)

TASK_FACT_LOOKBACK_DAYS = 30
# Facts that say what a person stated about a task, not how it is going: a
# task's colour never comes from them (a statement never repaints a task, and
# an ETA fact never masks the tracker's colour).
_STATEMENT_FACT_SOURCES = frozenset({TASK_UPDATE_FACT_SOURCE, CHECKIN_DRIFT_FACT_SOURCE})


class PortfolioFindings(Protocol):
    """Where the attention read takes the open risk and drift findings (``RiskService``)."""

    async def portfolio_risks(self, tenant_id: str, as_of: date) -> list[RiskFinding]: ...

    async def portfolio_drift(self, tenant_id: str, as_of: date) -> list[DriftFinding]: ...


@dataclass(frozen=True, kw_only=True)
class ProviderNames:
    """How a reason names where an inferred status comes from, e.g. the tracker and VCS."""

    tracker: str = "the issue tracker"
    vcs: str = "Git"


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
    # The tracker's own status name ("In Review"), for a synced task only.
    tracker_status: str | None = None
    # The person's own latest ETA for the task, from a check-in or the console.
    my_eta: date | None = None
    my_eta_label: str | None = None
    # The person's latest statement on the task: a state or a note.
    last_update: TaskStatement | None = None
    # The person's open blockers on the task, as their blocker details place them.
    blocker_ids: tuple[str, ...] = ()
    # The person is the synced tracker issue's assignee, through their identity link.
    can_move_in_tracker: bool = False


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
    #: The issue tracker's own status name, e.g. "In Progress" (see `_tracker_status_name`).
    tracker_status: str | None = None


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
    #: The issue tracker's own status name, e.g. "In Progress" (see `_tracker_status_name`).
    tracker_status: str | None = None


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
    #: The node's name where the read knows it: every node of a tree it read,
    #: the nodes a reason cites, and always a person in no team (N5). None
    #: rather than a guess.
    name: str | None = None
    #: A few words for under the cell's colour ("3 of 4 unanswered today"),
    #: and every reason behind it, for a tooltip (``attention.cell_reasons``).
    reason: str | None = None
    reasons: tuple[str, ...] = ()


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
        drift_signals: DriftSignals | None = None,
        identity_link_repository: IdentityLinkRepository | None = None,
    ) -> None:
        """``drift_signals`` gives a rollup computed here, with nothing stored, its drift (N3).

        ``identity_link_repository`` maps a person to their tracker account, so
        a focus task can say whether they are its synced assignee.
        """
        self._graph_repository = graph_repository
        self._status_repository = status_repository
        self._rollup_repository = rollup_repository
        self._time_series_repository = time_series_repository
        self._identity_links = identity_link_repository
        self._blocker_resolution = BlockerResolutionService(graph_repository, status_repository)
        self._rollup_service = RollupService(
            status_repository, rollup_repository, self._blocker_resolution, drift_signals
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
            blockers = _listed_blockers(status)
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
        context = await self._focus_context(tenant_id, developer_id, tree, resolved_blockers)
        tasks = tuple(
            [
                await self._focus_task(node, as_of, context)
                for node in _sorted_nodes(tree.nodes)
                if node.kind is NodeKind.TASK
            ]
        )
        status_source = status.source if status else StatusSource.UNKNOWN
        blockers = _listed_blockers(status)
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

        nodes_by_id, parents = await self._tenant_contains(tenant_id, as_of)
        kinds = {node_id: node.kind for node_id, node in nodes_by_id.items()}
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
                    tracker_status=_tracker_status_name(node.metadata),
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
        nodes_by_id, parents = await self._tenant_contains(tenant_id, as_of)
        tasks = tuple(
            [
                await self._task_progress(node, as_of)
                for node in _owned_tasks(tree, nodes_by_id, parents)
            ],
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
        if tree.root.id not in workstreams_in_use(tree.nodes, tree.edges, as_of):
            return _empty_workstream_progress(tree.root, as_of)
        statuses = await self._node_statuses_for_tree(tree, as_of)
        root_status = statuses.get((tree.root.kind, tree.root.id))
        nodes_by_id, parents = await self._tenant_contains(tenant_id, as_of)
        tasks = tuple(
            [
                await self._task_progress(node, as_of)
                for node in _owned_tasks(tree, nodes_by_id, parents)
            ],
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
        """The tree under a node with each node's status, empty workstreams left out.

        Workstreams are optional: one holding no task or work item on the day
        is no part of the program to draw or explain, so it and its edges are
        dropped -- unless it is the root asked about.
        """
        tree = await self._graph_repository.get_program_tree(tenant_id, program_id, as_of)
        node_statuses = await self._node_statuses_for_tree(tree, as_of)
        in_use = workstreams_in_use(tree.nodes, tree.edges, as_of)
        hidden = {
            node.id
            for node in tree.nodes
            if node.kind is NodeKind.WORKSTREAM
            and node.id not in in_use
            and node.id != tree.root.id
        }
        nodes: list[TreeNodeView] = []
        for node in _sorted_nodes(tuple(node for node in tree.nodes if node.id not in hidden)):
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
                if edge.from_node_id not in hidden and edge.to_node_id not in hidden
            ),
        )

    async def portfolio_heatmap(
        self,
        tenant_id: str,
        as_of: date,
        program_root_id: str | None = None,
        *,
        today: date | None = None,
        names: ProviderNames | None = None,
    ) -> PortfolioHeatmapView:
        """Heat for one day, computed on the fly when nothing is stored yet.

        Reads never record. They used to: a missing rollup was computed *and
        persisted*, which meant any caller could write a row of derived history
        for whatever ``as_of`` it asked about -- one /ask question about a date
        the model invented left a rollup behind -- and made stored history
        depend on who happened to open which screen. The rollup schedule
        (``connector="rollup"``) owns that write now.

        People in no team (N5) are in no program tree: their own cells come
        from their stored rows, which say so (``is_outside_teams``), or are
        computed beside a computed tree. They sit on a "no pod" row and count
        in nothing else on the map.

        Each cell also carries a short reason and every reason behind it
        (``attention.cell_reasons``), counted against who is in each team on
        the day and who answered the day's check-in: the tenant's nodes and
        edges read once, never the program tree walked.

        A workstream holding no task or work item on the day has no cell
        (``TeamGraph.shows``): workstreams are optional, so an empty one is
        never a row of unknown tiles nor an unknown in the counts.
        """
        loaded = await self._heatmap_statuses(tenant_id, as_of, program_root_id)
        if loaded is None:
            return PortfolioHeatmapView(as_of=as_of, rows=(), columns=(), cells=())
        statuses, tree = loaded
        day = await self._attention_day(
            tenant_id, statuses, as_of, today or datetime.now(tz=UTC).date(), names
        )
        # An empty workstream is optional, not unreported: it gets no cell.
        shown = [status for status in statuses if day.graph.shows(status.entity_ref)]
        cells = await self._heatmap_cells(tenant_id, shown, tree, day)
        rows = tuple(dict.fromkeys(cell.row for cell in cells))
        columns = tuple(dict.fromkeys(cell.column for cell in cells))
        return PortfolioHeatmapView(as_of=as_of, rows=rows, columns=columns, cells=cells)

    async def portfolio_attention(
        self,
        tenant_id: str,
        as_of: date,
        program_root_id: str | None,
        findings: PortfolioFindings | None,
        *,
        today: date,
        zone: tzinfo = UTC,
        names: ProviderNames | None = None,
        clock: bool = True,
    ) -> AttentionView:
        """What a director should know about the program on ``as_of``, in one read.

        The headline's sentence and its second line, the day's check-in counts
        for the people in the teams, and up to five signals worth acting on:
        from the same statuses the heat map shows, the open risk and drift
        findings (``findings``; none read without it), and how long each open
        blocker has stood.
        """
        program_id = program_root_id or await self._first_program_id(tenant_id, as_of)
        loaded = await self._heatmap_statuses(tenant_id, as_of, program_id)
        statuses = loaded[0] if loaded is not None else []
        day = await self._attention_day(tenant_id, statuses, as_of, today, names)
        risks = await findings.portfolio_risks(tenant_id, as_of) if findings else []
        drift = await findings.portfolio_drift(tenant_id, as_of) if findings else []
        people = sorted(node_id for (kind, node_id) in day.statuses if kind is NodeKind.DEVELOPER)
        ages = {
            blocker.blocker_id: max((as_of - blocker.first_seen_on).days, 0)
            for blocker in await self._status_repository.open_blockers_for_developers(
                tenant_id, people, as_of
            )
        }
        return attention_view(
            day, program_id, risks=risks, drift=drift, blocker_ages=ages, zone=zone, clock=clock
        )

    async def _heatmap_statuses(
        self, tenant_id: str, as_of: date, program_root_id: str | None
    ) -> tuple[list[NodeStatus], GraphTree | None] | None:
        """The day's statuses the heat map shows, and the tree when one was read.

        None when there is no program to show. Stored rows are read as they
        are; a day with none is computed (never recorded).
        """
        statuses = await self._rollup_repository.list_node_statuses(tenant_id, as_of)
        tree: GraphTree | None = None
        if program_root_id is not None:
            if not statuses:
                try:
                    tree = await self._graph_repository.get_program_tree(
                        tenant_id, program_root_id, as_of
                    )
                except GraphNotFound:
                    return None
                statuses = list(await self._rollup_service.compute(tree, as_of))
                statuses += await self._outside_teams(tenant_id, as_of)
            return statuses, tree
        resolved_root_id = await self._first_program_id(tenant_id, as_of)
        if resolved_root_id is None:
            return None
        try:
            tree = await self._graph_repository.get_program_tree(tenant_id, resolved_root_id, as_of)
        except GraphNotFound:
            return None
        tree_ids = {node.id for node in tree.nodes}
        in_tree = [status for status in statuses if status.entity_ref.id in tree_ids]
        if in_tree:
            statuses = in_tree + [
                status
                for status in statuses
                if status.entity_ref.id not in tree_ids and is_outside_teams(status)
            ]
        else:
            statuses = list(await self._rollup_service.compute(tree, as_of))
            statuses += await self._outside_teams(tenant_id, as_of)
        return statuses, tree

    async def _attention_day(
        self,
        tenant_id: str,
        statuses: Sequence[NodeStatus],
        as_of: date,
        today: date,
        names: ProviderNames | None,
    ) -> AttentionDay:
        """The day as the reasons read it: statuses, teams on the day, its check-ins."""
        provider = names or ProviderNames()
        graph = TeamGraph.from_graph(
            await self._graph_repository.list_nodes(tenant_id, as_of=as_of),
            await self._graph_repository.list_edges(tenant_id),
            as_of,
        )
        return AttentionDay.build(
            as_of=as_of,
            today=today,
            statuses=statuses,
            graph=graph,
            checkins=await self._status_repository.checkins_on_day(tenant_id, as_of),
            tracker=provider.tracker,
            vcs=provider.vcs,
        )

    async def _heatmap_cells(
        self,
        tenant_id: str,
        statuses: Sequence[NodeStatus],
        tree: GraphTree | None,
        day: AttentionDay,
    ) -> tuple[HeatmapCellView, ...]:
        """One cell per status, each amber or red one naming what drives it.

        A reason prints names and issue keys, never node ids. Only the nodes an
        amber or red cell's reason names are looked up -- the people behind its
        blockers, drift and updates, and the work items and tasks those cite --
        from the tree when one was read, else one by one; a node not found is
        left unnamed.
        """
        context = _ReasonContext.from_statuses(statuses)
        wanted = {
            node_id
            for status in statuses
            if _needs_drivers(status)
            for node_id in context.cited_node_ids(status)
        }
        # A person in no team is in no tree; their cell is named all the same.
        outside = {status.entity_ref.id for status in statuses if is_outside_teams(status)}
        nodes = {node.id: node for node in tree.nodes} if tree is not None else {}
        for node_id in sorted((wanted | outside) - nodes.keys()):
            node = await self._graph_repository.get_node(tenant_id, node_id, as_of=day.as_of)
            if node is not None:
                nodes[node_id] = node
        context = context.with_labels(
            {node_id: _reason_label(nodes[node_id]) for node_id in wanted if node_id in nodes}
        )
        cells: list[HeatmapCellView] = []
        for status in statuses:
            no_pod = is_outside_teams(status)
            reasons = cell_reasons(
                day,
                _without_no_pod(status) if no_pod else status,
                outside_teams=no_pod,
            )
            cells.append(
                replace(
                    _heatmap_cell(status, context),
                    name=node.name if (node := nodes.get(status.entity_ref.id)) else None,
                    reason=reasons.reason,
                    reasons=reasons.reasons,
                )
            )
        return tuple(cells)

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

    async def _outside_teams(self, tenant_id: str, as_of: date) -> list[NodeStatus]:
        """Computed own cells for the people in no team (N5), as the rollup records them."""
        people = await people_outside_teams(self._graph_repository, tenant_id, as_of)
        return list(await self._rollup_service.compute_outside_teams(people, as_of))

    async def _first_program_id(self, tenant_id: str, as_of: date) -> str | None:
        programs = await self._graph_repository.list_nodes(tenant_id, NodeKind.PROGRAM, as_of=as_of)
        return programs[0].id if programs else None

    async def _tenant_contains(
        self, tenant_id: str, as_of: date
    ) -> tuple[dict[str, GraphNode], dict[str, list[str]]]:
        """The tenant's nodes by id, and each node's ``contains`` parents on ``as_of``.

        Ownership is read from these, not from a tree: a tree holds only the
        edges below its root, so a container elsewhere is out of its sight.
        """
        nodes = {
            node.id: node
            for node in await self._graph_repository.list_nodes(tenant_id, as_of=as_of)
        }
        parents: dict[str, list[str]] = {}
        for edge in await self._graph_repository.list_edges(tenant_id, kind=EdgeKind.CONTAINS):
            if edge.is_active_on(as_of):
                parents.setdefault(edge.to_node_id, []).append(edge.from_node_id)
        return nodes, parents

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

    async def focus_task(
        self, tenant_id: str, developer_id: str, task_id: str, as_of: date
    ) -> FocusTaskView | None:
        """One task of the person's focus, as ``focus`` lists it; None when it is not theirs."""
        try:
            tree = await self._graph_repository.get_program_tree(tenant_id, developer_id, as_of)
        except GraphNotFound:
            return None
        node = next(
            (item for item in tree.nodes if item.kind is NodeKind.TASK and item.id == task_id),
            None,
        )
        if node is None:
            return None
        blockers = await self._blocker_resolution.open_blockers_for_developer(
            tenant_id, developer_id, as_of
        )
        context = await self._focus_context(tenant_id, developer_id, tree, blockers)
        return await self._focus_task(node, as_of, context)

    async def _focus_context(
        self,
        tenant_id: str,
        developer_id: str,
        tree: GraphTree,
        blockers: Sequence[ResolvedBlocker],
    ) -> _FocusContext:
        accounts = {developer_id}
        if self._identity_links is not None:
            link = await self._identity_links.get_identity_link(tenant_id, developer_id)
            if link is not None and link.jira_account_id:
                accounts.add(link.jira_account_id)
        blocker_ids: dict[str, list[str]] = {}
        for blocker in blockers:
            if blocker.work_item_ref is not None:
                blocker_ids.setdefault(blocker.work_item_ref.id, []).append(blocker.blocker_id)
        return _FocusContext(
            developer_id=developer_id,
            accounts=frozenset(accounts),
            blocker_ids={key: tuple(ids) for key, ids in blocker_ids.items()},
            assigned={
                edge.to_node_id
                for edge in tree.edges
                if edge.kind is EdgeKind.ASSIGNED_TO and edge.from_node_id == developer_id
            },
        )

    async def _focus_task(
        self, node: GraphNode, as_of: date, context: _FocusContext
    ) -> FocusTaskView:
        facts = await task_facts(self._time_series_repository, node, as_of)
        status = _task_status_from_facts(node, facts, as_of)
        eta = own_eta(facts, context.developer_id, as_of)
        return FocusTaskView(
            id=node.id,
            name=node.name,
            rag=status.rag,
            source=status.source,
            confidence=status.confidence,
            deadline=_deadline(node),
            tracker_status=_tracker_status_name(node.metadata),
            my_eta=eta.day if eta is not None else None,
            my_eta_label=eta.label if eta is not None else None,
            last_update=last_statement(facts, context.developer_id, as_of),
            blocker_ids=context.blocker_ids.get(node.id, ()),
            can_move_in_tracker=_owns_synced_issue(node, facts, context, as_of),
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
            tracker_status=_tracker_status_name(node.metadata),
        )

    async def _task_status(self, node: GraphNode, as_of: date) -> TaskStatusView:
        facts = await self._time_series_repository.list_facts(
            node.tenant_id,
            node.ref,
            _since_for_as_of(as_of),
        )
        return _task_status_from_facts(node, facts, as_of)


@dataclass(frozen=True, kw_only=True)
class _FocusContext:
    """What every task row of one person's focus reads alike."""

    developer_id: str
    # The person's ids in the tracker: their member id and their identity link's.
    accounts: frozenset[str]
    # Open blocker ids per task id, as the blocker details place them.
    blocker_ids: Mapping[str, tuple[str, ...]]
    # The tasks the person's tree reaches through their own assignment edge.
    assigned: set[str]


def _task_status_from_facts(
    node: GraphNode, facts: Sequence[FactEvent], as_of: date
) -> TaskStatusView:
    """A task's colour from its latest tracker or rollup fact, else its synced metadata.

    Statements are left out: a ``task_update`` (what the person said in chat
    or the console) never repaints the task, and an ``eta_stated`` or other
    ``checkin_drift`` fact, which carries no colour, no longer hides the
    tracker's.
    """
    since = _since_for_as_of(as_of)
    coloured = [
        fact
        for fact in facts
        if fact.source not in _STATEMENT_FACT_SOURCES and fact.observed_at >= since
    ]
    fact = max(coloured, key=lambda item: item.observed_at) if coloured else None
    return TaskStatusView(
        rag=_rag_from_fact_or_metadata(fact, node),
        source=_source_from_fact(fact),
        confidence=_confidence_from_fact(fact),
        source_ref=node.ref,
    )


def _owns_synced_issue(
    node: GraphNode, facts: Sequence[FactEvent], context: _FocusContext, as_of: date
) -> bool:
    """Whether the person is the synced tracker issue's assignee.

    Only a task the issue sync wrote (it carries the tracker ``state``) is an
    issue to move. The assignee is the latest synced issue fact's
    ``assignee_id``, matched against the person's tracker ids, the way the
    write-back's ownership gate matches the live issue's; with no issue fact
    in the window, the person's own assignment edge stands in. A read only:
    the write itself checks the live issue again.
    """
    state = node.metadata.get("state")
    if not isinstance(state, str) or not state.strip():
        return False
    synced = [
        fact
        for fact in facts
        if fact.source == ISSUE_FACT_SOURCE
        and "assignee_id" in fact.payload
        and fact.observed_at.date() <= as_of
    ]
    if not synced:
        return node.id in context.assigned
    assignee = max(synced, key=lambda item: item.observed_at).payload.get("assignee_id")
    return isinstance(assignee, str) and assignee in context.accounts


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


async def owned_project_tasks(
    graph_repository: GraphRepository, tenant_id: str, project_ids: Sequence[str], as_of: date
) -> dict[str, tuple[GraphNode, ...]]:
    """The tasks each project owns on ``as_of``, by the same rule its progress uses.

    Read for many projects at once, so the tenant's nodes and ``contains``
    edges are listed once. A project id that is not a project is left out.
    """
    nodes = {node.id: node for node in await graph_repository.list_nodes(tenant_id, as_of=as_of)}
    parents: dict[str, list[str]] = {}
    for edge in await graph_repository.list_edges(tenant_id, kind=EdgeKind.CONTAINS):
        if edge.is_active_on(as_of):
            parents.setdefault(edge.to_node_id, []).append(edge.from_node_id)
    owned: dict[str, tuple[GraphNode, ...]] = {}
    for project_id in project_ids:
        project = nodes.get(project_id)
        if project is None or project.kind is not NodeKind.PROJECT:
            continue
        tree = await graph_repository.get_program_tree(tenant_id, project_id, as_of)
        owned[project_id] = _owned_tasks(tree, nodes, parents)
    return owned


def _owned_tasks(
    tree: GraphTree,
    nodes: Mapping[str, GraphNode],
    parents: Mapping[str, Sequence[str]],
) -> tuple[GraphNode, ...]:
    """The tasks that belong to the root: the ones it reaches through ``contains``.

    `get_program_tree` is an untyped transitive closure over `contains` and
    `assigned_to`: it returns everything reachable from the root by either.
    That is right for a developer's focus, whose tasks arrive over
    `developer --assigned_to--> task`, but a project reaches far more than it
    owns. Its pods' members bring every task assigned to them, in any project,
    and a pod serving another project's workstream brings that workstream's
    tasks:

        project-insights > pod-data > ws-cart > CHK-201 > task-chk-201

    Counting them inflated a project's task counts and percent complete, and
    skewed it optimistic whenever the borrowed work was green.

    An assignment says whose work a task is, not where it belongs. A task
    counts when the root is one of its `contains` ancestors -- directly, or
    through a sprint, pod, workstream or work item beneath it. Ancestors come
    from the tenant's edges (``parents``), because the tree holds only the
    edges below the root and another project's claim is out of its sight. Two
    cases need more than that:

    - A task nothing owns -- assigned straight to a member, no container in
      between -- is unclaimed rather than foreign and keeps counting, as it
      does in a pod's task list.
    - A pod two projects share holds both projects' tickets, since one Jira
      filter can span both. Where a ticket's own Jira project is one of the
      projects above it, only that project counts it; where it names none of
      them, each one does.
    """
    root = tree.root
    owned: list[GraphNode] = []
    for task in (node for node in tree.nodes if node.kind is NodeKind.TASK):
        ancestors = _contains_ancestors(task.id, parents)
        if root.id in ancestors:
            if root.kind is not NodeKind.PROJECT or not _claimed_by_another_project(
                task, root, ancestors, nodes
            ):
                owned.append(task)
        elif not any(
            (ancestor_node := nodes.get(ancestor)) is not None
            and ancestor_node.kind in _OWNER_KINDS
            for ancestor in ancestors
        ):
            owned.append(task)
    return _sorted_nodes(tuple(owned))


def _contains_ancestors(node_id: str, parents: Mapping[str, Sequence[str]]) -> frozenset[str]:
    """Every node above ``node_id`` along ``contains`` edges."""
    seen: set[str] = set()
    queue = list(parents.get(node_id, ()))
    while queue:
        current = queue.pop()
        if current not in seen:
            seen.add(current)
            queue.extend(parents.get(current, ()))
    return frozenset(seen)


def _claimed_by_another_project(
    task: GraphNode,
    project: GraphNode,
    ancestors: frozenset[str],
    nodes: Mapping[str, GraphNode],
) -> bool:
    """Whether the task's own Jira project is a project above it other than ``project``."""
    keys = _task_jira_keys(task)
    claimants = {
        ancestor
        for ancestor in ancestors
        if (node := nodes.get(ancestor)) is not None
        and node.kind is NodeKind.PROJECT
        and _project_jira_key(node) in keys
    }
    return bool(claimants) and project.id not in claimants


def _task_jira_keys(task: GraphNode) -> frozenset[str]:
    """The Jira project a ticket is in: its ``project_key`` and its issue key's prefix."""
    keys: set[str] = set()
    project_key = task.metadata.get("project_key")
    if isinstance(project_key, str) and project_key.strip():
        keys.add(project_key.strip().upper())
    issue_key = task.metadata.get("key")
    if isinstance(issue_key, str) and "-" in issue_key:
        keys.add(issue_key.rsplit("-", 1)[0].strip().upper())
    return frozenset(keys)


def _project_jira_key(project: GraphNode) -> str | None:
    for field_name in ("jira_project_key", "key"):
        value = project.metadata.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


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


def _listed_blockers(status: DeveloperStatus | None) -> tuple[str, ...]:
    """The blockers a status lists, without the placeholder a day nobody answered carries.

    A non-response status holds ``NO_REPLY_BLOCKER`` so it is never empty. The
    rollups and the blocker resolver drop it -- silence is the person's status,
    never a blocker they have -- and so do the blocker details, which left the
    focus list a "blocker" with nothing behind it.
    """
    if status is None:
        return ()
    return tuple(
        blocker for blocker in status.blockers if blocker.strip().lower() != NO_REPLY_BLOCKER
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


def _empty_workstream_progress(node: GraphNode, as_of: date) -> WorkstreamProgressView:
    """A workstream with no task or work item on the day: no status, and why.

    Workstreams are optional, so the views leave an empty one out; a direct
    link still opens it, and says plainly that no work is in it yet rather
    than "no child task status data" and 0% complete. Neither its target date
    nor a status set on it colours it: there is no work for them to be about.
    """
    return WorkstreamProgressView(
        workstream_id=node.id,
        workstream_name=node.name,
        as_of=as_of,
        rag=Rag.UNKNOWN,
        source=StatusSource.UNKNOWN,
        confidence=None,
        percent_complete=0.0,
        total_tasks=0,
        green_tasks=0,
        amber_tasks=0,
        red_tasks=0,
        unknown_tasks=0,
        factors=(
            RollupFactor(
                description=NO_WORK_REASON,
                contributes=Rag.UNKNOWN,
                source_ref=node.ref,
                kind=FactorKind.AGGREGATE,
            ),
        ),
        source_names={},
        tasks=(),
    )


def _workstream_task_rag(
    node: GraphNode,
    tasks: tuple[TaskProgressView, ...],
    as_of: date,
) -> Rag:
    """The workstream's colour from its own status, its target date and its tasks.

    Tasks are read from their latest facts, so this can be worse than the
    stored rollup. As in the rollup, a task only adds risk, when it is red or
    amber: one to do or in progress has no colour, and a done one is finished
    work, not a report on the rest, so neither turns the workstream amber or
    green. Green comes from the rollup or from the workstream's own status.
    """
    metadata_rag = _rag_from_value(node.metadata.get("status"))
    if metadata_rag is Rag.RED or any(task.rag is Rag.RED for task in tasks):
        return Rag.RED
    if metadata_rag is Rag.AMBER or any(task.rag is Rag.AMBER for task in tasks):
        return Rag.AMBER
    if _approaching_target_date(node, as_of):
        return Rag.AMBER
    if metadata_rag is Rag.GREEN:
        return Rag.GREEN
    return Rag.UNKNOWN


def _workstream_task_factors(
    node: GraphNode,
    tasks: tuple[TaskProgressView, ...],
    rag: Rag,
    as_of: date,
) -> tuple[RollupFactor, ...]:
    factors = [
        RollupFactor(
            description=f"Task {task.name} is {task.rag.value}.",
            contributes=task.rag,
            source_ref=EntityRef(tenant_id=node.tenant_id, kind=NodeKind.TASK, id=task.id),
        )
        for task in tasks
        if task.rag in {Rag.RED, Rag.AMBER}
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
    if rag is Rag.UNKNOWN:
        return (
            RollupFactor(
                description="No child task status data is available.",
                contributes=Rag.UNKNOWN,
                source_ref=node.ref,
            ),
        )
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


# A synced task's normalized workflow state, as a reader would name it, for a
# tracker that gave no status name of its own.
_TRACKER_STATE_NAMES: dict[str, str] = {
    "todo": "To Do",
    "in_progress": "In Progress",
    "done": "Done",
    "blocked": "Blocked",
}


def _tracker_status_name(values: Mapping[str, JsonScalar]) -> str | None:
    """The issue tracker's own status for a task, e.g. "In Progress", or None.

    A task's RAG says how the work is going, not where it is: an In Progress
    ticket has no colour, so a manager moving one in Jira changed nothing a
    task row showed. The issue sync copies the tracker's status name to
    ``status`` beside its normalized ``state``; only a task carrying ``state``
    came from a tracker. Without it, ``status`` is a RAG word a seed or a
    person set -- already the chip's colour, not a tracker status. A synced
    task whose tracker gave no status name shows its normalized state.
    """
    state = values.get("state")
    if not isinstance(state, str) or not state.strip():
        return None
    status = values.get("status")
    if isinstance(status, str) and status.strip():
        return status.strip()
    return _TRACKER_STATE_NAMES.get(state.strip().lower())


def _rag_from_fact_or_metadata(fact: FactEvent | None, node: GraphNode) -> Rag:
    if fact is not None and (rag := task_rag(fact.payload)) is not None:
        return rag
    return task_rag(node.metadata) or Rag.UNKNOWN


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


# The heat-map row a person in no team sits on (N5), apart from every team's.
NO_POD_ROW = "no pod"


def _without_no_pod(status: NodeStatus) -> NodeStatus:
    """A person in no team's own status, without the line that says so (N5)."""
    return replace(
        status, factors=tuple(f for f in status.factors if f.kind is not FactorKind.NO_POD)
    )


def _heatmap_cell(status: NodeStatus, context: _ReasonContext) -> HeatmapCellView:
    if is_outside_teams(status):
        # Their check-in, read as anyone's, on a row of its own that says why
        # no team's colour carries it.
        own = replace(
            status,
            factors=tuple(f for f in status.factors if f.kind is not FactorKind.NO_POD),
        )
        cell = _heatmap_cell(own, context)
        return replace(cell, row=NO_POD_ROW, why=f"No pod, outside team colours: {cell.why}")
    factor = status.factors[0] if status.factors else None
    first = factor.description if factor else "No rollup factors are available."
    drivers = _drivers_line(status, context) if _needs_drivers(status) else None
    return HeatmapCellView(
        row=status.entity_ref.kind.value,
        column=status.entity_ref.id,
        entity_ref=status.entity_ref,
        rag=status.rag,
        source=status.source,
        why=drivers or first,
        source_ref=factor.source_ref if factor else status.entity_ref,
    )


# What a heat-map reason may call a task or work item by: its tracker key.
_ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9]+-\d+")


@dataclass(frozen=True)
class _ReasonContext:
    """What a cell's reason reads beyond its own factors.

    ``statuses`` gives each person's status source, so a status factor is
    counted by what the person's update was -- partial, inferred, stale --
    never by its wording. ``owners`` maps each open blocker to the person it
    is from, read off the people's own statuses, where every blocker starts.
    ``labels`` holds the names and issue keys a reason may print.
    """

    statuses: Mapping[tuple[NodeKind, str], NodeStatus]
    owners: Mapping[str, str]
    labels: Mapping[str, str]

    @classmethod
    def from_statuses(cls, statuses: Iterable[NodeStatus]) -> _ReasonContext:
        by_ref = {(status.entity_ref.kind, status.entity_ref.id): status for status in statuses}
        owners: dict[str, str] = {}
        for (kind, node_id), status in by_ref.items():
            if kind is not NodeKind.DEVELOPER:
                continue
            for factor in status.factors:
                if factor.kind is FactorKind.BLOCKER:
                    owners.setdefault(_blocker_key(factor), node_id)
        return cls(statuses=by_ref, owners=owners, labels={})

    def with_labels(self, labels: Mapping[str, str]) -> _ReasonContext:
        return _ReasonContext(statuses=self.statuses, owners=self.owners, labels=labels)

    def owner_of(self, factor: RollupFactor) -> str | None:
        owner = self.owners.get(_blocker_key(factor))
        if owner is None and factor.source_ref.kind is NodeKind.DEVELOPER:
            owner = factor.source_ref.id
        return owner

    def cited_node_ids(self, status: NodeStatus) -> set[str]:
        """The nodes this cell's reason may name."""
        ids: set[str] = set()
        for factor in status.factors:
            if factor.kind is FactorKind.BLOCKER:
                if (owner := self.owner_of(factor)) is not None:
                    ids.add(owner)
                if factor.work_item_ref is not None:
                    ids.add(factor.work_item_ref.id)
            elif factor.kind is FactorKind.DRIFT:
                # A drift factor cites its owner, and the issue it is on.
                ids.add(factor.source_ref.id)
                if factor.work_item_ref is not None:
                    ids.add(factor.work_item_ref.id)
            elif factor.kind is FactorKind.TASK:
                ids.add(factor.source_ref.id)
            elif _is_person_update(factor):
                # A person whose update is partial, inferred, stale or missing (N44).
                ids.add(factor.source_ref.id)
        return ids

    def label(self, node_id: str) -> str | None:
        """A node's name or issue key; None rather than its raw id."""
        label = self.labels.get(node_id)
        if label is None and _ISSUE_KEY.fullmatch(node_id):
            return node_id
        return label

    def person_source(self, developer_id: str) -> StatusSource | None:
        status = self.statuses.get((NodeKind.DEVELOPER, developer_id))
        return status.source if status is not None else None


def _reason_label(node: GraphNode) -> str:
    """A person by name; a task or work item by its issue key where it has one."""
    if node.kind is not NodeKind.DEVELOPER:
        key = node.metadata.get("key")
        if isinstance(key, str) and key.strip():
            return key.strip()
        if _ISSUE_KEY.fullmatch(node.id):
            return node.id
    return node.name


def _blocker_key(factor: RollupFactor) -> str:
    """A blocker's identity across the cells it reaches, as the rollup counts it."""
    return factor.blocker_id or factor.description


def _needs_drivers(status: NodeStatus) -> bool:
    """Whether a cell's reason names its drivers rather than its first factor.

    Every amber or red parent does: its first factor is one child's reason,
    and "Status is partial" says nothing of the blocker beside it. A person
    with one reason keeps it -- that reason is the whole story -- but one with
    several, such as two blockers that together make them red, is summed up.
    """
    if status.rag not in {Rag.AMBER, Rag.RED}:
        return False
    return status.entity_ref.kind is not NodeKind.DEVELOPER or len(status.factors) > 1


# How people's updates read in a reason, in the order a reason lists them.
# None is a person whose own status was not read: their update needs confirming.
_STATUS_REASON_TEXT: dict[StatusSource | None, tuple[str, str]] = {
    StatusSource.PARTIAL: ("partial update", "partial updates"),
    StatusSource.INFERRED: ("inferred status", "inferred statuses"),
    StatusSource.STALE: ("stale update", "stale updates"),
    StatusSource.UNKNOWN: ("missing update", "missing updates"),
    None: ("update needing confirmation", "updates needing confirmation"),
}


def _drivers_line(status: NodeStatus, context: _ReasonContext) -> str | None:
    """One short line naming what makes a cell amber or red.

    E.g. "1 open blocker (CHK-8, Zoe Almeida); 2 partial updates (Omar
    Haddad; Sofia Bergmann)." -- the drivers the Exec Today hero line names
    for the whole program, from the same rollup factors: each open blocker
    once, with its work item's key and the person it is from; blocked tasks;
    signals that disagree with an owner's issue (N3); how many people's
    updates are partial, inferred, stale or missing, and whose (N44); tasks
    needing attention; approaching target dates. None when no factor says,
    and the cell keeps its first one.
    """
    factors = [factor for factor in status.factors if factor.contributes is not Rag.GREEN]
    parts = [
        part
        for part in (
            _blockers_part(factors, status.entity_ref, context),
            _tasks_part(factors, blocked=True, context=context),
            _drift_part(factors, status.entity_ref, context),
            *_people_parts(factors, status.entity_ref, context),
            _tasks_part(factors, blocked=False, context=context),
            _target_dates_part(factors),
        )
        if part
    ]
    if not parts:
        return None
    line = "; ".join(parts)
    return f"{line[:1].upper()}{line[1:]}."


def _is_person_update(factor: RollupFactor) -> bool:
    """Whether a factor is a person's own update that needs something: a people part."""
    return (
        factor.kind is FactorKind.STATUS
        and factor.source_ref.kind is NodeKind.DEVELOPER
        and factor.contributes is not Rag.GREEN
    )


def _people_parts(
    factors: Iterable[RollupFactor], cell: EntityRef, context: _ReasonContext
) -> list[str]:
    """How many people's updates are partial, inferred, stale or missing, and whose.

    E.g. "1 partial update (Omar Haddad)" (N44). The people are named as an
    open blocker's are: two at most, then "; N more"; never on their own
    cell; and a person whose name is not known is counted but left unnamed,
    never shown by id.
    """
    people: dict[StatusSource | None, dict[str, None]] = {}
    for factor in factors:
        if not _is_person_update(factor):
            continue
        source = (
            StatusSource.UNKNOWN
            if factor.contributes is Rag.UNKNOWN
            else context.person_source(factor.source_ref.id)
        )
        bucket = source if source in _STATUS_REASON_TEXT else None
        people.setdefault(bucket, {})[factor.source_ref.id] = None
    parts: list[str] = []
    for source, nouns in _STATUS_REASON_TEXT.items():
        if source not in people:
            continue
        names = [
            name
            for person_id in people[source]
            if not (cell.kind is NodeKind.DEVELOPER and person_id == cell.id)
            and (name := context.label(person_id)) is not None
        ]
        parts.append(_named(_count(len(people[source]), nouns), names))
    return parts


def _tasks_part(
    factors: Iterable[RollupFactor], *, blocked: bool, context: _ReasonContext
) -> str | None:
    """Blocked tasks (red), or tasks needing attention (amber), with up to two named."""
    tasks = [
        factor
        for factor in factors
        if factor.kind is FactorKind.TASK and (factor.contributes is Rag.RED) is blocked
    ]
    if not tasks:
        return None
    nouns = ("blocked task", "blocked tasks") if blocked else _ATTENTION_TASK_NOUNS
    labels = list(
        dict.fromkeys(label for factor in tasks if (label := context.label(factor.source_ref.id)))
    )
    counted = _count(len({factor.source_ref.id for factor in tasks}), nouns)
    return f"{counted} ({_join_and(labels)})" if labels else counted


_ATTENTION_TASK_NOUNS = ("task needing attention", "tasks needing attention")


def _target_dates_part(factors: Iterable[RollupFactor]) -> str | None:
    target_dates = [factor for factor in factors if factor.kind is FactorKind.TARGET_DATE]
    if len(target_dates) == 1:
        return _clause(target_dates[0].description)
    return f"{len(target_dates)} target dates approaching" if target_dates else None


def _blockers_part(
    factors: Iterable[RollupFactor], cell: EntityRef, context: _ReasonContext
) -> str | None:
    """E.g. "2 open blockers (CHK-8, Zoe Almeida; CHK-17, Omar Haddad)".

    Each blocker counts once, however many children carry it, as the rollup
    counts them. They are grouped by the person they are from, a person's own
    cell does not name them, and two groups are named at most.
    """
    blockers: dict[str, RollupFactor] = {}
    for factor in factors:
        if factor.kind is FactorKind.BLOCKER:
            blockers.setdefault(_blocker_key(factor), factor)
    if not blockers:
        return None
    groups: dict[str | None, list[str]] = {}
    for factor in blockers.values():
        owner_id = context.owner_of(factor)
        own_cell = cell.kind is NodeKind.DEVELOPER and owner_id == cell.id
        owner = context.label(owner_id) if owner_id is not None and not own_cell else None
        items = groups.setdefault(owner, [])
        item = context.label(factor.work_item_ref.id) if factor.work_item_ref else None
        if item is not None and item not in items:
            items.append(item)
    labels = [
        label
        for owner, items in groups.items()
        if (label := ", ".join(part for part in (_join_and(items), owner) if part))
    ]
    return _named(_count(len(blockers), ("open blocker", "open blockers")), labels)


def _drift_part(
    factors: Iterable[RollupFactor], cell: EntityRef, context: _ReasonContext
) -> str | None:
    """Signals that disagree with an owner's issues, as the rollup carries them (N3).

    One signal reads as itself, with its owner on anyone else's cell: "signals
    disagree: CHK-17 merged but still open in Jira (Omar Haddad)". Several
    are counted by issue and grouped by owner, as blockers are: "signals
    disagree on 3 issues (CHK-6 and IDP-3, Noah Weber; CHK-14, Sofia
    Bergmann)".
    """
    drift: dict[tuple[str, str], RollupFactor] = {}
    for factor in factors:
        if factor.kind is FactorKind.DRIFT:
            drift.setdefault((factor.source_ref.id, factor.description), factor)
    if not drift:
        return None

    def owner(factor: RollupFactor) -> str | None:
        own_cell = cell.kind is NodeKind.DEVELOPER and factor.source_ref.id == cell.id
        return None if own_cell else context.label(factor.source_ref.id)

    if len(drift) == 1:
        (factor,) = drift.values()
        name = owner(factor)
        return f"{_clause(factor.description)} ({name})" if name else _clause(factor.description)
    groups: dict[str | None, list[str]] = {}
    issues: set[str] = set()
    for factor in drift.values():
        items = groups.setdefault(owner(factor), [])
        if factor.work_item_ref is not None:
            issues.add(factor.work_item_ref.id)
            item = context.label(factor.work_item_ref.id)
            if item is not None and item not in items:
                items.append(item)
    labels = [
        label
        for name, items in groups.items()
        if (label := ", ".join(part for part in (_join_and(items), name) if part))
    ]
    counted = f"signals disagree on {_count(len(issues) or len(drift), ('issue', 'issues'))}"
    return _named(counted, labels)


def _count(n: int, nouns: tuple[str, str]) -> str:
    return f"{n} {nouns[0] if n == 1 else nouns[1]}"


def _named(counted: str, labels: Sequence[str], limit: int = 2) -> str:
    """A count with whom or what it is about, e.g. "2 partial updates (Omar Haddad; Ira Novak)".

    The one way a reason names people -- blockers, drift and updates alike:
    labels apart by "; ", ``limit`` named at most, then "; N more". With no
    label known, the count stands alone.
    """
    if not labels:
        return counted
    more = len(labels) - limit
    return f"{counted} ({'; '.join(labels[:limit])}{f'; {more} more' if more > 0 else ''})"


def _join_and(items: Sequence[str], limit: int = 2) -> str:
    if len(items) > limit:
        return f"{', '.join(items[:limit])} and {len(items) - limit} more"
    return " and ".join(items)


def _clause(description: str) -> str:
    """A factor's own sentence as one part of a line: no capital, no full stop."""
    text = description.strip().rstrip(".")
    return f"{text[:1].lower()}{text[1:]}"
