"""Index user conversation-turn message lookups."""

from __future__ import annotations

from alembic import op

revision = "0007_conversation_user_turn_lookup"
down_revision = "0006_checkin_clarifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS conversation_turns_user_message_idx
        ON conversation_turns (tenant_id, developer_id, chat_message_id)
        WHERE role = 'user' AND chat_message_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS conversation_turns_user_message_idx;")
