from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from core.domain.graph import EdgeKind, EntityRef, GraphEdge, GraphNode, GraphTree, NodeKind
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import DeveloperStatus, StatusSource
from core.ports.repositories import RollupRepository, StatusRepository


class RollupService:
    def __init__(
        self,
        status_repository: StatusRepository,
        rollup_repository: RollupRepository | None = None,
    ) -> None:
        self._status_repository = status_repository
        self._rollup_repository = rollup_repository

    async def compute(self, tree: GraphTree, as_of: date) -> tuple[NodeStatus, ...]:
        index = _TreeIndex(tree)
        statuses: dict[str, NodeStatus] = {}

        async def rollup_node(node: GraphNode) -> NodeStatus | None:
            existing = statuses.get(node.id)
            if existing is not None:
                return existing
            if node.kind is NodeKind.TASK:
                return _task_node_status(node, as_of)
            if node.kind is NodeKind.DEVELOPER:
                status = await self._developer_status(index, node, as_of)
            else:
                child_statuses = [
                    child_status
                    for child in index.contained_children(node.id)
                    if (child_status := await rollup_node(child)) is not None
                ]
                status = (
                    _aggregate_workstream_node(node, child_statuses, as_of)
                    if node.kind is NodeKind.WORKSTREAM
                    else _aggregate_node(node, child_statuses, as_of)
                )
            statuses[node.id] = status
            return status

        await rollup_node(tree.root)
        return tuple(
            statuses[node.id]
            for node in tree.nodes
            if node.id in statuses and node.kind is not NodeKind.TASK
        )

    async def compute_and_record(self, tree: GraphTree, as_of: date) -> tuple[NodeStatus, ...]:
        statuses = await self.compute(tree, as_of)
        if self._rollup_repository is not None:
            for status in statuses:
                await self._rollup_repository.record_node_status(status)
        return statuses

    async def _developer_status(
        self,
        index: _TreeIndex,
        node: GraphNode,
        as_of: date,
    ) -> NodeStatus:
        status = await self._status_repository.latest_developer_status(
            node.tenant_id,
            node.id,
            as_of,
        )
        if status is None:
            return _node_status(
                node,
                Rag.UNKNOWN,
                StatusSource.UNKNOWN,
                as_of,
                (
                    RollupFactor(
                        description="No developer status data is available.",
                        contributes=Rag.UNKNOWN,
                        source_ref=node.ref,
                    ),
                ),
            )
        critical_task = index.first_critical_assigned_task(node.id)
        rag, factors = _developer_factors(node, status, critical_task)
        return _node_status(node, rag, status.source, as_of, factors)


class _TreeIndex:
    def __init__(self, tree: GraphTree) -> None:
        self._nodes = {node.id: node for node in tree.nodes}
        self._contains: dict[str, list[GraphNode]] = {}
        self._assigned_tasks: dict[str, list[tuple[GraphEdge, GraphNode]]] = {}

        for edge in tree.edges:
            target = self._nodes.get(edge.to_node_id)
            if target is None:
                continue
            if edge.kind is EdgeKind.CONTAINS:
                self._contains.setdefault(edge.from_node_id, []).append(target)
            elif edge.kind is EdgeKind.ASSIGNED_TO:
                source = self._nodes.get(edge.from_node_id)
                if source is not None and source.kind is NodeKind.DEVELOPER:
                    self._assigned_tasks.setdefault(source.id, []).append((edge, target))

    def contained_children(self, node_id: str) -> list[GraphNode]:
        return self._contains.get(node_id, [])

    def first_critical_assigned_task(self, developer_id: str) -> GraphNode | None:
        for edge, task in self._assigned_tasks.get(developer_id, []):
            if task.kind is NodeKind.TASK and (
                _truthy(task.metadata.get("critical_path"))
                or _truthy(edge.metadata.get("critical_path"))
            ):
                return task
        return None


def _developer_factors(
    node: GraphNode,
    status: DeveloperStatus,
    critical_task: GraphNode | None,
) -> tuple[Rag, tuple[RollupFactor, ...]]:
    source_ref = critical_task.ref if critical_task is not None else node.ref

    if status.source is StatusSource.UNKNOWN:
        return (
            Rag.UNKNOWN,
            (
                RollupFactor(
                    description="Developer status is unknown.",
                    contributes=Rag.UNKNOWN,
                    source_ref=node.ref,
                ),
            ),
        )
    if status.source is StatusSource.STALE:
        return (
            Rag.AMBER,
            (
                RollupFactor(
                    description="Developer status is stale and needs confirmation.",
                    contributes=Rag.AMBER,
                    source_ref=node.ref,
                ),
            ),
        )

    if status.blockers:
        has_high_risk_blocker = len(status.blockers) > 1 or critical_task is not None
        contributes = Rag.RED if has_high_risk_blocker else Rag.AMBER
        factors = tuple(
            RollupFactor(
                description=f"Blocker: {blocker}",
                contributes=contributes,
                source_ref=source_ref,
            )
            for blocker in status.blockers
        )
        return contributes, factors

    if status.source is StatusSource.PARTIAL:
        return (
            Rag.AMBER,
            (
                RollupFactor(
                    description="Status is partial and needs blocker or ETA confirmation.",
                    contributes=Rag.AMBER,
                    source_ref=node.ref,
                ),
            ),
        )

    if status.source is StatusSource.INFERRED:
        return (
            Rag.AMBER,
            (
                RollupFactor(
                    description="Status is inferred and needs confirmation.",
                    contributes=Rag.AMBER,
                    source_ref=node.ref,
                ),
            ),
        )

    return (
        Rag.GREEN,
        (
            RollupFactor(
                description="Confirmed status has no blockers.",
                contributes=Rag.GREEN,
                source_ref=node.ref,
            ),
        ),
    )


def _aggregate_node(
    node: GraphNode,
    child_statuses: Iterable[NodeStatus],
    as_of: date,
) -> NodeStatus:
    children = tuple(child_statuses)
    if not children:
        return _node_status(
            node,
            Rag.UNKNOWN,
            StatusSource.UNKNOWN,
            as_of,
            (
                RollupFactor(
                    description="No child status data is available.",
                    contributes=Rag.UNKNOWN,
                    source_ref=node.ref,
                ),
            ),
        )

    factors = tuple(
        factor for child in children if child.rag is not Rag.GREEN for factor in child.factors
    )
    if not factors:
        factors = (
            RollupFactor(
                description="All child statuses are confirmed with no blockers.",
                contributes=Rag.GREEN,
                source_ref=node.ref,
            ),
        )
    return _node_status(
        node,
        _aggregate_rag(children, factors),
        _aggregate_source(children),
        as_of,
        factors,
    )


def _aggregate_workstream_node(
    node: GraphNode,
    child_statuses: Iterable[NodeStatus],
    as_of: date,
) -> NodeStatus:
    children = tuple(child_statuses)
    if not children:
        return _node_status(
            node,
            Rag.UNKNOWN,
            StatusSource.UNKNOWN,
            as_of,
            (
                RollupFactor(
                    description="No child task status data is available.",
                    contributes=Rag.UNKNOWN,
                    source_ref=node.ref,
                ),
            ),
        )
    factors = tuple(
        factor for child in children if child.rag is not Rag.GREEN for factor in child.factors
    )
    target_date = _deadline(node)
    if _approaching_target_date(node, as_of):
        factors = (
            *factors,
            RollupFactor(
                description=f"Target date {target_date} is approaching.",
                contributes=Rag.AMBER,
                source_ref=node.ref,
            ),
        )
    if not factors:
        factors = (
            RollupFactor(
                description="All child tasks show active progress with no blockers.",
                contributes=Rag.GREEN,
                source_ref=node.ref,
            ),
        )
    if any(child.rag is Rag.RED for child in children):
        rag = Rag.RED
    elif any(child.rag in {Rag.AMBER, Rag.UNKNOWN} for child in children) or any(
        factor.contributes is Rag.AMBER for factor in factors
    ):
        rag = Rag.AMBER
    else:
        rag = Rag.GREEN
    return _node_status(node, rag, _aggregate_source(children), as_of, factors)


def _task_node_status(node: GraphNode, as_of: date) -> NodeStatus:
    rag = _rag_from_value(node.metadata.get("status")) or Rag.UNKNOWN
    source = _source_from_value(node.metadata.get("source"), rag)
    if rag is Rag.UNKNOWN:
        factors = (
            RollupFactor(
                description="Task status is unknown.",
                contributes=Rag.UNKNOWN,
                source_ref=node.ref,
            ),
        )
    elif rag is Rag.GREEN:
        factors = (
            RollupFactor(
                description="Task shows active progress.",
                contributes=Rag.GREEN,
                source_ref=node.ref,
            ),
        )
    elif rag is Rag.RED:
        factors = (
            RollupFactor(
                description=f"Task {node.name} is blocked.",
                contributes=Rag.RED,
                source_ref=node.ref,
            ),
        )
    else:
        factors = (
            RollupFactor(
                description=f"Task {node.name} needs attention.",
                contributes=Rag.AMBER,
                source_ref=node.ref,
            ),
        )
    return _node_status(node, rag, source, as_of, factors)


def _aggregate_rag(
    children: tuple[NodeStatus, ...],
    factors: tuple[RollupFactor, ...],
) -> Rag:
    if any(child.rag is Rag.RED for child in children):
        return Rag.RED
    blocker_count = sum(1 for factor in factors if "blocker" in factor.description.lower())
    if blocker_count > 1:
        return Rag.RED
    if any(child.rag is Rag.AMBER for child in children):
        return Rag.AMBER
    if any(child.rag is Rag.UNKNOWN for child in children):
        return Rag.UNKNOWN
    return Rag.GREEN


def _aggregate_source(children: tuple[NodeStatus, ...]) -> StatusSource:
    child_sources = {child.source for child in children}
    if StatusSource.UNKNOWN in child_sources:
        return StatusSource.UNKNOWN
    if StatusSource.STALE in child_sources:
        return StatusSource.STALE
    if StatusSource.PARTIAL in child_sources:
        return StatusSource.PARTIAL
    if StatusSource.INFERRED in child_sources:
        return StatusSource.INFERRED
    return StatusSource.CONFIRMED


def _node_status(
    node: GraphNode,
    rag: Rag,
    source: StatusSource,
    as_of: date,
    factors: tuple[RollupFactor, ...],
) -> NodeStatus:
    return NodeStatus(
        entity_ref=EntityRef(tenant_id=node.tenant_id, kind=node.kind, id=node.id),
        rag=rag,
        source=source,
        factors=factors,
        as_of=as_of,
    )


def _truthy(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


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


def _source_from_value(value: object, rag: Rag) -> StatusSource:
    if isinstance(value, str):
        try:
            return StatusSource(value)
        except ValueError:
            pass
    return StatusSource.UNKNOWN if rag is Rag.UNKNOWN else StatusSource.INFERRED


def _deadline(node: GraphNode) -> date | None:
    value = node.metadata.get("target_date")
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _approaching_target_date(node: GraphNode, as_of: date) -> bool:
    phase = node.metadata.get("phase")
    if isinstance(phase, str) and phase.strip().lower() == "done":
        return False
    deadline = _deadline(node)
    if deadline is None:
        return False
    return as_of <= deadline <= date.fromordinal(as_of.toordinal() + 14)
