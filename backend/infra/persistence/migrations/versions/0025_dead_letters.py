"""Add dead-letter records for stuck inbound events.

Backs Plan 04 Section 4d (dead-letter + alerting): a ``dead_letters`` table that
makes the "recorded but never finalized" failure mode operator-visible and
recoverable. Rows store only identifiers and diagnostics (tenant, conversation
key, event ids, reason, attempts, timestamps) -- never raw DM/reply content,
mirroring the ``inbound_chat_events`` privacy rule. ``event_ids`` carries the
buffered event ids so an operator can re-arm the exact burst.
"""

from __future__ import annotations

from alembic import op

revision = "0025_dead_letters"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0024_remove_dead_infra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dead_letters (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
            conversation_key TEXT NOT NULL,
            event_ids JSONB NOT NULL DEFAULT '[]',
            reason TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            first_seen_at TIMESTAMPTZ NOT NULL,
            dead_lettered_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'rearmed')),
            rearmed_at TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_dead_letters_open
        ON dead_letters (tenant_id, status, dead_lettered_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dead_letters;")
