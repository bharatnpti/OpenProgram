"""Add searchable directory users."""

from __future__ import annotations

from alembic import op

revision = "0009_directory_users"
down_revision = "0008_developer_status_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS directory_users (
            tenant_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            email TEXT,
            handle TEXT,
            avatar_url TEXT,
            title TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            source TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            synced_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, external_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS directory_users_display_name_trgm_idx
        ON directory_users USING GIN (display_name gin_trgm_ops);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS directory_users_email_trgm_idx
        ON directory_users USING GIN (email gin_trgm_ops);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS directory_users_email_trgm_idx;")
    op.execute("DROP INDEX IF EXISTS directory_users_display_name_trgm_idx;")
    op.execute("DROP TABLE IF EXISTS directory_users;")
