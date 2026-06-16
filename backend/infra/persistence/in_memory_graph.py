from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from math import sqrt

from core.domain.errors import GraphNotFound
from core.domain.graph import (
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    GraphTree,
    NodeKind,
    VectorMatch,
    normalize_vector,
)


@dataclass
class InMemoryGraphStore:
    _nodes: dict[tuple[str, str], GraphNode] = field(default_factory=dict)
    _edges: list[GraphEdge] = field(default_factory=list)
    _facts: list[FactEvent] = field(default_factory=list)
    _vectors: dict[tuple[str, str, str], tuple[float, ...]] = field(default_factory=dict)

    async def upsert_node(self, node: GraphNode) -> None:
        self._nodes[(node.tenant_id, node.id)] = node

    async def add_edge(self, edge: GraphEdge) -> None:
        if edge not in self._edges:
            self._edges.append(edge)

    async def get_program_tree(self, tenant_id: str, program_id: str, as_of: date) -> GraphTree:
        root = self._nodes.get((tenant_id, program_id))
        if root is None:
            raise GraphNotFound(f"program {program_id} not found for tenant {tenant_id}")

        selected_edges: list[GraphEdge] = []
        selected_nodes: dict[str, GraphNode] = {root.id: root}
        queue: deque[str] = deque([root.id])

        while queue:
            current_id = queue.popleft()
            for edge in self._active_edges_from(tenant_id, current_id, as_of):
                target = self._nodes.get((tenant_id, edge.to_node_id))
                if target is None:
                    continue
                selected_edges.append(edge)
                if target.id not in selected_nodes:
                    selected_nodes[target.id] = target
                    queue.append(target.id)

        return GraphTree(
            root=root,
            nodes=tuple(selected_nodes.values()),
            edges=tuple(selected_edges),
        )

    async def active_developer_memberships(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> list[GraphEdge]:
        return [
            edge
            for edge in self._edges
            if edge.tenant_id == tenant_id
            and edge.kind in {EdgeKind.CONTAINS, EdgeKind.ASSIGNED_TO}
            and edge.is_active_on(as_of)
            and (edge.from_node_id == developer_id or edge.to_node_id == developer_id)
        ]

    async def append_fact(self, fact: FactEvent) -> None:
        self._facts.append(fact)

    async def list_facts(self, tenant_id: str, entity_ref: EntityRef) -> list[FactEvent]:
        return [
            fact
            for fact in self._facts
            if fact.tenant_id == tenant_id and fact.entity_ref == entity_ref
        ]

    async def upsert_embedding(
        self, tenant_id: str, entity_ref: EntityRef, vector: Sequence[float]
    ) -> None:
        self._vectors[(tenant_id, entity_ref.kind.value, entity_ref.id)] = normalize_vector(vector)

    async def search(
        self, tenant_id: str, vector: Sequence[float], limit: int
    ) -> list[VectorMatch]:
        query = normalize_vector(vector)
        scored: list[VectorMatch] = []
        for (stored_tenant, kind, entity_id), stored_vector in self._vectors.items():
            if stored_tenant != tenant_id:
                continue
            score = _cosine(query, stored_vector)
            scored.append(
                VectorMatch(
                    entity_ref=EntityRef(tenant_id=tenant_id, kind=_node_kind(kind), id=entity_id),
                    score=score,
                )
            )
        return sorted(scored, key=lambda match: match.score, reverse=True)[:limit]

    def _active_edges_from(self, tenant_id: str, node_id: str, as_of: date) -> list[GraphEdge]:
        return [
            edge
            for edge in self._edges
            if edge.tenant_id == tenant_id
            and edge.from_node_id == node_id
            and edge.kind in {EdgeKind.CONTAINS, EdgeKind.ASSIGNED_TO}
            and edge.is_active_on(as_of)
        ]


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _node_kind(value: str) -> NodeKind:
    return NodeKind(value)
