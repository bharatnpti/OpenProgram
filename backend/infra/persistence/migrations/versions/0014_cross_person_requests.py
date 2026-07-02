"""Create cross-person request lifecycle table."""

from __future__ import annotations

from alembic import op

revision = "0014_cross_person_requests"
down_revision = "0013_drop_developer_status_mood"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cross_person_requests (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            requester_chat_ref TEXT,
            counterpart_id TEXT,
            kind TEXT NOT NULL,
            note TEXT NOT NULL,
            task_kind TEXT,
            task_id TEXT,
            source_correlation_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            raw_name TEXT,
            email TEXT,
            counterpart_display_name TEXT,
            counterpart_email TEXT,
            notify_message_id TEXT,
            notify_correlation_id TEXT,
            PRIMARY KEY (tenant_id, id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS cross_person_requests_counterpart_status_idx
        ON cross_person_requests (tenant_id, counterpart_id, status);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS cross_person_requests_notify_correlation_idx
        ON cross_person_requests (tenant_id, notify_correlation_id);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS cross_person_requests_requester_status_idx
        ON cross_person_requests (tenant_id, requester_id, status);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS cross_person_requests_requester_status_idx;")
    op.execute("DROP INDEX IF EXISTS cross_person_requests_notify_correlation_idx;")
    op.execute("DROP INDEX IF EXISTS cross_person_requests_counterpart_status_idx;")
    op.execute("DROP TABLE IF EXISTS cross_person_requests;")
