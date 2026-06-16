"""Phase 0 foundation schema."""

from __future__ import annotations

from alembic import op

revision = "0001_phase_0_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS age;
        EXCEPTION WHEN undefined_file THEN
            RAISE NOTICE 'Apache AGE extension unavailable';
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS timescaledb;
        EXCEPTION WHEN undefined_file THEN
            RAISE NOTICE 'TimescaleDB extension unavailable';
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS vector;
        EXCEPTION WHEN undefined_file THEN
            RAISE NOTICE 'pgvector extension unavailable';
        END $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_nodes (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
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
            kind TEXT NOT NULL,
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
    op.execute(
        """
        DO $$
        BEGIN
            PERFORM create_hypertable('facts', 'observed_at', if_not_exists => TRUE);
        EXCEPTION WHEN undefined_function THEN
            RAISE NOTICE 'TimescaleDB create_hypertable unavailable';
        END $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_items (
            tenant_id TEXT NOT NULL,
            entity_kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            embedding DOUBLE PRECISION[] NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, entity_kind, entity_id)
        );
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
    op.execute("DROP TABLE IF EXISTS connector_secrets;")
    op.execute("DROP TABLE IF EXISTS vector_items;")
    op.execute("DROP TABLE IF EXISTS facts;")
    op.execute("DROP TABLE IF EXISTS graph_edges;")
    op.execute("DROP TABLE IF EXISTS graph_nodes;")
