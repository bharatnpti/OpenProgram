from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import StrEnum

type JsonScalar = str | int | float | bool | None

#: Node metadata key the graph keeps for itself: the day a node was deleted.
#: A deleted node stays stored, so a read as of an earlier day still finds it
#: (``GraphRepository.delete_node``). Every node write drops the key from the
#: metadata it is given, and no read returns it.
DELETED_ON_METADATA_KEY = "deleted_on"


class NodeKind(StrEnum):
    PROGRAM = "program"
    PROJECT = "project"
    WORKSTREAM = "workstream"
    SPRINT = "sprint"
    REPO = "repo"
    POD = "pod"
    DEVELOPER = "developer"
    TASK = "task"
    WORK_ITEM = "work_item"


class EdgeKind(StrEnum):
    CONTAINS = "contains"
    ASSIGNED_TO = "assigned_to"
    DEPENDS_ON = "depends_on"


@dataclass(frozen=True, kw_only=True)
class EntityRef:
    tenant_id: str
    kind: NodeKind
    id: str


@dataclass(frozen=True, kw_only=True)
class GraphNode:
    tenant_id: str
    id: str
    kind: NodeKind
    name: str
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)

    @property
    def ref(self) -> EntityRef:
        return EntityRef(tenant_id=self.tenant_id, kind=self.kind, id=self.id)


@dataclass(frozen=True, kw_only=True)
class Program(GraphNode):
    kind: NodeKind = field(default=NodeKind.PROGRAM, init=False)


@dataclass(frozen=True, kw_only=True)
class Project(GraphNode):
    kind: NodeKind = field(default=NodeKind.PROJECT, init=False)


@dataclass(frozen=True, kw_only=True)
class Workstream(GraphNode):
    kind: NodeKind = field(default=NodeKind.WORKSTREAM, init=False)


@dataclass(frozen=True, kw_only=True)
class SprintNode(GraphNode):
    kind: NodeKind = field(default=NodeKind.SPRINT, init=False)


@dataclass(frozen=True, kw_only=True)
class RepoNode(GraphNode):
    kind: NodeKind = field(default=NodeKind.REPO, init=False)


@dataclass(frozen=True, kw_only=True)
class Pod(GraphNode):
    kind: NodeKind = field(default=NodeKind.POD, init=False)


@dataclass(frozen=True, kw_only=True)
class Developer(GraphNode):
    kind: NodeKind = field(default=NodeKind.DEVELOPER, init=False)


@dataclass(frozen=True, kw_only=True)
class Task(GraphNode):
    kind: NodeKind = field(default=NodeKind.TASK, init=False)


@dataclass(frozen=True, kw_only=True)
class WorkItem(GraphNode):
    kind: NodeKind = field(default=NodeKind.WORK_ITEM, init=False)


@dataclass(frozen=True, kw_only=True)
class GraphEdge:
    tenant_id: str
    from_node_id: str
    to_node_id: str
    kind: EdgeKind
    valid_from: date | None = None
    valid_to: date | None = None
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)

    def is_active_on(self, as_of: date) -> bool:
        starts_before = self.valid_from is None or self.valid_from <= as_of
        return starts_before and self.ends_after(as_of)

    def ends_after(self, day: date) -> bool:
        """Whether the edge still holds on ``day`` or a later day.

        True for an edge active on ``day`` and for one that starts later; false
        once it has ended, which makes it history for the days before its end.
        """
        return self.valid_to is None or self.valid_to > day

    def ended_on(self, day: date) -> GraphEdge | None:
        """The edge as it reads once it ends on ``day``, or None if it then holds on no day.

        Windows are half-open, so an edge ended on ``day`` is gone from ``day``
        on and unchanged for every earlier day. One that starts on ``day`` or
        later would hold on no day at all. One that already ended by ``day``
        is returned as it is: its history is not rewritten.
        """
        if not self.ends_after(day):
            return self
        if self.valid_from is not None and self.valid_from >= day:
            return None
        return replace(self, valid_to=day)


@dataclass(frozen=True, kw_only=True)
class GraphTree:
    root: GraphNode
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


@dataclass(frozen=True, kw_only=True)
class FactEvent:
    tenant_id: str
    source: str
    entity_ref: EntityRef
    payload: Mapping[str, JsonScalar]
    observed_at: datetime
    correlation_id: str
    ingested_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True, kw_only=True)
class VectorMatch:
    entity_ref: EntityRef
    score: float
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)


def normalize_vector(vector: Sequence[float]) -> tuple[float, ...]:
    return tuple(float(value) for value in vector)


# What puts a workstream in use: work it holds directly.
_WORK_KINDS = frozenset({NodeKind.TASK, NodeKind.WORK_ITEM})


def workstreams_in_use(
    nodes: Iterable[GraphNode], edges: Iterable[GraphEdge], as_of: date
) -> frozenset[str]:
    """The workstreams that hold work on ``as_of``: a task or work item they contain.

    Workstreams are optional. Pods are the one grouping a tenant must set up,
    and most teams run all their work through them, so a workstream is often
    created, linked to a project and served by pods, yet never holds a task.
    The persona views show only the workstreams in use, so an empty one never
    reads as an unknown tile, a 0% panel or a branch with nothing on it.
    Pods, repos or an owner do not put one in use; an active ``contains`` edge
    to a task or work item does, read on ``as_of`` like every other edge.
    Admin and the config API still list every workstream.
    """
    kinds = {node.id: node.kind for node in nodes}
    return frozenset(
        edge.from_node_id
        for edge in edges
        if edge.kind is EdgeKind.CONTAINS
        and kinds.get(edge.from_node_id) is NodeKind.WORKSTREAM
        and kinds.get(edge.to_node_id) in _WORK_KINDS
        and edge.is_active_on(as_of)
    )
