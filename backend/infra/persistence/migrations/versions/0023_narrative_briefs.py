"""Add persisted scheduled narrative briefs.

Backs Plan 03 Feature F (scheduled narrative briefs): a ``narrative_briefs``
table storing descriptive daily-pod, weekly-project, and exec rollups. Bodies
are fact/feed/status summaries only -- never raw check-in or DM/reply content.
``sources`` carries entity/fact refs so every claim stays drillable.
"""

from __future__ import annotations

from alembic import op

revision = "0023_narrative_briefs"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0022_writeback_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS narrative_briefs (
            tenant_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('daily_pod', 'weekly_project', 'exec')),
            scope_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL,
            sources JSONB NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, kind, scope_id, generated_at)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_narrative_briefs_latest
        ON narrative_briefs (tenant_id, kind, generated_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS narrative_briefs;")
