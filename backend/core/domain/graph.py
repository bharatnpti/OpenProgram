from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

type JsonScalar = str | int | float | bool | None


class NodeKind(StrEnum):
    PROGRAM = "program"
    PROJECT = "project"
    POD = "pod"
    DEVELOPER = "developer"
    TASK = "task"


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
class Pod(GraphNode):
    kind: NodeKind = field(default=NodeKind.POD, init=False)


@dataclass(frozen=True, kw_only=True)
class Developer(GraphNode):
    kind: NodeKind = field(default=NodeKind.DEVELOPER, init=False)


@dataclass(frozen=True, kw_only=True)
class Task(GraphNode):
    kind: NodeKind = field(default=NodeKind.TASK, init=False)


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
        ends_after = self.valid_to is None or self.valid_to > as_of
        return starts_before and ends_after


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
