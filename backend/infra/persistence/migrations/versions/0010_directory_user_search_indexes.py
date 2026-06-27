"""Add directory user search indexes."""

from __future__ import annotations

from alembic import op

revision = "0010_directory_user_search_indexes"
down_revision = "0009_directory_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS directory_users_active_listing_idx
        ON directory_users (tenant_id, display_name, external_id)
        WHERE is_active = TRUE;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS directory_users_handle_trgm_idx
        ON directory_users USING GIN (handle gin_trgm_ops)
        WHERE handle IS NOT NULL AND is_active = TRUE;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS directory_users_external_id_trgm_idx
        ON directory_users USING GIN (external_id gin_trgm_ops)
        WHERE is_active = TRUE;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS directory_users_external_id_trgm_idx;")
    op.execute("DROP INDEX IF EXISTS directory_users_handle_trgm_idx;")
    op.execute("DROP INDEX IF EXISTS directory_users_active_listing_idx;")
