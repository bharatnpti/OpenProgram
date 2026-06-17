"""Add durable per-user daily conversation turns."""

from __future__ import annotations

from alembic import op

revision = "0005_conversation_store"
down_revision = "0004_graph_anchor_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_turns (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            developer_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            conversation_date DATE NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('agent', 'user', 'system')),
            content TEXT NOT NULL,
            correlation_id TEXT,
            chat_message_id TEXT,
            observed_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (id, observed_at)
        );
        """
    )
    op.execute(
        "SELECT create_hypertable('conversation_turns', 'observed_at', if_not_exists => TRUE);"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS conversation_turns_user_day_idx
        ON conversation_turns (tenant_id, developer_id, conversation_date DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS conversation_turns_user_day_idx;")
    op.execute("DROP TABLE IF EXISTS conversation_turns;")
