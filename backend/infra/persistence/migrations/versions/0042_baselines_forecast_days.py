"""Planning baselines, and each scope's forecast as it stood per day.

Two new tables, additive only: no existing table, the graph or the AGE mirror
changes, and the downgrade drops only these. There is no data step: after the
deploy, an admin dispatch (POST /admin/workflows/forecast-days/reconstruct)
rebuilds earlier days from the stored requirement snapshots.

planning_baselines: one row each time a product owner or manager marks planning
complete for a project or a release, freezing the requirement keys in
``payload`` ({"keys": {KEY: {stage, points, due, type}}, "reconstructed",
"snapshot_day"}). A newer baseline replaces the active one by setting its
``superseded_at``; the replaced rows are kept, and a partial unique index allows
one active baseline per scope.

forecast_days: one row per scope (project, release, later pod) and day with the
history forecast's 50% and 85% dates, the verdict and the date it was measured
against, the team's latest date and the forecast's inputs. The hourly snapshot
run keeps today's row; reconstructed rows are flagged and never replace a kept
one. ``attribution`` is for the breakdown of why the date moved.
"""

from __future__ import annotations

from alembic import op

revision = "0042_baselines_forecast_days"
down_revision = "0040_release_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS planning_baselines (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            scope_kind TEXT NOT NULL CHECK (scope_kind IN ('project', 'release')),
            scope_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('snapshot', 'date')),
            planned_on DATE NOT NULL,
            payload JSONB NOT NULL,
            change_label TEXT NOT NULL,
            grace_working_days INTEGER NOT NULL DEFAULT 2
                CHECK (grace_working_days >= 0),
            set_by TEXT NOT NULL,
            set_at TIMESTAMPTZ NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            superseded_at TIMESTAMPTZ
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS planning_baselines_by_scope
            ON planning_baselines (tenant_id, scope_kind, scope_id, set_at);
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS planning_baselines_one_active
            ON planning_baselines (tenant_id, scope_kind, scope_id)
            WHERE superseded_at IS NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS planning_baselines_by_project
            ON planning_baselines (tenant_id, project_id, set_at);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS forecast_days (
            tenant_id TEXT NOT NULL,
            scope_kind TEXT NOT NULL CHECK (scope_kind IN ('project', 'release', 'pod')),
            scope_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            day DATE NOT NULL,
            p50 DATE,
            p85 DATE,
            verdict TEXT NOT NULL,
            target_date DATE,
            team_latest DATE,
            team_latest_key TEXT,
            remaining NUMERIC NOT NULL,
            unit TEXT NOT NULL,
            sample_days INTEGER NOT NULL,
            completed_in_sample NUMERIC NOT NULL,
            min_sample_days INTEGER NOT NULL,
            attribution JSONB,
            reconstructed BOOLEAN NOT NULL DEFAULT false,
            computed_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, scope_kind, scope_id, day)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS forecast_days_by_project
            ON forecast_days (tenant_id, project_id, day);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS forecast_days;")
    op.execute("DROP TABLE IF EXISTS planning_baselines;")
