from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from typing import Protocol

from opentelemetry import trace

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
from core.domain.identity import IdentityLink
from core.domain.writeback import WriteBackAudit, WriteBackStatus

_tracer = trace.get_tracer("openprogram.persistence.graph")

# Cypher relationship labels cannot be bound as parameters, so the AGE sync
# interpolates them. Keeping the mapping here makes it explicit that the only
# possible values are these three literals, never caller-supplied strings.
_AGE_RELATION_BY_EDGE_KIND = {
    EdgeKind.CONTAINS: "CONTAINS",
    EdgeKind.ASSIGNED_TO: "ASSIGNED_TO",
    EdgeKind.DEPENDS_ON: "DEPENDS_ON",
}


class AsyncSqlSession(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class AsyncSqlExecutor(AsyncSqlSession, Protocol):
    def transaction(self) -> AbstractAsyncContextManager[AsyncSqlSession]: ...


class PostgresGraphRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def list_nodes(self, tenant_id: str, kind: NodeKind | None = None) -> list[GraphNode]:
        with _tracer.start_as_current_span("postgres.graph.list_nodes"):
            if kind is None:
                rows = await self._executor.fetch(
                    """
                    SELECT tenant_id, id, kind, name, metadata
                    FROM graph_nodes
                    WHERE tenant_id = %s
                    ORDER BY kind, name, id
                    """,
                    (tenant_id,),
                )
            else:
                rows = await self._executor.fetch(
                    """
                    SELECT tenant_id, id, kind, name, metadata
                    FROM graph_nodes
                    WHERE tenant_id = %s AND kind = %s
                    ORDER BY kind, name, id
                    """,
                    (tenant_id, kind.value),
                )
        return [_node_from_row(row) for row in rows]

    async def get_node(self, tenant_id: str, id: str) -> GraphNode | None:
        with _tracer.start_as_current_span("postgres.graph.get_node"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, id, kind, name, metadata
                FROM graph_nodes
                WHERE tenant_id = %s AND id = %s
                LIMIT 1
                """,
                (tenant_id, id),
            )
        return _node_from_row(rows[0]) if rows else None

    async def upsert_node(self, node: GraphNode) -> None:
        with _tracer.start_as_current_span("postgres.graph.upsert_node"):
            async with self._executor.transaction() as transaction:
                await transaction.execute(
                    """
                    INSERT INTO graph_nodes (tenant_id, id, kind, name, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, id)
                    DO UPDATE SET
                        kind = EXCLUDED.kind,
                        name = EXCLUDED.name,
                        metadata = EXCLUDED.metadata
                    """,
                    (node.tenant_id, node.id, node.kind.value, node.name, dict(node.metadata)),
                )
                await self._sync_age_node(transaction, node)

    async def delete_node(self, tenant_id: str, id: str) -> None:
        with _tracer.start_as_current_span("postgres.graph.delete_node"):
            async with self._executor.transaction() as transaction:
                await transaction.execute(
                    """
                    DELETE FROM graph_edges
                    WHERE tenant_id = %s
                      AND (from_node_id = %s OR to_node_id = %s)
                    """,
                    (tenant_id, id, id),
                )
                await transaction.execute(
                    """
                    DELETE FROM graph_nodes
                    WHERE tenant_id = %s AND id = %s
                    """,
                    (tenant_id, id),
                )
                await self._sync_age_delete_node(transaction, tenant_id, id)

    async def add_edge(self, edge: GraphEdge) -> None:
        with _tracer.start_as_current_span("postgres.graph.add_edge"):
            async with self._executor.transaction() as transaction:
                await transaction.execute(
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
                await self._sync_age_edge(transaction, edge)

    async def list_edges(
        self,
        tenant_id: str,
        from_node_id: str | None = None,
        to_node_id: str | None = None,
        kind: EdgeKind | None = None,
    ) -> list[GraphEdge]:
        clauses = ["tenant_id = %s"]
        params: list[object] = [tenant_id]
        if from_node_id is not None:
            clauses.append("from_node_id = %s")
            params.append(from_node_id)
        if to_node_id is not None:
            clauses.append("to_node_id = %s")
            params.append(to_node_id)
        if kind is not None:
            clauses.append("kind = %s")
            params.append(kind.value)
        query = f"""
            SELECT tenant_id, from_node_id, to_node_id, kind, valid_from, valid_to, metadata
            FROM graph_edges
            WHERE {" AND ".join(clauses)}
            ORDER BY from_node_id, to_node_id, kind, valid_from NULLS FIRST, valid_to NULLS LAST
        """
        with _tracer.start_as_current_span("postgres.graph.list_edges"):
            rows = await self._executor.fetch(query, tuple(params))
        return [_edge_from_row(row) for row in rows]

    async def remove_edge(self, edge: GraphEdge) -> None:
        with _tracer.start_as_current_span("postgres.graph.remove_edge"):
            async with self._executor.transaction() as transaction:
                await transaction.execute(
                    """
                    DELETE FROM graph_edges
                    WHERE tenant_id = %s
                      AND from_node_id = %s
                      AND to_node_id = %s
                      AND kind = %s
                      AND valid_from IS NOT DISTINCT FROM %s
                      AND valid_to IS NOT DISTINCT FROM %s
                      AND metadata = %s
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
                await self._sync_age_remove_edge(transaction, edge)

    async def get_program_tree(self, tenant_id: str, program_id: str, as_of: date) -> GraphTree:
        with _tracer.start_as_current_span("postgres.graph.get_program_tree"):
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

        with _tracer.start_as_current_span("postgres.graph.get_program_tree_edges"):
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
        with _tracer.start_as_current_span("postgres.graph.active_developer_memberships"):
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

    async def pods_containing_developer(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> list[GraphNode]:
        with _tracer.start_as_current_span("postgres.graph.pods_containing_developer"):
            rows = await self._executor.fetch(
                """
                SELECT n.tenant_id, n.id, n.kind, n.name, n.metadata
                FROM graph_edges e
                JOIN graph_nodes n
                  ON n.tenant_id = e.tenant_id AND n.id = e.from_node_id
                WHERE e.tenant_id = %s
                  AND e.to_node_id = %s
                  AND e.kind = 'contains'
                  AND n.kind = 'pod'
                  AND (e.valid_from IS NULL OR e.valid_from <= %s)
                  AND (e.valid_to IS NULL OR e.valid_to > %s)
                ORDER BY n.id
                """,
                (tenant_id, developer_id, as_of, as_of),
            )
            return [_node_from_row(row) for row in rows]

    async def pods_for_task(self, tenant_id: str, task_id: str, as_of: date) -> list[GraphNode]:
        """Pods owning a task/work item, via direct containment or a workstream.

        The target id may be a TASK node id (Jira issue key) or a WORK_ITEM
        node id — the query never filters the target's kind, only that the
        resolved ancestors are pods.
        """
        with _tracer.start_as_current_span("postgres.graph.pods_for_task"):
            rows = await self._executor.fetch(
                """
                SELECT DISTINCT n.tenant_id, n.id, n.kind, n.name, n.metadata
                FROM graph_nodes n
                JOIN (
                    SELECT e.from_node_id AS pod_id
                    FROM graph_edges e
                    WHERE e.tenant_id = %s AND e.to_node_id = %s AND e.kind = 'contains'
                      AND (e.valid_from IS NULL OR e.valid_from <= %s)
                      AND (e.valid_to IS NULL OR e.valid_to > %s)
                  UNION
                    SELECT pw.from_node_id
                    FROM graph_edges wt
                    JOIN graph_edges pw
                      ON pw.tenant_id = wt.tenant_id
                     AND pw.to_node_id = wt.from_node_id
                     AND pw.kind = 'assigned_to'
                    WHERE wt.tenant_id = %s AND wt.to_node_id = %s AND wt.kind = 'contains'
                      AND (wt.valid_from IS NULL OR wt.valid_from <= %s)
                      AND (wt.valid_to IS NULL OR wt.valid_to > %s)
                      AND (pw.valid_from IS NULL OR pw.valid_from <= %s)
                      AND (pw.valid_to IS NULL OR pw.valid_to > %s)
                ) pods ON n.id = pods.pod_id
                WHERE n.tenant_id = %s AND n.kind = 'pod'
                ORDER BY n.id
                """,
                (
                    tenant_id,
                    task_id,
                    as_of,
                    as_of,
                    tenant_id,
                    task_id,
                    as_of,
                    as_of,
                    as_of,
                    as_of,
                    tenant_id,
                ),
            )
            return [_node_from_row(row) for row in rows]

    async def get_identity_link(self, tenant_id: str, developer_id: str) -> IdentityLink | None:
        with _tracer.start_as_current_span("postgres.graph.get_identity_link"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, developer_id, chat_user_id, jira_account_id,
                       jira_email, vcs_username
                FROM identity_links
                WHERE tenant_id = %s AND developer_id = %s
                LIMIT 1
                """,
                (tenant_id, developer_id),
            )
        return _identity_link_from_row(rows[0]) if rows else None

    async def upsert_identity_link(self, link: IdentityLink) -> None:
        with _tracer.start_as_current_span("postgres.graph.upsert_identity_link"):
            await self._executor.execute(
                """
                INSERT INTO identity_links (
                    tenant_id, developer_id, chat_user_id, jira_account_id,
                    jira_email, vcs_username
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, developer_id)
                DO UPDATE SET
                    chat_user_id = EXCLUDED.chat_user_id,
                    jira_account_id = EXCLUDED.jira_account_id,
                    jira_email = EXCLUDED.jira_email,
                    vcs_username = EXCLUDED.vcs_username,
                    updated_at = now()
                """,
                (
                    link.tenant_id,
                    link.developer_id,
                    link.chat_user_id,
                    link.jira_account_id,
                    link.jira_email,
                    link.vcs_username,
                ),
            )

    async def list_identity_links(self, tenant_id: str) -> list[IdentityLink]:
        with _tracer.start_as_current_span("postgres.graph.list_identity_links"):
            rows = await self._executor.fetch(
                """
                SELECT tenant_id, developer_id, chat_user_id, jira_account_id,
                       jira_email, vcs_username
                FROM identity_links
                WHERE tenant_id = %s
                ORDER BY developer_id
                """,
                (tenant_id,),
            )
        return [_identity_link_from_row(row) for row in rows]

    async def get_writeback_enabled(self, tenant_id: str) -> bool | None:
        with _tracer.start_as_current_span("postgres.graph.get_writeback_enabled"):
            rows = await self._executor.fetch(
                """
                SELECT enabled
                FROM writeback_config
                WHERE tenant_id = %s
                LIMIT 1
                """,
                (tenant_id,),
            )
        if not rows:
            return None
        value = rows[0].get("enabled")
        return bool(value) if value is not None else None

    async def set_writeback_enabled(self, tenant_id: str, enabled: bool) -> None:
        with _tracer.start_as_current_span("postgres.graph.set_writeback_enabled"):
            await self._executor.execute(
                """
                INSERT INTO writeback_config (tenant_id, enabled)
                VALUES (%s, %s)
                ON CONFLICT (tenant_id)
                DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now()
                """,
                (tenant_id, enabled),
            )

    async def record(self, audit: WriteBackAudit) -> None:
        with _tracer.start_as_current_span("postgres.graph.record_writeback_audit"):
            await self._executor.execute(
                """
                INSERT INTO writeback_audit (
                    id, tenant_id, developer_id, issue_key, correlation_id,
                    status, target_state, before_state, after_state, comment,
                    source, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    audit.id,
                    audit.tenant_id,
                    audit.developer_id,
                    audit.issue_key,
                    audit.correlation_id,
                    audit.status.value,
                    audit.target_state,
                    audit.before_state,
                    audit.after_state,
                    audit.comment,
                    audit.source,
                    audit.created_at,
                ),
            )

    async def list_for_issue(self, tenant_id: str, issue_key: str) -> list[WriteBackAudit]:
        with _tracer.start_as_current_span("postgres.graph.list_writeback_audit"):
            rows = await self._executor.fetch(
                """
                SELECT id, tenant_id, developer_id, issue_key, correlation_id,
                       status, target_state, before_state, after_state, comment,
                       source, created_at
                FROM writeback_audit
                WHERE tenant_id = %s AND issue_key = %s
                ORDER BY created_at
                """,
                (tenant_id, issue_key),
            )
        return [_writeback_audit_from_row(row) for row in rows]

    async def list_writeback_by_correlation(
        self, tenant_id: str, correlation_id: str
    ) -> list[WriteBackAudit]:
        with _tracer.start_as_current_span("postgres.graph.list_writeback_by_correlation"):
            rows = await self._executor.fetch(
                """
                SELECT id, tenant_id, developer_id, issue_key, correlation_id,
                       status, target_state, before_state, after_state, comment,
                       source, created_at
                FROM writeback_audit
                WHERE tenant_id = %s AND correlation_id = %s
                ORDER BY created_at
                """,
                (tenant_id, correlation_id),
            )
        return [_writeback_audit_from_row(row) for row in rows]

    async def find_existing(
        self,
        tenant_id: str,
        issue_key: str,
        target_state: str,
        correlation_id: str,
    ) -> WriteBackAudit | None:
        with _tracer.start_as_current_span("postgres.graph.find_writeback_audit"):
            rows = await self._executor.fetch(
                """
                SELECT id, tenant_id, developer_id, issue_key, correlation_id,
                       status, target_state, before_state, after_state, comment,
                       source, created_at
                FROM writeback_audit
                WHERE tenant_id = %s AND issue_key = %s
                      AND target_state = %s AND correlation_id = %s
                ORDER BY created_at
                LIMIT 1
                """,
                (tenant_id, issue_key, target_state, correlation_id),
            )
        return _writeback_audit_from_row(rows[0]) if rows else None

    async def get_writeback_audit(self, tenant_id: str, audit_id: str) -> WriteBackAudit | None:
        with _tracer.start_as_current_span("postgres.graph.get_writeback_audit"):
            rows = await self._executor.fetch(
                """
                SELECT id, tenant_id, developer_id, issue_key, correlation_id,
                       status, target_state, before_state, after_state, comment,
                       source, created_at
                FROM writeback_audit
                WHERE tenant_id = %s AND id = %s
                LIMIT 1
                """,
                (tenant_id, audit_id),
            )
        return _writeback_audit_from_row(rows[0]) if rows else None

    async def count_applied_writebacks(self, tenant_id: str, since: datetime | None = None) -> int:
        with _tracer.start_as_current_span("postgres.graph.count_applied_writebacks"):
            rows = await self._executor.fetch(
                """
                SELECT COUNT(*)::int AS applied_count
                FROM writeback_audit
                WHERE tenant_id = %s AND status = %s
                      AND (%s IS NULL OR created_at >= %s)
                """,
                (tenant_id, WriteBackStatus.APPLIED.value, since, since),
            )
        return _int_value(rows[0].get("applied_count")) if rows else 0

    async def list_applied_writebacks(
        self, tenant_id: str, limit: int, since: datetime | None = None
    ) -> list[WriteBackAudit]:
        with _tracer.start_as_current_span("postgres.graph.list_applied_writebacks"):
            rows = await self._executor.fetch(
                """
                SELECT id, tenant_id, developer_id, issue_key, correlation_id,
                       status, target_state, before_state, after_state, comment,
                       source, created_at
                FROM writeback_audit
                WHERE tenant_id = %s AND status = %s
                      AND (%s IS NULL OR created_at >= %s)
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (tenant_id, WriteBackStatus.APPLIED.value, since, since, limit),
            )
        return [_writeback_audit_from_row(row) for row in rows]

    async def _sync_age_node(self, session: AsyncSqlSession, node: GraphNode) -> None:
        with _tracer.start_as_current_span("postgres.age.sync_node"):
            await _prepare_age_session(session)
            await session.execute(
                """
                SELECT *
                FROM cypher('openprogram_graph', $$
                    MERGE (n:GraphNode {tenant_id: $tenant_id, id: $id})
                    SET n.kind = $kind,
                        n.name = $name
                    RETURN n
                $$, %s) AS (n agtype)
                """,
                (
                    _age_params(
                        tenant_id=node.tenant_id,
                        id=node.id,
                        kind=node.kind.value,
                        name=node.name,
                    ),
                ),
            )

    async def _sync_age_edge(self, session: AsyncSqlSession, edge: GraphEdge) -> None:
        relation = _AGE_RELATION_BY_EDGE_KIND[edge.kind]
        with _tracer.start_as_current_span("postgres.age.sync_edge"):
            await _prepare_age_session(session)
            await session.execute(
                # `relation` is a Cypher label, which cannot be a bound parameter. It comes
                # from a closed EdgeKind -> literal map, never from caller input.
                f"""
                SELECT *
                FROM cypher('openprogram_graph', $$
                    MATCH (from_node:GraphNode {{
                        tenant_id: $tenant_id,
                        id: $from_node_id
                    }})
                    MATCH (to_node:GraphNode {{
                        tenant_id: $tenant_id,
                        id: $to_node_id
                    }})
                    CREATE (from_node)-[edge:{relation} {{
                        kind: $kind,
                        valid_from: $valid_from,
                        valid_to: $valid_to
                    }}]->(to_node)
                    RETURN edge
                $$, %s) AS (edge agtype)
                """,
                (
                    _age_params(
                        tenant_id=edge.tenant_id,
                        from_node_id=edge.from_node_id,
                        to_node_id=edge.to_node_id,
                        kind=edge.kind.value,
                        valid_from=edge.valid_from.isoformat() if edge.valid_from else None,
                        valid_to=edge.valid_to.isoformat() if edge.valid_to else None,
                    ),
                ),
            )

    async def _sync_age_delete_node(
        self, session: AsyncSqlSession, tenant_id: str, id: str
    ) -> None:
        with _tracer.start_as_current_span("postgres.age.delete_node"):
            await _prepare_age_session(session)
            await session.execute(
                """
                SELECT *
                FROM cypher('openprogram_graph', $$
                    MATCH (n:GraphNode {tenant_id: $tenant_id, id: $id})
                    DETACH DELETE n
                    RETURN 1
                $$, %s) AS (n agtype)
                """,
                (_age_params(tenant_id=tenant_id, id=id),),
            )

    async def _sync_age_remove_edge(self, session: AsyncSqlSession, edge: GraphEdge) -> None:
        relation = _AGE_RELATION_BY_EDGE_KIND[edge.kind]
        with _tracer.start_as_current_span("postgres.age.remove_edge"):
            await _prepare_age_session(session)
            await session.execute(
                # See _sync_age_edge: the label is a fixed literal, values are bound.
                f"""
                SELECT *
                FROM cypher('openprogram_graph', $$
                    MATCH (from_node:GraphNode {{
                        tenant_id: $tenant_id,
                        id: $from_node_id
                    }})-[edge:{relation}]->(to_node:GraphNode {{
                        tenant_id: $tenant_id,
                        id: $to_node_id
                    }})
                    DELETE edge
                    RETURN 1
                $$, %s) AS (edge agtype)
                """,
                (
                    _age_params(
                        tenant_id=edge.tenant_id,
                        from_node_id=edge.from_node_id,
                        to_node_id=edge.to_node_id,
                    ),
                ),
            )


class PostgresTimeSeriesRepository:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def append_fact(self, fact: FactEvent) -> None:
        with _tracer.start_as_current_span("postgres.timeseries.append_fact"):
            await self._executor.execute(
                """
                INSERT INTO facts (
                    tenant_id, source, entity_kind, entity_id, payload,
                    observed_at, ingested_at, correlation_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
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

    async def append_fact_once(self, fact: FactEvent) -> None:
        await self.append_fact(fact)

    async def list_facts(
        self,
        tenant_id: str,
        entity_ref: EntityRef,
        since: datetime | None = None,
    ) -> list[FactEvent]:
        with _tracer.start_as_current_span("postgres.timeseries.list_facts"):
            if since is not None:
                rows = await self._executor.fetch(
                    """
                    SELECT tenant_id, source, entity_kind, entity_id, payload, observed_at,
                           ingested_at, correlation_id
                    FROM facts
                    WHERE tenant_id = %s AND entity_kind = %s AND entity_id = %s
                      AND observed_at >= %s
                    ORDER BY observed_at
                    """,
                    (tenant_id, entity_ref.kind.value, entity_ref.id, since),
                )
            else:
                rows = await self._executor.fetch(
                    """
                    SELECT tenant_id, source, entity_kind, entity_id, payload, observed_at,
                           ingested_at, correlation_id
                    FROM facts
                    WHERE tenant_id = %s AND entity_kind = %s AND entity_id = %s
                    ORDER BY observed_at
                    """,
                    (tenant_id, entity_ref.kind.value, entity_ref.id),
                )
            return [_fact_from_row(row) for row in rows]

    async def list_recent_facts(
        self,
        tenant_id: str,
        since: datetime | None = None,
        sources: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[FactEvent]:
        if limit <= 0:
            return []
        clauses = ["tenant_id = %s"]
        params: list[object] = [tenant_id]
        if since is not None:
            clauses.append("observed_at >= %s")
            params.append(since)
        if sources is not None:
            if not sources:
                return []
            placeholders = ", ".join(["%s"] * len(sources))
            clauses.append(f"source IN ({placeholders})")
            params.extend(sources)
        params.append(limit)
        with _tracer.start_as_current_span("postgres.timeseries.list_recent_facts"):
            rows = await self._executor.fetch(
                f"""
                SELECT tenant_id, source, entity_kind, entity_id, payload, observed_at,
                       ingested_at, correlation_id
                FROM facts
                WHERE {" AND ".join(clauses)}
                ORDER BY observed_at DESC, ingested_at DESC, correlation_id DESC
                LIMIT %s
                """,
                tuple(params),
            )
        return [_fact_from_row(row) for row in rows]


class PostgresVectorStore:
    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def upsert_embedding(
        self, tenant_id: str, entity_ref: EntityRef, vector: Sequence[float]
    ) -> None:
        with _tracer.start_as_current_span("postgres.vector.upsert_embedding"):
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
        with _tracer.start_as_current_span("postgres.vector.search"):
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


async def _prepare_age_session(session: AsyncSqlSession) -> None:
    await session.execute("LOAD 'age'")
    await session.execute('SET LOCAL search_path = ag_catalog, "$user", public')


def _age_params(**values: str | None) -> str:
    """Serialize Cypher parameters for the ``cypher()`` third argument.

    AGE requires that argument to be a real bind parameter, so values travel as
    a JSON document rather than being interpolated into the query text. This is
    what keeps caller-supplied ids and names off the Cypher parse path.
    """
    return json.dumps(values)


def _node_from_row(row: Mapping[str, object]) -> GraphNode:
    metadata = row.get("metadata")
    return GraphNode(
        tenant_id=str(row["tenant_id"]),
        id=str(row["id"]),
        kind=NodeKind(str(row["kind"])),
        name=str(row["name"]),
        metadata=_json_mapping(metadata),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int_value(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GraphNotFound("expected an integer aggregate value")
    return value


def _identity_link_from_row(row: Mapping[str, object]) -> IdentityLink:
    return IdentityLink(
        tenant_id=str(row["tenant_id"]),
        developer_id=str(row["developer_id"]),
        chat_user_id=_optional_str(row.get("chat_user_id")),
        jira_account_id=_optional_str(row.get("jira_account_id")),
        jira_email=_optional_str(row.get("jira_email")),
        vcs_username=_optional_str(row.get("vcs_username")),
    )


def _writeback_audit_from_row(row: Mapping[str, object]) -> WriteBackAudit:
    created_at = row["created_at"]
    if not isinstance(created_at, datetime):
        raise GraphNotFound("writeback_audit row missing created_at")
    return WriteBackAudit(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        developer_id=str(row["developer_id"]),
        issue_key=str(row["issue_key"]),
        correlation_id=str(row["correlation_id"]),
        status=WriteBackStatus(str(row["status"])),
        target_state=str(row["target_state"]),
        before_state=_optional_str(row.get("before_state")),
        after_state=_optional_str(row.get("after_state")),
        comment=_optional_str(row.get("comment")),
        source=str(row["source"]),
        created_at=created_at,
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
