"""Add workstream graph node kind."""

from __future__ import annotations

from alembic import op

revision = "0011_workstream_node_kind"
down_revision = "0010_directory_user_search_indexes"
branch_labels = None
depends_on = None

_ALL_NODE_KINDS = (
    "'program', 'project', 'workstream', 'sprint', 'repo', 'pod', 'developer', 'task'"
)
_OLD_NODE_KINDS = "'program', 'project', 'sprint', 'repo', 'pod', 'developer', 'task'"


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


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM graph_edges
        WHERE (tenant_id, from_node_id) IN (
            SELECT tenant_id, id FROM graph_nodes WHERE kind = 'workstream'
        )
           OR (tenant_id, to_node_id) IN (
            SELECT tenant_id, id FROM graph_nodes WHERE kind = 'workstream'
        );
        """
    )
    op.execute("DELETE FROM node_statuses WHERE entity_kind = 'workstream';")
    op.execute("DELETE FROM graph_nodes WHERE kind = 'workstream';")
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
