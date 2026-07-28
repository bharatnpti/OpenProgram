"""Add developer-local checkin_date to checkins for timezone-correct rosters."""

from __future__ import annotations

from alembic import op

revision = "0020_checkin_local_date"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0019_inbound_chat_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE checkins ADD COLUMN IF NOT EXISTS checkin_date DATE;")
    # Backfill existing rows from the UTC calendar date of asked_at. Historical
    # rows predate per-developer local-date capture; UTC is an acceptable
    # fallback and matches the prior roster behaviour for those rows. New rows
    # persist the developer-local date computed by the check-in workflow.
    op.execute(
        """
        UPDATE checkins
        SET checkin_date = (asked_at AT TIME ZONE 'UTC')::date
        WHERE checkin_date IS NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS checkins_tenant_developer_checkin_date_idx
        ON checkins (tenant_id, developer_id, checkin_date);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS checkins_tenant_developer_checkin_date_idx;")
    op.execute("ALTER TABLE checkins DROP COLUMN IF EXISTS checkin_date;")
