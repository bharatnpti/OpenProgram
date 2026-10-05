"""Keep the tenant's delivery stage mapping and a daily snapshot of each project's requirements.

delivery_settings holds which tracker statuses count as which of the six
delivery stages (raised, groomed, in development, in testing, business
testing, production), which statuses are not counted, and which issue types
are requirements. requirement_snapshots holds, per project and day, how many
requirements sat in each stage and each requirement's stage, so the console
can draw counts over time and the day report can say what moved. A later
snapshot of the same day replaces the earlier one; past days are not changed.
"""

from __future__ import annotations

from alembic import op

revision = "0035_delivery_stages"
down_revision = "0034_integration_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_settings (
            tenant_id TEXT PRIMARY KEY,
            mapping JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by TEXT NOT NULL
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS requirement_snapshots (
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            day DATE NOT NULL,
            payload JSONB NOT NULL,
            computed_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, project_id, day)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS requirement_snapshots;")
    op.execute("DROP TABLE IF EXISTS delivery_settings;")
