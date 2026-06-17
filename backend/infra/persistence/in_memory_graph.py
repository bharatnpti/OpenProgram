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
from core.domain.integrations import SyncCursor
from core.domain.rollup import NodeStatus
from core.domain.status import CheckIn, DeveloperStatus


@dataclass
class InMemoryGraphStore:
    _nodes: dict[tuple[str, str], GraphNode] = field(default_factory=dict)
    _edges: list[GraphEdge] = field(default_factory=list)
    _facts: list[FactEvent] = field(default_factory=list)
    _vectors: dict[tuple[str, str, str], tuple[float, ...]] = field(default_factory=dict)
    _checkins: list[CheckIn] = field(default_factory=list)
    _developer_statuses: dict[tuple[str, str, date], DeveloperStatus] = field(default_factory=dict)
    _node_statuses: dict[tuple[str, str, str, date], NodeStatus] = field(default_factory=dict)
    _sync_cursors: dict[tuple[str, str, str], SyncCursor] = field(default_factory=dict)

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

    async def record_checkin(self, checkin: CheckIn) -> None:
        self._checkins = [
            existing
            for existing in self._checkins
            if not (
                existing.tenant_id == checkin.tenant_id
                and existing.correlation_id == checkin.correlation_id
            )
        ]
        self._checkins.append(checkin)

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> CheckIn | None:
        for checkin in reversed(self._checkins):
            if checkin.tenant_id == tenant_id and checkin.correlation_id == correlation_id:
                return checkin
        return None

    async def record_developer_status(self, status: DeveloperStatus) -> None:
        self._developer_statuses[(status.tenant_id, status.developer_id, status.as_of)] = status

    async def latest_developer_status(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> DeveloperStatus | None:
        matching = [
            status
            for status in self._developer_statuses.values()
            if status.tenant_id == tenant_id
            and status.developer_id == developer_id
            and status.as_of <= as_of
        ]
        return max(matching, key=lambda status: status.as_of) if matching else None

    async def developers_without_checkin(self, tenant_id: str, as_of: date) -> list[str]:
        known_developer_ids = {
            node.id
            for (node_tenant_id, _), node in self._nodes.items()
            if node_tenant_id == tenant_id and node.kind is NodeKind.DEVELOPER
        }
        known_developer_ids.update(
            status.developer_id
            for status in self._developer_statuses.values()
            if status.tenant_id == tenant_id
        )
        known_developer_ids.update(
            checkin.developer_id for checkin in self._checkins if checkin.tenant_id == tenant_id
        )
        replied_developer_ids = {
            checkin.developer_id
            for checkin in self._checkins
            if checkin.tenant_id == tenant_id
            and checkin.replied_at is not None
            and checkin.replied_at.date() == as_of
        }
        return sorted(known_developer_ids - replied_developer_ids)

    async def record_node_status(self, status: NodeStatus) -> None:
        self._node_statuses[
            (
                status.entity_ref.tenant_id,
                status.entity_ref.kind.value,
                status.entity_ref.id,
                status.as_of,
            )
        ] = status

    async def latest_node_status(
        self, tenant_id: str, entity_ref: EntityRef, as_of: date
    ) -> NodeStatus | None:
        matching = [
            status
            for status in self._node_statuses.values()
            if status.entity_ref.tenant_id == tenant_id
            and status.entity_ref == entity_ref
            and status.as_of <= as_of
        ]
        return max(matching, key=lambda status: status.as_of) if matching else None

    async def list_node_statuses(self, tenant_id: str, as_of: date) -> list[NodeStatus]:
        latest_by_entity: dict[tuple[str, str], NodeStatus] = {}
        for status in self._node_statuses.values():
            if status.entity_ref.tenant_id != tenant_id or status.as_of > as_of:
                continue
            key = (status.entity_ref.kind.value, status.entity_ref.id)
            current = latest_by_entity.get(key)
            if current is None or current.as_of < status.as_of:
                latest_by_entity[key] = status
        return sorted(
            latest_by_entity.values(),
            key=lambda status: (status.entity_ref.kind.value, status.entity_ref.id),
        )

    async def get_cursor(self, tenant_id: str, connector: str, scope: str) -> SyncCursor:
        return self._sync_cursors.get((tenant_id, connector, scope), SyncCursor())

    async def record_cursor(
        self, tenant_id: str, connector: str, scope: str, cursor: SyncCursor
    ) -> None:
        self._sync_cursors[(tenant_id, connector, scope)] = cursor

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
