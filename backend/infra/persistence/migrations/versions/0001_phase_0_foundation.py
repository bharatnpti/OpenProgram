"""Phase 0 foundation schema."""

from __future__ import annotations

import os

from alembic import op

revision = "0001_phase_0_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    embedding_dimension = _embedding_dimension()
    vector_type = f"vector({embedding_dimension})"
    op.execute("CREATE EXTENSION IF NOT EXISTS age;")
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("LOAD 'age';")
    op.execute('SET search_path = ag_catalog, "$user", public;')
    op.execute(
        """
        SELECT create_graph('pulseops_graph')
        WHERE NOT EXISTS (
            SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'pulseops_graph'
        );
        """
    )
    op.execute("SET search_path = public;")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_nodes (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('program', 'project', 'pod', 'developer', 'task')),
            name TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_edges (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            from_node_id TEXT NOT NULL,
            to_node_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('contains', 'assigned_to', 'depends_on')),
            valid_from DATE,
            valid_to DATE,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, from_node_id) REFERENCES graph_nodes (tenant_id, id),
            FOREIGN KEY (tenant_id, to_node_id) REFERENCES graph_nodes (tenant_id, id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS graph_edges_from_idx
        ON graph_edges (tenant_id, from_node_id, kind, valid_from, valid_to);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS facts (
            id BIGSERIAL,
            tenant_id TEXT NOT NULL,
            source TEXT NOT NULL,
            entity_kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            payload JSONB NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            correlation_id TEXT NOT NULL,
            PRIMARY KEY (id, observed_at)
        );
        """
    )
    op.execute("SELECT create_hypertable('facts', 'observed_at', if_not_exists => TRUE);")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_items (
            tenant_id TEXT NOT NULL,
            entity_kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            embedding __VECTOR_TYPE__ NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, entity_kind, entity_id)
        );
        """.replace("__VECTOR_TYPE__", vector_type)
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS vector_items_embedding_hnsw_idx
        ON vector_items USING hnsw (embedding vector_cosine_ops);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_secrets (
            tenant_id TEXT NOT NULL,
            connector TEXT NOT NULL,
            key TEXT NOT NULL,
            ciphertext BYTEA NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, connector, key)
        );
        """
    )


def downgrade() -> None:
    op.execute("LOAD 'age';")
    op.execute('SET search_path = ag_catalog, "$user", public;')
    op.execute(
        """
        SELECT drop_graph('pulseops_graph', true)
        WHERE EXISTS (
            SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'pulseops_graph'
        );
        """
    )
    op.execute("SET search_path = public;")
    op.execute("DROP TABLE IF EXISTS connector_secrets;")
    op.execute("DROP TABLE IF EXISTS vector_items;")
    op.execute("DROP TABLE IF EXISTS facts;")
    op.execute("DROP TABLE IF EXISTS graph_edges;")
    op.execute("DROP TABLE IF EXISTS graph_nodes;")


def _embedding_dimension() -> int:
    raw_value = os.getenv("PULSEOPS_EMBEDDING_DIMENSION", "1536")
    try:
        dimension = int(raw_value)
    except ValueError as exc:
        raise ValueError("PULSEOPS_EMBEDDING_DIMENSION must be an integer") from exc
    if dimension <= 0:
        raise ValueError("PULSEOPS_EMBEDDING_DIMENSION must be positive")
    return dimension
