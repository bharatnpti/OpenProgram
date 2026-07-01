"""Drop per-reply developer status mood."""

from __future__ import annotations

from alembic import op

revision = "0013_drop_developer_status_mood"
down_revision = "0012_work_item_node_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE developer_statuses
        DROP COLUMN IF EXISTS mood;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE developer_statuses
        ADD COLUMN IF NOT EXISTS mood TEXT;
        """
    )
