"""Add issue-tracker write-back audit log, tenant system gate, and consent.

Backs Feature A (Jira write-back): an append-only ``writeback_audit`` log, the
per-tenant ``writeback_config`` system-gate override, and a per-developer
``write_back_consent`` column on ``checkin_preferences`` (default ``always_ask``).
"""

from __future__ import annotations

from alembic import op

revision = "0022_writeback_audit"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0021_identity_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Append-only audit of gated writes: who/what/when, before->after for
    # reversibility, and (issue_key, target_state, correlation_id) for idempotency.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS writeback_audit (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            developer_id TEXT NOT NULL,
            issue_key TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            status TEXT NOT NULL,
            target_state TEXT NOT NULL,
            before_state TEXT,
            after_state TEXT,
            comment TEXT,
            source TEXT NOT NULL DEFAULT 'checkin',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_writeback_audit_issue
        ON writeback_audit (tenant_id, issue_key);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_writeback_audit_idempotency
        ON writeback_audit (tenant_id, issue_key, target_state, correlation_id);
        """
    )
    # Admin-controlled system gate override, per tenant. Absence means fall back
    # to the static settings default (OFF).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS writeback_config (
            tenant_id TEXT PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    # Per-developer standing consent gate. Safe default: always_ask.
    op.execute(
        """
        ALTER TABLE checkin_preferences
        ADD COLUMN IF NOT EXISTS write_back_consent TEXT NOT NULL DEFAULT 'always_ask';
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE checkin_preferences DROP COLUMN IF EXISTS write_back_consent;")
    op.execute("DROP TABLE IF EXISTS writeback_config;")
    op.execute("DROP TABLE IF EXISTS writeback_audit;")
