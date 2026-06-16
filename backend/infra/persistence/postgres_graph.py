from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol

from core.domain.errors import GraphNotFound
from core.domain.graph import (
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    GraphTree,
    JsonScalar,
    NodeKind,
    VectorMatch,
    normalize_vector,
)


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresGraphRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def upsert_node(self, node: GraphNode) -> None:
        await self._executor.execute(
            """
            INSERT INTO graph_nodes (tenant_id, id, kind, name, metadata)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, id)
            DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name, metadata = EXCLUDED.metadata
            """,
            (node.tenant_id, node.id, node.kind.value, node.name, dict(node.metadata)),
        )
        await self._sync_age_node(node)

    async def add_edge(self, edge: GraphEdge) -> None:
        await self._executor.execute(
            """
            INSERT INTO graph_edges (
                tenant_id, from_node_id, to_node_id, kind, valid_from, valid_to, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                edge.tenant_id,
                edge.from_node_id,
                edge.to_node_id,
                edge.kind.value,
                edge.valid_from,
                edge.valid_to,
                dict(edge.metadata),
            ),
        )
        await self._sync_age_edge(edge)

    async def get_program_tree(self, tenant_id: str, program_id: str, as_of: date) -> GraphTree:
        rows = await self._executor.fetch(
            """
            WITH RECURSIVE walk AS (
                SELECT tenant_id, id, kind, name, metadata
                FROM graph_nodes
                WHERE tenant_id = %s AND id = %s
              UNION
                SELECT child.tenant_id, child.id, child.kind, child.name, child.metadata
                FROM graph_nodes child
                JOIN graph_edges edge
                  ON edge.tenant_id = child.tenant_id
                 AND edge.to_node_id = child.id
                JOIN walk parent
                  ON parent.tenant_id = edge.tenant_id
                 AND parent.id = edge.from_node_id
                WHERE edge.kind IN ('contains', 'assigned_to')
                  AND (edge.valid_from IS NULL OR edge.valid_from <= %s)
                  AND (edge.valid_to IS NULL OR edge.valid_to > %s)
            )
            SELECT * FROM walk
            """,
            (tenant_id, program_id, as_of, as_of),
        )
        nodes = tuple(_node_from_row(row) for row in rows)
        if not nodes:
            raise GraphNotFound(f"program {program_id} not found for tenant {tenant_id}")

        edge_rows = await self._executor.fetch(
            """
            SELECT tenant_id, from_node_id, to_node_id, kind, valid_from, valid_to, metadata
            FROM graph_edges
            WHERE tenant_id = %s
              AND kind IN ('contains', 'assigned_to')
              AND (valid_from IS NULL OR valid_from <= %s)
              AND (valid_to IS NULL OR valid_to > %s)
            """,
            (tenant_id, as_of, as_of),
        )
        node_ids = {node.id for node in nodes}
        edges = tuple(
            edge
            for edge in (_edge_from_row(row) for row in edge_rows)
            if edge.from_node_id in node_ids and edge.to_node_id in node_ids
        )
        return GraphTree(root=nodes[0], nodes=nodes, edges=edges)

    async def active_developer_memberships(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> list[GraphEdge]:
        rows = await self._executor.fetch(
            """
            SELECT tenant_id, from_node_id, to_node_id, kind, valid_from, valid_to, metadata
            FROM graph_edges
            WHERE tenant_id = %s
              AND kind IN ('contains', 'assigned_to')
              AND (from_node_id = %s OR to_node_id = %s)
              AND (valid_from IS NULL OR valid_from <= %s)
              AND (valid_to IS NULL OR valid_to > %s)
            """,
            (tenant_id, developer_id, developer_id, as_of, as_of),
        )
        return [_edge_from_row(row) for row in rows]

    async def append_fact(self, fact: FactEvent) -> None:
        await self._executor.execute(
            """
            INSERT INTO facts (
                tenant_id, source, entity_kind, entity_id, payload,
                observed_at, ingested_at, correlation_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                fact.tenant_id,
                fact.source,
                fact.entity_ref.kind.value,
                fact.entity_ref.id,
                dict(fact.payload),
                fact.observed_at,
                fact.ingested_at,
                fact.correlation_id,
            ),
        )

    async def list_facts(self, tenant_id: str, entity_ref: EntityRef) -> list[FactEvent]:
        rows = await self._executor.fetch(
            """
            SELECT tenant_id, source, entity_kind, entity_id, payload, observed_at, ingested_at,
                   correlation_id
            FROM facts
            WHERE tenant_id = %s AND entity_kind = %s AND entity_id = %s
            ORDER BY observed_at
            """,
            (tenant_id, entity_ref.kind.value, entity_ref.id),
        )
        return [_fact_from_row(row) for row in rows]

    async def upsert_embedding(
        self, tenant_id: str, entity_ref: EntityRef, vector: Sequence[float]
    ) -> None:
        await self._executor.execute(
            """
            INSERT INTO vector_items (tenant_id, entity_kind, entity_id, embedding)
            VALUES (%s, %s, %s, %s::vector)
            ON CONFLICT (tenant_id, entity_kind, entity_id)
            DO UPDATE SET embedding = EXCLUDED.embedding
            """,
            (
                tenant_id,
                entity_ref.kind.value,
                entity_ref.id,
                _vector_literal(normalize_vector(vector)),
            ),
        )

    async def search(
        self, tenant_id: str, vector: Sequence[float], limit: int
    ) -> list[VectorMatch]:
        rows = await self._executor.fetch(
            """
            SELECT entity_kind, entity_id, 1 - (embedding <=> %s::vector) AS score
            FROM vector_items
            WHERE tenant_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (
                _vector_literal(normalize_vector(vector)),
                tenant_id,
                _vector_literal(normalize_vector(vector)),
                limit,
            ),
        )
        return [
            VectorMatch(
                entity_ref=EntityRef(
                    tenant_id=tenant_id,
                    kind=NodeKind(str(row["entity_kind"])),
                    id=str(row["entity_id"]),
                ),
                score=_float_field(row.get("score")),
            )
            for row in rows
        ]

    async def _sync_age_node(self, node: GraphNode) -> None:
        await self._executor.execute(
            f"""
            LOAD 'age';
            SET search_path = ag_catalog, "$user", public;
            SELECT *
            FROM cypher('pulseops_graph', $$
                MERGE (n:GraphNode {{
                    tenant_id: {_cypher_string(node.tenant_id)},
                    id: {_cypher_string(node.id)}
                }})
                SET n.kind = {_cypher_string(node.kind.value)},
                    n.name = {_cypher_string(node.name)}
                RETURN n
            $$) AS (n agtype);
            """
        )

    async def _sync_age_edge(self, edge: GraphEdge) -> None:
        relation = {
            EdgeKind.CONTAINS: "CONTAINS",
            EdgeKind.ASSIGNED_TO: "ASSIGNED_TO",
            EdgeKind.DEPENDS_ON: "DEPENDS_ON",
        }[edge.kind]
        valid_from = _cypher_string(edge.valid_from.isoformat()) if edge.valid_from else "null"
        valid_to = _cypher_string(edge.valid_to.isoformat()) if edge.valid_to else "null"
        await self._executor.execute(
            f"""
            LOAD 'age';
            SET search_path = ag_catalog, "$user", public;
            SELECT *
            FROM cypher('pulseops_graph', $$
                MATCH (from_node:GraphNode {{
                    tenant_id: {_cypher_string(edge.tenant_id)},
                    id: {_cypher_string(edge.from_node_id)}
                }})
                MATCH (to_node:GraphNode {{
                    tenant_id: {_cypher_string(edge.tenant_id)},
                    id: {_cypher_string(edge.to_node_id)}
                }})
                CREATE (from_node)-[edge:{relation} {{
                    kind: {_cypher_string(edge.kind.value)},
                    valid_from: {valid_from},
                    valid_to: {valid_to}
                }}]->(to_node)
                RETURN edge
            $$) AS (edge agtype);
            """
        )


def _node_from_row(row: Mapping[str, object]) -> GraphNode:
    metadata = row.get("metadata")
    return GraphNode(
        tenant_id=str(row["tenant_id"]),
        id=str(row["id"]),
        kind=NodeKind(str(row["kind"])),
        name=str(row["name"]),
        metadata=_json_mapping(metadata),
    )


def _edge_from_row(row: Mapping[str, object]) -> GraphEdge:
    valid_from = row.get("valid_from")
    valid_to = row.get("valid_to")
    metadata = row.get("metadata")
    return GraphEdge(
        tenant_id=str(row["tenant_id"]),
        from_node_id=str(row["from_node_id"]),
        to_node_id=str(row["to_node_id"]),
        kind=EdgeKind(str(row["kind"])),
        valid_from=valid_from if isinstance(valid_from, date) else None,
        valid_to=valid_to if isinstance(valid_to, date) else None,
        metadata=_json_mapping(metadata),
    )


def _fact_from_row(row: Mapping[str, object]) -> FactEvent:
    from datetime import datetime

    payload = _json_mapping(row.get("payload"))
    observed_at = row["observed_at"]
    ingested_at = row["ingested_at"]
    if not isinstance(observed_at, datetime) or not isinstance(ingested_at, datetime):
        message = "fact row timestamp fields must be datetime instances"
        raise TypeError(message)
    return FactEvent(
        tenant_id=str(row["tenant_id"]),
        source=str(row["source"]),
        entity_ref=EntityRef(
            tenant_id=str(row["tenant_id"]),
            kind=NodeKind(str(row["entity_kind"])),
            id=str(row["entity_id"]),
        ),
        payload=payload,
        observed_at=observed_at,
        ingested_at=ingested_at,
        correlation_id=str(row["correlation_id"]),
    )


def _json_mapping(value: object) -> dict[str, JsonScalar]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, JsonScalar] = {}
    for key, item in value.items():
        if isinstance(key, str) and (item is None or isinstance(item, str | int | float | bool)):
            result[key] = item
    return result


def _float_field(value: object) -> float:
    if isinstance(value, str | int | float):
        return float(value)
    return 0.0


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in vector) + "]"


def _cypher_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
