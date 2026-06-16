from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from core.domain.graph import EntityRef, FactEvent, GraphEdge, GraphNode, GraphTree, VectorMatch


class GraphRepository(Protocol):
    async def upsert_node(self, node: GraphNode) -> None: ...

    async def add_edge(self, edge: GraphEdge) -> None: ...

    async def get_program_tree(self, tenant_id: str, program_id: str, as_of: date) -> GraphTree: ...

    async def active_developer_memberships(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> list[GraphEdge]: ...


class TimeSeriesRepository(Protocol):
    async def append_fact(self, fact: FactEvent) -> None: ...

    async def list_facts(self, tenant_id: str, entity_ref: EntityRef) -> list[FactEvent]: ...


class VectorStore(Protocol):
    async def upsert_embedding(
        self, tenant_id: str, entity_ref: EntityRef, vector: Sequence[float]
    ) -> None: ...

    async def search(
        self, tenant_id: str, vector: Sequence[float], limit: int
    ) -> list[VectorMatch]: ...
