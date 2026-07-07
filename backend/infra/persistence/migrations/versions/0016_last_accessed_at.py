"""Add last-access tracking for raw conversation retention."""

from __future__ import annotations

from alembic import op

revision = "0016_last_accessed_at"
down_revision = "0015_openprogram_age_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE conversation_turns
        ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ;
        """
    )
    op.execute(
        """
        UPDATE conversation_turns
        SET last_accessed_at = observed_at
        WHERE last_accessed_at IS NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE checkins
        ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ;
        """
    )
    op.execute(
        """
        UPDATE checkins
        SET last_accessed_at = COALESCE(replied_at, asked_at)
        WHERE last_accessed_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE checkins DROP COLUMN IF EXISTS last_accessed_at;")
    op.execute("ALTER TABLE conversation_turns DROP COLUMN IF EXISTS last_accessed_at;")
