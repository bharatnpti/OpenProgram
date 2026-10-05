"""Keep each project's escalation matrix, a report's release, and the day's note.

escalation_matrices: per project, or the tenant's own under project_id '',
after how many days each kind of ask (fix, decision, answer, review) reaches
each level above its owner, and who stands there (in ``levels``), with who
owns a decision nothing else names an owner for.

day_reports.release_id: a report on one release of its project only.

day_report_notes: what someone wants said first in one day's report.
"""

from __future__ import annotations

from alembic import op

revision = "0039_escalation_matrix"
down_revision = "0038_gates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS escalation_matrices (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            decision_owner_id TEXT,
            levels JSONB NOT NULL DEFAULT '[]'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by TEXT NOT NULL,
            PRIMARY KEY (tenant_id, project_id)
        );
        """
    )
    op.execute("ALTER TABLE day_reports ADD COLUMN IF NOT EXISTS release_id TEXT;")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS day_report_notes (
            tenant_id TEXT NOT NULL,
            report_id TEXT NOT NULL,
            report_date DATE NOT NULL,
            text TEXT NOT NULL,
            author TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, report_id, report_date),
            FOREIGN KEY (tenant_id, report_id)
                REFERENCES day_reports (tenant_id, report_id) ON DELETE CASCADE
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS day_report_notes;")
    op.execute("ALTER TABLE day_reports DROP COLUMN IF EXISTS release_id;")
    op.execute("DROP TABLE IF EXISTS escalation_matrices;")
