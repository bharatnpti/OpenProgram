"""Add durable check-in clarification sends."""

from __future__ import annotations

from alembic import op

revision = "0006_checkin_clarifications"
down_revision = "0005_conversation_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkin_clarifications (
            tenant_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            clarification_number INTEGER NOT NULL CHECK (clarification_number > 0),
            question TEXT NOT NULL,
            sent_at TIMESTAMPTZ,
            outbound_message_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, correlation_id, clarification_number)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS checkin_clarifications;")
