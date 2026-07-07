"""Rename AGE graph mirror for OpenProgram."""

from __future__ import annotations

from alembic import op

revision = "0015_openprogram_age_graph"
down_revision = "0014_cross_person_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("LOAD 'age';")
    op.execute('SET search_path = ag_catalog, "$user", public;')
    op.execute(
        """
        SELECT create_graph('openprogram_graph')
        WHERE NOT EXISTS (
            SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'openprogram_graph'
        );
        """
    )
    op.execute(
        """
        SELECT drop_graph('pulseops_graph', true)
        WHERE EXISTS (
            SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'pulseops_graph'
        );
        """
    )
    op.execute("SET search_path = public;")


def downgrade() -> None:
    op.execute("LOAD 'age';")
    op.execute('SET search_path = ag_catalog, "$user", public;')
    op.execute(
        """
        SELECT create_graph('pulseops_graph')
        WHERE NOT EXISTS (
            SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'pulseops_graph'
        );
        """
    )
    op.execute(
        """
        SELECT drop_graph('openprogram_graph', true)
        WHERE EXISTS (
            SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'openprogram_graph'
        );
        """
    )
    op.execute("SET search_path = public;")
