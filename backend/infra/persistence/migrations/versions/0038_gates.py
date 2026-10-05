"""Keep gate templates, the items each issue needs, and the questions it waits on.

gate_templates: what an admin set up. Each guards one delivery stage and lists
the kinds of item it needs, who signs each kind off, whether evidence is
required, and the headings Jira text writes them under (in ``kinds``).

gate_items: one row per item on an issue, suggested from its Jira text or
added by hand. A suggestion counts only once confirmed; a dismissed one stays
so it is not suggested again. ``fingerprint`` is the issue, gate, kind and
normalised text, unique per tenant.

tracked_questions: questions asked in Jira comments by mentioning someone,
with whether the person replied. A status a person set is never overwritten
by a later scan.

issue_scans: the issue's own updated time when its text was last read.
"""

from __future__ import annotations

from alembic import op

revision = "0038_gates"
down_revision = "0037_delivery_dates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gate_templates (
            tenant_id TEXT NOT NULL,
            template_id TEXT NOT NULL,
            name TEXT NOT NULL,
            guards_stage TEXT NOT NULL,
            kinds JSONB NOT NULL,
            issue_types TEXT[] NOT NULL DEFAULT '{}',
            enabled BOOLEAN NOT NULL DEFAULT true,
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by TEXT NOT NULL,
            PRIMARY KEY (tenant_id, template_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gate_items (
            tenant_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            issue_key TEXT NOT NULL,
            template_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('suggested', 'dismissed', 'pending', 'met', 'failed', 'waived')
            ),
            source TEXT NOT NULL CHECK (source IN ('description', 'comment', 'manual')),
            source_ref TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            created_by TEXT NOT NULL,
            signed_by TEXT,
            signed_at TIMESTAMPTZ,
            evidence_url TEXT,
            note TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (tenant_id, item_id),
            UNIQUE (tenant_id, fingerprint)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS gate_items_by_issue ON gate_items (tenant_id, issue_key);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tracked_questions (
            tenant_id TEXT NOT NULL,
            question_id TEXT NOT NULL,
            issue_key TEXT NOT NULL,
            comment_ref TEXT NOT NULL DEFAULT '',
            asked_by TEXT NOT NULL,
            asked_by_name TEXT NOT NULL DEFAULT '',
            asked_to TEXT NOT NULL DEFAULT '',
            asked_to_name TEXT NOT NULL DEFAULT '',
            asked_at TIMESTAMPTZ NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('not_yet', 'partly', 'answered', 'closed_unanswered')
            ),
            confirmed BOOLEAN NOT NULL DEFAULT false,
            status_set_by_person BOOLEAN NOT NULL DEFAULT false,
            answered_ref TEXT,
            dismissed BOOLEAN NOT NULL DEFAULT false,
            updated_at TIMESTAMPTZ,
            updated_by TEXT,
            PRIMARY KEY (tenant_id, question_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS tracked_questions_by_issue "
        "ON tracked_questions (tenant_id, issue_key);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS issue_scans (
            tenant_id TEXT NOT NULL,
            issue_key TEXT NOT NULL,
            issue_updated_at TIMESTAMPTZ,
            scanned_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, issue_key)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS issue_scans;")
    op.execute("DROP TABLE IF EXISTS tracked_questions;")
    op.execute("DROP TABLE IF EXISTS gate_items;")
    op.execute("DROP TABLE IF EXISTS gate_templates;")
