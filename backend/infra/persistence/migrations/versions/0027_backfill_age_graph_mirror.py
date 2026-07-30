"""Rebuild the Apache AGE graph mirror from the relational graph tables.

``0026_restore_vector_and_age_graph`` recreated ``openprogram_graph`` as an
*empty* graph. ``PostgresGraphRepository`` only writes to the mirror on
node/edge mutations, so without a backfill the mirror reflects nothing that
existed before the restore -- a database with rows in ``graph_nodes`` would show
an empty graph until every node happened to be touched again.

Nothing reads the mirror today (all graph reads use the relational
``graph_nodes``/``graph_edges`` recursive CTE), so this is a latent-correctness
fix rather than a functional one.

Idempotency: the mirror is **cleared and rebuilt** from the relational tables
rather than merged into. Cypher ``MERGE`` on a relationship cannot be used for
this: AGE omits properties whose value is null when writing, so an edge stored
with ``valid_to`` absent never matches a ``MERGE`` pattern that specifies
``valid_to: null``, and re-running would duplicate every open-ended edge
(verified against AGE 1.5 -- two identical MERGE calls produced two edges).
Rebuilding from the relational source of truth is idempotent by construction and
does not depend on MERGE semantics.
"""

from __future__ import annotations

from alembic import op

revision = "0027_backfill_age_graph_mirror"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0026_restore_vector_and_age_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # LOAD and search_path must be set outside the DO block: the block's DECLARE
    # section is parsed before its body runs, so `agtype` has to already resolve.
    op.execute("LOAD 'age';")
    op.execute('SET search_path = ag_catalog, "$user", public;')
    op.execute(
        """
        DO $do$
        DECLARE
            node_row record;
            edge_row record;
            relation text;
            params ag_catalog.agtype;
        BEGIN
            -- Clear the mirror so the rebuild is idempotent. DETACH DELETE drops
            -- each vertex together with its relationships.
            EXECUTE $q$
                SELECT * FROM cypher('openprogram_graph', $c$
                    MATCH (n:GraphNode) DETACH DELETE n RETURN 1
                $c$) AS (cleared agtype)
            $q$;

            FOR node_row IN
                SELECT tenant_id, id, kind, name FROM graph_nodes
            LOOP
                params := (
                    json_build_object(
                        'tenant_id', node_row.tenant_id,
                        'id', node_row.id,
                        'kind', node_row.kind,
                        'name', node_row.name
                    )::text
                )::ag_catalog.agtype;
                EXECUTE $q$
                    SELECT * FROM cypher('openprogram_graph', $c$
                        MERGE (n:GraphNode {tenant_id: $tenant_id, id: $id})
                        SET n.kind = $kind,
                            n.name = $name
                        RETURN n
                    $c$, $1) AS (n agtype)
                $q$ USING params;
            END LOOP;

            FOR edge_row IN
                SELECT tenant_id, from_node_id, to_node_id, kind, valid_from, valid_to
                FROM graph_edges
            LOOP
                -- Mirrors _AGE_RELATION_BY_EDGE_KIND in postgres_graph.py. A kind
                -- outside the closed set is a data error, not something to guess at.
                relation := CASE edge_row.kind
                    WHEN 'contains' THEN 'CONTAINS'
                    WHEN 'assigned_to' THEN 'ASSIGNED_TO'
                    WHEN 'depends_on' THEN 'DEPENDS_ON'
                END;
                IF relation IS NULL THEN
                    RAISE EXCEPTION 'unknown graph_edges.kind %', edge_row.kind;
                END IF;

                params := (
                    json_build_object(
                        'tenant_id', edge_row.tenant_id,
                        'from_node_id', edge_row.from_node_id,
                        'to_node_id', edge_row.to_node_id,
                        'kind', edge_row.kind,
                        'valid_from', to_char(edge_row.valid_from, 'YYYY-MM-DD'),
                        'valid_to', to_char(edge_row.valid_to, 'YYYY-MM-DD')
                    )::text
                )::ag_catalog.agtype;
                -- `relation` is a Cypher label, which cannot be bound; it comes from
                -- the CASE above, never from caller input. Values stay bound.
                EXECUTE format($q$
                    SELECT * FROM cypher('openprogram_graph', $c$
                        MATCH (from_node:GraphNode {tenant_id: $tenant_id, id: $from_node_id})
                        MATCH (to_node:GraphNode {tenant_id: $tenant_id, id: $to_node_id})
                        CREATE (from_node)-[edge:%s {
                            kind: $kind,
                            valid_from: $valid_from,
                            valid_to: $valid_to
                        }]->(to_node)
                        RETURN edge
                    $c$, $1) AS (edge agtype)
                $q$, relation) USING params;
            END LOOP;
        END
        $do$;
        """
    )


def downgrade() -> None:
    """Empty the mirror again, returning it to the post-0026 state.

    The relational tables are the source of truth and are untouched here, so the
    only thing to undo is the mirrored copy.
    """
    op.execute(
        """
        DO $do$
        BEGIN
            EXECUTE 'LOAD ''age''';
            PERFORM set_config('search_path', 'ag_catalog, "$user", public', true);
            EXECUTE $q$
                SELECT * FROM cypher('openprogram_graph', $c$
                    MATCH (n:GraphNode) DETACH DELETE n RETURN 1
                $c$) AS (cleared agtype)
            $q$;
        EXCEPTION
            WHEN OTHERS THEN
                -- AGE absent or graph already gone; nothing to clear.
                NULL;
        END
        $do$;
        """
    )
