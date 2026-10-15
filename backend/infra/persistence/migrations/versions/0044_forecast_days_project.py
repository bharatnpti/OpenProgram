"""forecast_days keyed by project too, so a pod two projects share keeps a row under each.

A pod's forecast is its part of one project's work (that project's keys, date and
seed). With the key (tenant_id, scope_kind, scope_id, day) a pod on two projects
could keep one row a day only, so it kept none on those days and had no
history. The key is now (tenant_id, scope_kind, scope_id, project_id, day).

forecast_days has carried project_id since 0042, NOT NULL on every row: the
project the row was computed under (the project itself for a project row, the
release's project for a release row, the project whose delivery view computed it
for a pod row; an upsert replaced it with the row). So this needs no new column
and no backfill: every existing row is already keyed by the project it was
computed in. For a project row project_id equals scope_id, and a release belongs
to one project, so their keys are unchanged in effect; the plain column key is
the equivalent of a (..., coalesce(project_id, ''), day) expression key, and it
is what the repository's ON CONFLICT (column list) upsert infers. The index by
(tenant_id, project_id, day) stays.

The downgrade restores the old key. A pod with rows under more than one project on
a day loses that day's rows, as the old code keeps no row for such a pod; any
other scope with rows under two projects on a day (a release moved to another
project) keeps the one computed last. Nothing else changes.
"""

from __future__ import annotations

from alembic import op

revision = "0044_forecast_days_project"
down_revision = "0043_jira_hygiene"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE forecast_days
            DROP CONSTRAINT IF EXISTS forecast_days_pkey,
            ADD CONSTRAINT forecast_days_pkey
                PRIMARY KEY (tenant_id, scope_kind, scope_id, project_id, day);
        """
    )


def downgrade() -> None:
    # A pod on more than one project that day: the old key cannot hold its rows.
    op.execute(
        """
        DELETE FROM forecast_days AS stale
        USING (
            SELECT tenant_id, scope_kind, scope_id, day
            FROM forecast_days
            WHERE scope_kind = 'pod'
            GROUP BY tenant_id, scope_kind, scope_id, day
            HAVING count(*) > 1
        ) AS shared
        WHERE stale.tenant_id = shared.tenant_id
            AND stale.scope_kind = shared.scope_kind
            AND stale.scope_id = shared.scope_id
            AND stale.day = shared.day;
        """
    )
    # Any other scope under two projects on a day: the row computed last stays.
    op.execute(
        """
        DELETE FROM forecast_days AS stale
        WHERE EXISTS (
            SELECT 1 FROM forecast_days AS later
            WHERE later.tenant_id = stale.tenant_id
                AND later.scope_kind = stale.scope_kind
                AND later.scope_id = stale.scope_id
                AND later.day = stale.day
                AND (later.computed_at, later.project_id) > (stale.computed_at, stale.project_id)
        );
        """
    )
    op.execute(
        """
        ALTER TABLE forecast_days
            DROP CONSTRAINT IF EXISTS forecast_days_pkey,
            ADD CONSTRAINT forecast_days_pkey PRIMARY KEY (tenant_id, scope_kind, scope_id, day);
        """
    )
