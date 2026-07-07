"""Add developer confirmation metadata to statuses."""

from __future__ import annotations

from alembic import op

revision = "0017_developer_status_confirmation"
down_revision = "0016_last_accessed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE developer_statuses
        ADD COLUMN IF NOT EXISTS developer_confirmed BOOLEAN NOT NULL DEFAULT false;
        """
    )
    op.execute(
        """
        ALTER TABLE developer_statuses
        ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE developer_statuses DROP COLUMN IF EXISTS confirmed_at;")
    op.execute("ALTER TABLE developer_statuses DROP COLUMN IF EXISTS developer_confirmed;")
