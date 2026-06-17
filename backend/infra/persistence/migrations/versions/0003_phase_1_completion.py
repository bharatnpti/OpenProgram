"""Complete Phase 1 sense orchestration schema."""

from __future__ import annotations

from alembic import op

revision = "0003_phase_1_completion"
down_revision = "0002_phase_1_status_rollups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkin_correlations (
            tenant_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            developer_id TEXT NOT NULL,
            chat_user_ref TEXT NOT NULL,
            chat_thread_ref TEXT NOT NULL,
            outbound_message_id TEXT NOT NULL,
            asked_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, correlation_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS checkin_correlations_thread_idx
        ON checkin_correlations (tenant_id, chat_thread_ref, asked_at DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS checkin_correlations_user_pending_idx
        ON checkin_correlations (tenant_id, chat_user_ref, asked_at DESC)
        WHERE consumed_at IS NULL;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkin_preferences (
            tenant_id TEXT NOT NULL,
            developer_id TEXT NOT NULL,
            local_time TIME NOT NULL DEFAULT TIME '09:30',
            timezone TEXT,
            weekdays JSONB NOT NULL DEFAULT '{"items": [0, 1, 2, 3, 4]}'::jsonb,
            reply_wait_seconds INTEGER NOT NULL DEFAULT 0 CHECK (reply_wait_seconds >= 0),
            final_reply_wait_seconds INTEGER NOT NULL DEFAULT 0
                CHECK (final_reply_wait_seconds >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, developer_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkin_schedule_runs (
            tenant_id TEXT NOT NULL,
            developer_id TEXT NOT NULL,
            checkin_date DATE NOT NULL,
            correlation_id TEXT NOT NULL,
            status TEXT NOT NULL,
            scheduled_at TIMESTAMPTZ NOT NULL,
            reason TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, developer_id, checkin_date)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkin_nudges (
            tenant_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            nudge_number INTEGER NOT NULL CHECK (nudge_number > 0),
            sent_at TIMESTAMPTZ,
            outbound_message_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, correlation_id, nudge_number)
        );
        """
    )
    op.execute(
        """
        DELETE FROM facts existing
        USING facts duplicate
        WHERE existing.id < duplicate.id
          AND existing.tenant_id = duplicate.tenant_id
          AND existing.source = duplicate.source
          AND existing.entity_kind = duplicate.entity_kind
          AND existing.entity_id = duplicate.entity_id
          AND existing.correlation_id = duplicate.correlation_id
          AND existing.observed_at = duplicate.observed_at;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS facts_identity_unique_idx
        ON facts (
            tenant_id,
            source,
            entity_kind,
            entity_id,
            correlation_id,
            observed_at
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS facts_identity_unique_idx;")
    op.execute("DROP TABLE IF EXISTS checkin_nudges;")
    op.execute("DROP TABLE IF EXISTS checkin_schedule_runs;")
    op.execute("DROP TABLE IF EXISTS checkin_preferences;")
    op.execute("DROP TABLE IF EXISTS checkin_correlations;")
