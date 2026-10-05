"""Keep committed delivery dates with their history, and the releases they apply to.

delivery_date_changes is append-only: each row is one change to the date of a
project, of one pod's part of a project, or of a release, with who made it,
when and an optional note. The latest row for a scope is its current date, and
the first is the date originally committed, so a date that moved shows as
moved. A cleared date is a row with no date.

releases names a slice of a project's issues by a Jira fix version or label,
so a release can carry its own date, forecast, requirements view and report.
"""

from __future__ import annotations

from alembic import op

revision = "0037_delivery_dates"
down_revision = "0036_day_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_date_changes (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            scope_kind TEXT NOT NULL CHECK (scope_kind IN ('project', 'pod', 'release')),
            scope_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            target_date DATE,
            changed_at TIMESTAMPTZ NOT NULL,
            changed_by TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT ''
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS delivery_date_changes_by_project
        ON delivery_date_changes (tenant_id, project_id, changed_at);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS releases (
            tenant_id TEXT NOT NULL,
            release_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            match_kind TEXT NOT NULL CHECK (match_kind IN ('fix_version', 'label')),
            match_value TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by TEXT NOT NULL,
            PRIMARY KEY (tenant_id, release_id)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS releases;")
    op.execute("DROP TABLE IF EXISTS delivery_date_changes;")
