from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from core.application.blocker_resolution import (
    BlockerProvenance,
    BlockerResolutionService,
    ResolvedBlocker,
)
from core.application.status_summaries import NO_REPLY_BLOCKER
from core.domain.graph import EdgeKind, EntityRef, GraphNode, GraphTree, JsonScalar, NodeKind
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

    A parent counts only the children that carry health information. A person
    always does: one with no status is unknown for their pod, never green. A
    task does only when it is blocked, at risk or carries an explicit RAG
    status (see `task_health`). Everything else -- a repo, a sprint, a work
    item or workstream with nothing beneath it that reports, a task that only
    says where the work is -- is neutral: it keeps its own unknown status but
    is left out of its parent's, so a project whose people have all reported
    can be green although it also holds repos, sprints and open tickets. A
    node with nothing status-bearing beneath it stays unknown, never green.
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
        # Recorded nodes that carry no health information for their parents.
        neutral: set[str] = set()
        blockers: dict[str, tuple[ResolvedBlocker, ...]] = {}
        incoming = await self._incoming_dependencies(tree, as_of, blockers)

        async def rollup_node(node: GraphNode) -> NodeStatus | None:
            """Roll `node` up and return what it contributes to its parent.

            None means it carries no health information: the parent leaves it
            out instead of counting it unknown. Its own status is still
            recorded, except a task's, which never is.
            """
            existing = statuses.get(node.id)
            if existing is not None:
                return None if node.id in neutral else existing
            if node.kind is NodeKind.TASK:
                return _task_node_status(node, as_of)
            if node.kind is NodeKind.DEVELOPER:
                status = await self._developer_status(node, as_of, blockers)
                statuses[node.id] = status
                return status
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
            if node.kind is NodeKind.POD and child_statuses and incoming.get(node.id):
                status = _with_incoming_dependencies(status, incoming[node.id])
            statuses[node.id] = status
            if not child_statuses:
                # Nothing beneath it reports: unknown itself, neutral above.
                neutral.add(node.id)
                return None
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

    async def _incoming_dependencies(
        self,
        tree: GraphTree,
        as_of: date,
        blockers: dict[str, tuple[ResolvedBlocker, ...]],
    ) -> dict[str, tuple[RollupFactor, ...]]:
        """Per pod in the tree: the other teams' people waiting on its work.

        A cross-team blocker counts for the blocked person's pod. The pod it
        waits on reads it as a reason line, never as its own blocker: it keeps
        its colour, and no longer says it has nothing on it. Read once per
        developer and kept in ``blockers`` for the developer's own status.
        """
        pods = {node.id for node in tree.nodes if node.kind is NodeKind.POD}
        found: dict[str, list[RollupFactor]] = {}
        for node in tree.nodes:
            if node.kind is not NodeKind.DEVELOPER or not pods:
                continue
            blockers[node.id] = await self._open_blockers(node, as_of)
            for blocker in blockers[node.id]:
                for pod_id in blocker.depends_on_pod_ids:
                    if pod_id in pods:
                        found.setdefault(pod_id, []).append(_incoming_factor(node, blocker))
        if tree.root.kind is NodeKind.POD and self._blocker_resolution is not None:
            # A pod's own tree holds none of the other teams' people: find them
            # through the blockers recorded on the pod's tasks.
            tasks = [node.id for node in tree.nodes if node.kind is NodeKind.TASK]
            for developer, blocker in await self._blocker_resolution.blockers_on_tasks(
                tree.root.tenant_id, tasks, as_of
            ):
                if developer.id not in blockers and tree.root.id in blocker.depends_on_pod_ids:
                    found.setdefault(tree.root.id, []).append(_incoming_factor(developer, blocker))
        return {pod_id: _dedupe_factors(factors) for pod_id, factors in found.items()}

    async def _open_blockers(self, node: GraphNode, as_of: date) -> tuple[ResolvedBlocker, ...]:
        if self._blocker_resolution is None:
            return ()
        return await self._blocker_resolution.open_blockers_for_developer(
            node.tenant_id, node.id, as_of
        )

    async def _developer_status(
        self,
        node: GraphNode,
        as_of: date,
        known_blockers: dict[str, tuple[ResolvedBlocker, ...]] | None = None,
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
                        kind=FactorKind.STATUS,
                    ),
                ),
            )
        known = (known_blockers or {}).get(node.id)
        blockers = known if known is not None else await self._open_blockers(node, as_of)
        rag, factors = _developer_factors(node, status, blockers)
        return _node_status(node, rag, status.source, as_of, factors)


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
                description=_blocker_description(blocker),
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


def _blocker_description(blocker: ResolvedBlocker) -> str:
    """The blocker's reason line; a cross-team one says whose work it waits on.

    It travels up from the blocked person's pod to the project and the
    program, which then read it as the cross-team dependency it is.
    """
    text = f"Blocker: {blocker.description}"
    if not blocker.cross_team:
        return text
    teams = " and ".join(_team_name(name) for name in blocker.depends_on_pod_names)
    return f"{text.rstrip().rstrip('.')}; cross-team dependency on {teams}."


def _incoming_factor(developer: GraphNode, blocker: ResolvedBlocker) -> RollupFactor:
    waited = blocker.depends_on_ref.id if blocker.depends_on_ref is not None else "its work"
    blocked = (
        f" for {blocker.work_item_ref.id}"
        if blocker.work_item_ref is not None and blocker.work_item_ref.id != waited
        else ""
    )
    return RollupFactor(
        description=f"Incoming dependency: {developer.name} waits on {waited}{blocked}.",
        contributes=Rag.GREEN,
        source_ref=developer.ref,
        kind=FactorKind.DEPENDENCY,
        blocker_id=blocker.blocker_id,
        work_item_ref=blocker.depends_on_ref,
    )


def _with_incoming_dependencies(
    status: NodeStatus, incoming: tuple[RollupFactor, ...]
) -> NodeStatus:
    """A pod's status with the dependencies on it: same colour, one reason line each.

    A green pod drops its "no blockers" line for them, which was no longer
    the whole story; any other pod lists them after its own reasons.
    """
    own = tuple(
        factor
        for factor in status.factors
        if not (status.rag is Rag.GREEN and factor.kind is FactorKind.AGGREGATE)
    )
    return NodeStatus(
        entity_ref=status.entity_ref,
        rag=status.rag,
        source=status.source,
        factors=own + incoming,
        as_of=status.as_of,
    )


def _team_name(pod_name: str) -> str:
    name = pod_name.strip()
    return name[: -len(" Pod")] if name.endswith(" Pod") else name


def _legacy_status_blockers(
    node: GraphNode, status: DeveloperStatus
) -> tuple[ResolvedBlocker, ...]:
    """When nothing was resolved, flat status strings behave as global blockers.

    Except the placeholder a non-response status carries: silence is the
    person's status (inferred, stale or unknown), never a blocker. Counted as
    one, it read "Blocker: no confirmed reply" up to the program and, beside
    one real blocker, made two -- red. The resolver drops it the same way.
    """
    descriptions = [
        description
        for description in status.blockers
        if description.strip().lower() != NO_REPLY_BLOCKER
    ]
    return tuple(
        ResolvedBlocker(
            blocker_id=f"legacy:{node.id}:{index}",
            description=description,
            developer_id=node.id,
            first_seen_on=status.as_of,
            work_item_ref=None,
            work_item_name=None,
            explicit_pod_ref=None,
            pod_ids=(),
            unattributed=True,
            critical=False,
            provenance=BlockerProvenance.LEGACY_STATUS,
        )
        for index, description in enumerate(descriptions, start=1)
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
    """Aggregate the children that carry health; with none, nothing has reported."""
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
    """Aggregate the children that carry health, then weigh the target date.

    With no such child the workstream is unknown and neutral for its project,
    as it was with no children at all: a near target date turns a reported
    workstream amber, but gives no status to one that nothing reports on.
    """
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


def _task_node_status(node: GraphNode, as_of: date) -> NodeStatus | None:
    """What a task contributes to its parent, or None when it carries no health."""
    rag = task_health(node.metadata)
    if rag is None:
        return None
    if rag is Rag.RED:
        description = f"Task {node.name} is blocked."
    elif rag is Rag.AMBER:
        description = f"Task {node.name} needs attention."
    else:
        description = "Task shows active progress."
    return _node_status(
        node,
        rag,
        _source_from_value(node.metadata.get("source"), rag),
        as_of,
        (
            RollupFactor(
                description=description,
                contributes=rag,
                source_ref=node.ref,
                kind=FactorKind.TASK,
            ),
        ),
    )


# A task's `status` holds either an explicit RAG word, as seeds and people set
# it, or the tracker's own status name ("To Do", "In Progress", "Done"), as the
# Jira sync copies it. `state` is the tracker's normalized workflow state:
# todo, in_progress, done or blocked.
_TASK_HEALTH_WORDS: dict[str, Rag] = {
    "green": Rag.GREEN,
    "amber": Rag.AMBER,
    "at_risk": Rag.AMBER,
    "at-risk": Rag.AMBER,
    "warning": Rag.AMBER,
    "red": Rag.RED,
    "blocked": Rag.RED,
}
_TASK_DONE_WORDS = frozenset({"done", "complete", "completed"})


def task_health(values: Mapping[str, JsonScalar]) -> Rag | None:
    """What a task tells its parent about health, or None when it tells nothing.

    Only a blocked task, an at-risk one, or an explicit RAG status says how the
    work is going. Where the work sits in the workflow -- to do, in progress,
    done -- does not, so such a task is neutral for its parent: neither unknown
    nor green. `state` catches a blocked task whose tracker status has another
    name ("On Hold"), so it still goes red.
    """
    if _normalized(values.get("state")) == "blocked":
        return Rag.RED
    return _TASK_HEALTH_WORDS.get(_normalized(values.get("status")))


def task_rag(values: Mapping[str, JsonScalar]) -> Rag | None:
    """A task's own colour, as its chip shows it, or None when it has none.

    Its health where it has any (`task_health`), green once it is done --
    whatever the tracker calls done -- and unknown when it says so. To do and
    in progress get none: RAG has no "in progress", and a green task counts as
    complete in progress percentages.
    """
    health = task_health(values)
    if health is not None:
        return health
    status = _normalized(values.get("status"))
    if status in _TASK_DONE_WORDS or _normalized(values.get("state")) == "done":
        return Rag.GREEN
    if status == "unknown":
        return Rag.UNKNOWN
    return None


def _normalized(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


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
