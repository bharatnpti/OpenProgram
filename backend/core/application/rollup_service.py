from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from core.application.blocker_resolution import (
    BlockerProvenance,
    BlockerResolutionService,
    ResolvedBlocker,
)
from core.domain.graph import EdgeKind, EntityRef, GraphNode, GraphTree, NodeKind
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from core.domain.status import DeveloperStatus, StatusSource
from core.ports.repositories import RollupRepository, StatusRepository


class RollupService:
    """RAG rollups over a graph tree.

    Person-level status is global: a developer node carries every open
    blocker as a typed, attributed factor. Pod-scoping happens exactly once,
    at POD aggregation, by filtering blocker factors to the pod at hand —
    ancestors then compose pod results structurally, so a project that does
    not contain pod B never sees a B-scoped blocker.
    """

    def __init__(
        self,
        status_repository: StatusRepository,
        rollup_repository: RollupRepository | None = None,
        blocker_resolution: BlockerResolutionService | None = None,
    ) -> None:
        self._status_repository = status_repository
        self._rollup_repository = rollup_repository
        self._blocker_resolution = blocker_resolution

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
                status = await self._developer_status(node, as_of)
            else:
                child_statuses = [
                    child_status
                    for child in index.contained_children(node.id)
                    if (child_status := await rollup_node(child)) is not None
                ]
                if node.kind is NodeKind.POD:
                    child_statuses = [
                        _effective_child_status(child, node.id) for child in child_statuses
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

    async def _developer_status(self, node: GraphNode, as_of: date) -> NodeStatus:
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
                        kind=FactorKind.STATUS,
                    ),
                ),
            )
        blockers = await self._resolved_blockers(node, status, as_of)
        rag, factors = _developer_factors(node, status, blockers)
        return _node_status(node, rag, status.source, as_of, factors)

    async def _resolved_blockers(
        self, node: GraphNode, status: DeveloperStatus, as_of: date
    ) -> tuple[ResolvedBlocker, ...]:
        if self._blocker_resolution is not None:
            return await self._blocker_resolution.open_blockers_for_developer(
                node.tenant_id, node.id, as_of
            )
        return ()


class _TreeIndex:
    def __init__(self, tree: GraphTree) -> None:
        self._nodes = {node.id: node for node in tree.nodes}
        self._contains: dict[str, list[GraphNode]] = {}

        for edge in tree.edges:
            target = self._nodes.get(edge.to_node_id)
            if target is None:
                continue
            if edge.kind is EdgeKind.CONTAINS:
                self._contains.setdefault(edge.from_node_id, []).append(target)

    def contained_children(self, node_id: str) -> list[GraphNode]:
        return self._contains.get(node_id, [])


def _developer_factors(
    node: GraphNode,
    status: DeveloperStatus,
    blockers: tuple[ResolvedBlocker, ...],
) -> tuple[Rag, tuple[RollupFactor, ...]]:
    if status.source is StatusSource.UNKNOWN:
        return (
            Rag.UNKNOWN,
            (
                RollupFactor(
                    description="Developer status is unknown.",
                    contributes=Rag.UNKNOWN,
                    source_ref=node.ref,
                    kind=FactorKind.STATUS,
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
                    kind=FactorKind.STATUS,
                ),
            ),
        )

    effective_blockers = blockers or _legacy_status_blockers(node, status)
    if effective_blockers:
        # The developer node stays person-global: any critical blocker or more
        # than one distinct open blocker escalates the PERSON to red. Pod
        # aggregation later filters these factors to each pod's own scope.
        factors = tuple(
            RollupFactor(
                description=f"Blocker: {blocker.description}",
                contributes=Rag.RED if blocker.critical else Rag.AMBER,
                source_ref=blocker.work_item_ref or node.ref,
                kind=FactorKind.BLOCKER,
                blocker_id=blocker.blocker_id,
                work_item_ref=blocker.work_item_ref,
                applies_to_pod_ids=blocker.pod_ids,
                unattributed=blocker.unattributed,
            )
            for blocker in effective_blockers
        )
        rag = _rag_from_factors(factors)
        return rag, factors

    if status.source is StatusSource.PARTIAL:
        return (
            Rag.AMBER,
            (
                RollupFactor(
                    description="Status is partial and needs blocker or ETA confirmation.",
                    contributes=Rag.AMBER,
                    source_ref=node.ref,
                    kind=FactorKind.STATUS,
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
                    kind=FactorKind.STATUS,
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
                kind=FactorKind.STATUS,
            ),
        ),
    )


def _legacy_status_blockers(
    node: GraphNode, status: DeveloperStatus
) -> tuple[ResolvedBlocker, ...]:
    """Without a resolver, flat status strings behave as global blockers."""
    return tuple(
        ResolvedBlocker(
            blocker_id=f"legacy:{node.id}:{index}",
            description=description,
            developer_id=node.id,
            first_seen_on=status.as_of,
            work_item_ref=None,
            explicit_pod_ref=None,
            pod_ids=(),
            unattributed=True,
            critical=False,
            provenance=BlockerProvenance.LEGACY_STATUS,
        )
        for index, description in enumerate(status.blockers, start=1)
    )


def _effective_child_status(child: NodeStatus, pod_id: str) -> NodeStatus:
    """Filter a child's blocker factors to the pod being aggregated.

    Non-blocker factors always pass (person-global signals like staleness).
    Blocker factors pass when they carry no pod scoping (legacy rows) or when
    this pod is in scope — unattributed blockers already carry every pod the
    developer belongs to, so they need no special case.
    """
    kept = tuple(
        factor
        for factor in child.factors
        if factor.kind is not FactorKind.BLOCKER
        or not factor.applies_to_pod_ids
        or pod_id in factor.applies_to_pod_ids
    )
    if kept == child.factors:
        return child
    if not kept:
        kept = (
            RollupFactor(
                description="No factors apply within this pod.",
                contributes=Rag.GREEN,
                source_ref=child.entity_ref,
                kind=FactorKind.AGGREGATE,
            ),
        )
    return NodeStatus(
        entity_ref=child.entity_ref,
        rag=_rag_from_factors(kept),
        source=child.source,
        factors=kept,
        as_of=child.as_of,
    )


def _rag_from_factors(factors: tuple[RollupFactor, ...]) -> Rag:
    if any(factor.contributes is Rag.RED for factor in factors):
        return Rag.RED
    if _distinct_blocker_count(factors) > 1:
        return Rag.RED
    if any(factor.contributes is Rag.AMBER for factor in factors):
        return Rag.AMBER
    if any(factor.contributes is Rag.UNKNOWN for factor in factors):
        return Rag.UNKNOWN
    return Rag.GREEN


def _distinct_blocker_count(factors: Iterable[RollupFactor]) -> int:
    return len(
        {
            factor.blocker_id or factor.description
            for factor in factors
            if factor.kind is FactorKind.BLOCKER
        }
    )


def _dedupe_factors(factors: Iterable[RollupFactor]) -> tuple[RollupFactor, ...]:
    """Collapse the same blocker surfacing via multiple pods to one factor."""
    seen: set[tuple[FactorKind, str, EntityRef]] = set()
    unique: list[RollupFactor] = []
    for factor in factors:
        key = (factor.kind, factor.blocker_id or factor.description, factor.source_ref)
        if key in seen:
            continue
        seen.add(key)
        unique.append(factor)
    return tuple(unique)


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
                    kind=FactorKind.AGGREGATE,
                ),
            ),
        )

    factors = _dedupe_factors(
        factor for child in children if child.rag is not Rag.GREEN for factor in child.factors
    )
    if not factors:
        factors = (
            RollupFactor(
                description="All child statuses are confirmed with no blockers.",
                contributes=Rag.GREEN,
                source_ref=node.ref,
                kind=FactorKind.AGGREGATE,
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
                    kind=FactorKind.AGGREGATE,
                ),
            ),
        )
    factors = _dedupe_factors(
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
                kind=FactorKind.TARGET_DATE,
            ),
        )
    if not factors:
        factors = (
            RollupFactor(
                description="All child tasks show active progress with no blockers.",
                contributes=Rag.GREEN,
                source_ref=node.ref,
                kind=FactorKind.AGGREGATE,
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
                kind=FactorKind.TASK,
            ),
        )
    elif rag is Rag.GREEN:
        factors = (
            RollupFactor(
                description="Task shows active progress.",
                contributes=Rag.GREEN,
                source_ref=node.ref,
                kind=FactorKind.TASK,
            ),
        )
    elif rag is Rag.RED:
        factors = (
            RollupFactor(
                description=f"Task {node.name} is blocked.",
                contributes=Rag.RED,
                source_ref=node.ref,
                kind=FactorKind.TASK,
            ),
        )
    else:
        factors = (
            RollupFactor(
                description=f"Task {node.name} needs attention.",
                contributes=Rag.AMBER,
                source_ref=node.ref,
                kind=FactorKind.TASK,
            ),
        )
    return _node_status(node, rag, source, as_of, factors)


def _aggregate_rag(
    children: tuple[NodeStatus, ...],
    factors: tuple[RollupFactor, ...],
) -> Rag:
    if any(child.rag is Rag.RED for child in children):
        return Rag.RED
    if _distinct_blocker_count(factors) > 1:
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
