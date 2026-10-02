"""Track counterpart DM attempts on cross-person requests.

A counterpart DM that failed to send left the request open with no
notification ids and nothing retried it. Each send attempt is now recorded:
how many have been made, when the last one was, and when the next one is due.
A retry pass picks up requests whose next attempt is due; once the limit is
reached the next-attempt time is cleared and the request stays visibly
un-notified.

Existing rows keep zero attempts. A DM that failed before this change cannot be
told apart from a request recorded while notification was switched off, so
neither is retried; both stay as they are.
"""

from __future__ import annotations

from alembic import op

revision = "0032_cross_person_notify_attempts"
down_revision = "0031_checkin_preferences_inherit_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE cross_person_requests
        ADD COLUMN IF NOT EXISTS notify_attempts INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS notify_last_attempt_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS notify_next_attempt_at TIMESTAMPTZ;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS cross_person_requests_notify_retry_idx
        ON cross_person_requests (tenant_id, notify_next_attempt_at)
        WHERE notify_next_attempt_at IS NOT NULL AND notify_correlation_id IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS cross_person_requests_notify_retry_idx;")
    op.execute(
        """
        ALTER TABLE cross_person_requests
        DROP COLUMN IF EXISTS notify_next_attempt_at,
        DROP COLUMN IF EXISTS notify_last_attempt_at,
        DROP COLUMN IF EXISTS notify_attempts;
        """
    )
