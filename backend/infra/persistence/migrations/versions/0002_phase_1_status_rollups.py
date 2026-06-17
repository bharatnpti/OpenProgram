"""Phase 1 status, rollup, and sync cursor schema."""

from __future__ import annotations

from alembic import op

revision = "0002_phase_1_status_rollups"
down_revision = "0001_phase_0_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkins (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            developer_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            asked_at TIMESTAMPTZ NOT NULL,
            replied_at TIMESTAMPTZ,
            raw_reply TEXT,
            signals JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, correlation_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS checkins_tenant_developer_asked_idx
        ON checkins (tenant_id, developer_id, asked_at DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS checkins_tenant_developer_replied_idx
        ON checkins (tenant_id, developer_id, replied_at DESC);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS developer_statuses (
            tenant_id TEXT NOT NULL,
            developer_id TEXT NOT NULL,
            as_of DATE NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('confirmed', 'inferred', 'stale', 'unknown')),
            blockers JSONB NOT NULL DEFAULT '{"items": []}'::jsonb,
            summary TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, developer_id, as_of)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS developer_statuses_latest_idx
        ON developer_statuses (tenant_id, developer_id, as_of DESC);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS node_statuses (
            tenant_id TEXT NOT NULL,
            entity_kind TEXT NOT NULL CHECK (
                entity_kind IN ('program', 'project', 'pod', 'developer', 'task')
            ),
            entity_id TEXT NOT NULL,
            as_of DATE NOT NULL,
            rag TEXT NOT NULL CHECK (rag IN ('green', 'amber', 'red', 'unknown')),
            source TEXT NOT NULL CHECK (source IN ('confirmed', 'inferred', 'stale', 'unknown')),
            factors JSONB NOT NULL DEFAULT '{"items": []}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, entity_kind, entity_id, as_of)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS node_statuses_latest_idx
        ON node_statuses (tenant_id, entity_kind, entity_id, as_of DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS node_statuses_tenant_as_of_idx
        ON node_statuses (tenant_id, as_of DESC);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_sync_cursors (
            tenant_id TEXT NOT NULL,
            connector TEXT NOT NULL,
            scope TEXT NOT NULL,
            cursor_value TEXT,
            cursor_updated_at TIMESTAMPTZ,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, connector, scope)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS connector_sync_cursors_tenant_connector_idx
        ON connector_sync_cursors (tenant_id, connector);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_sync_cursors;")
    op.execute("DROP TABLE IF EXISTS node_statuses;")
    op.execute("DROP TABLE IF EXISTS developer_statuses;")
    op.execute("DROP TABLE IF EXISTS checkins;")
