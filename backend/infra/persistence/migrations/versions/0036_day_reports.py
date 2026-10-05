"""Store day reports and every time one was sent.

day_reports holds what an admin set up: the project, the local send time,
timezone and weekdays, and where the report goes (chat channels, people,
email addresses, the Teams channel). day_report_runs keeps each send with the
text that went out and how each destination fared, as fixed sentences. A
partial unique index allows one scheduled run per report and local day, which
is what makes a scheduled report go out at most once that day; manual "send
now" runs are not limited. Removing a report removes its runs.
"""

from __future__ import annotations

from alembic import op

revision = "0036_day_reports"
down_revision = "0035_delivery_stages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS day_reports (
            tenant_id TEXT NOT NULL,
            report_id TEXT NOT NULL,
            name TEXT NOT NULL,
            project_id TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT false,
            local_time TIME NOT NULL,
            timezone TEXT NOT NULL,
            weekdays SMALLINT[] NOT NULL,
            destinations JSONB NOT NULL DEFAULT '[]'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by TEXT NOT NULL,
            PRIMARY KEY (tenant_id, report_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS day_report_runs (
            tenant_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            report_id TEXT NOT NULL,
            report_date DATE NOT NULL,
            trigger TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual')),
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            outcomes JSONB NOT NULL DEFAULT '[]'::jsonb,
            actor TEXT,
            PRIMARY KEY (tenant_id, run_id),
            FOREIGN KEY (tenant_id, report_id)
                REFERENCES day_reports (tenant_id, report_id) ON DELETE CASCADE
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS day_report_runs_one_scheduled_per_day
        ON day_report_runs (tenant_id, report_id, report_date)
        WHERE trigger = 'schedule';
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS day_report_runs_newest_first
        ON day_report_runs (tenant_id, report_id, started_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS day_report_runs;")
    op.execute("DROP TABLE IF EXISTS day_reports;")
