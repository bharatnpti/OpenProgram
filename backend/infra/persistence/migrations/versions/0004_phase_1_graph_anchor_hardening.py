"""Harden Phase 1 graph anchors and fact reads."""

from __future__ import annotations

from alembic import op

revision = "0004_graph_anchor_hardening"
down_revision = "0003_phase_1_completion"
branch_labels = None
depends_on = None

_ALL_NODE_KINDS = "'program', 'project', 'sprint', 'repo', 'pod', 'developer', 'task'"
_OLD_NODE_KINDS = "'program', 'project', 'pod', 'developer', 'task'"


def upgrade() -> None:
    op.execute("ALTER TABLE graph_nodes DROP CONSTRAINT IF EXISTS graph_nodes_kind_check;")
    op.execute(
        f"""
        ALTER TABLE graph_nodes
        ADD CONSTRAINT graph_nodes_kind_check
        CHECK (kind IN ({_ALL_NODE_KINDS}));
        """
    )
    op.execute(
        "ALTER TABLE node_statuses DROP CONSTRAINT IF EXISTS node_statuses_entity_kind_check;"
    )
    op.execute(
        f"""
        ALTER TABLE node_statuses
        ADD CONSTRAINT node_statuses_entity_kind_check
        CHECK (entity_kind IN ({_ALL_NODE_KINDS}));
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS facts_entity_observed_idx
        ON facts (tenant_id, entity_kind, entity_id, observed_at DESC);
        """
    )
    op.execute(
        """
        ALTER TABLE checkin_preferences
        ALTER COLUMN reply_wait_seconds SET DEFAULT 14400,
        ALTER COLUMN final_reply_wait_seconds SET DEFAULT 28800;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM graph_edges
        WHERE (tenant_id, from_node_id) IN (
            SELECT tenant_id, id FROM graph_nodes WHERE kind IN ('sprint', 'repo')
        )
           OR (tenant_id, to_node_id) IN (
            SELECT tenant_id, id FROM graph_nodes WHERE kind IN ('sprint', 'repo')
        );
        """
    )
    op.execute("DELETE FROM node_statuses WHERE entity_kind IN ('sprint', 'repo');")
    op.execute("DELETE FROM graph_nodes WHERE kind IN ('sprint', 'repo');")
    op.execute("DROP INDEX IF EXISTS facts_entity_observed_idx;")
    op.execute(
        """
        ALTER TABLE checkin_preferences
        ALTER COLUMN reply_wait_seconds SET DEFAULT 0,
        ALTER COLUMN final_reply_wait_seconds SET DEFAULT 0;
        """
    )
    op.execute("ALTER TABLE graph_nodes DROP CONSTRAINT IF EXISTS graph_nodes_kind_check;")
    op.execute(
        f"""
        ALTER TABLE graph_nodes
        ADD CONSTRAINT graph_nodes_kind_check
        CHECK (kind IN ({_OLD_NODE_KINDS}));
        """
    )
    op.execute(
        "ALTER TABLE node_statuses DROP CONSTRAINT IF EXISTS node_statuses_entity_kind_check;"
    )
    op.execute(
        f"""
        ALTER TABLE node_statuses
        ADD CONSTRAINT node_statuses_entity_kind_check
        CHECK (entity_kind IN ({_OLD_NODE_KINDS}));
        """
    )
