"""Allow partial check-in status source."""

from __future__ import annotations

from alembic import op

revision = "0018_partial_status_source"
down_revision = "0017_developer_status_confirmation"
branch_labels = None
depends_on = None

_OLD_SOURCES = "'confirmed', 'inferred', 'stale', 'unknown'"
_NEW_SOURCES = "'confirmed', 'partial', 'inferred', 'stale', 'unknown'"


def upgrade() -> None:
    _replace_source_check("developer_statuses", _NEW_SOURCES)
    _replace_source_check("node_statuses", _NEW_SOURCES)


def downgrade() -> None:
    op.execute("UPDATE developer_statuses SET source = 'inferred' WHERE source = 'partial';")
    op.execute("UPDATE node_statuses SET source = 'inferred' WHERE source = 'partial';")
    _replace_source_check("developer_statuses", _OLD_SOURCES)
    _replace_source_check("node_statuses", _OLD_SOURCES)


def _replace_source_check(table_name: str, sources: str) -> None:
    op.execute(
        f"""
        ALTER TABLE {table_name}
        DROP CONSTRAINT IF EXISTS {table_name}_source_check;
        """
    )
    op.execute(
        f"""
        ALTER TABLE {table_name}
        ADD CONSTRAINT {table_name}_source_check CHECK (source IN ({sources}));
        """
    )
