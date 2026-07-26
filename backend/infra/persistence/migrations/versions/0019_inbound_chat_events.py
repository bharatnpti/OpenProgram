"""Add durable inbound chat event buffer for fast-ack + coalesced processing."""

from __future__ import annotations

from alembic import op

revision = "0019_inbound_chat_events"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0018_partial_status_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    # A regular table (not a Timescale hypertable): the UNIQUE(tenant_id,
    # provider, event_id) redelivery guard cannot include received_at, which a
    # hypertable would force into the key and defeat retry dedup. Rows are a
    # short-lived processing buffer purged on the conversation retention path.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS inbound_chat_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            event_id TEXT NOT NULL,
            conversation_key TEXT NOT NULL,
            chat_user_ref TEXT NOT NULL,
            chat_thread_ref TEXT,
            message_ref TEXT NOT NULL,
            outbound_message_id TEXT,
            correlation_id TEXT NOT NULL,
            text TEXT NOT NULL,
            raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            received_at TIMESTAMPTZ NOT NULL,
            processed_at TIMESTAMPTZ,
            attempts INT NOT NULL DEFAULT 0,
            UNIQUE (tenant_id, provider, event_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS inbound_chat_events_conversation_idx
        ON inbound_chat_events (tenant_id, conversation_key, processed_at);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS inbound_chat_events_sweep_idx
        ON inbound_chat_events (tenant_id, processed_at, received_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS inbound_chat_events_sweep_idx;")
    op.execute("DROP INDEX IF EXISTS inbound_chat_events_conversation_idx;")
    op.execute("DROP TABLE IF EXISTS inbound_chat_events;")
