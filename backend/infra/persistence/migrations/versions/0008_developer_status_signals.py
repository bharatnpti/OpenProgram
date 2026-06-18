"""Persist parsed developer status signals."""

from __future__ import annotations

from alembic import op

revision = "0008_developer_status_signals"
down_revision = "0007_conversation_user_turn_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE developer_statuses
        ADD COLUMN IF NOT EXISTS eta_change_days INTEGER;
        """
    )
    op.execute(
        """
        ALTER TABLE developer_statuses
        ADD COLUMN IF NOT EXISTS mood TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE developer_statuses
        DROP COLUMN IF EXISTS mood;
        """
    )
    op.execute(
        """
        ALTER TABLE developer_statuses
        DROP COLUMN IF EXISTS eta_change_days;
        """
    )
