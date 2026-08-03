"""First-class developer blocker lifecycle table plus a reverse graph-edge index.

``developer_blockers`` gives each reported blocker identity, lifecycle dates
(first_seen_on / last_seen_on / resolved_on), and optional attribution to a
work item or an explicit pod, so pod-scoped reads stop fanning every blocker
of a multi-pod developer into every pod. ``developer_statuses.blockers``
remains a derived compatibility projection of the open set.

``graph_edges_to_idx`` mirrors ``graph_edges_from_idx`` for the new reverse
lookups (pods containing a developer; pods owning a task).
"""

from __future__ import annotations

from alembic import op

revision = "0028_developer_blockers"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0027_backfill_age_graph_mirror"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive: 0027 historically leaked `search_path = ag_catalog, ...` on
    # the shared alembic connection; databases migrated with that version
    # would otherwise create this table inside ag_catalog.
    op.execute("SET search_path = public;")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS developer_blockers (
            tenant_id TEXT NOT NULL,
            blocker_id TEXT NOT NULL,
            developer_id TEXT NOT NULL,
            description TEXT NOT NULL,
            normalized_key TEXT NOT NULL,
            work_item_id TEXT,
            pod_id TEXT,
            source TEXT NOT NULL CHECK (
                source IN ('checkin', 'correction', 'carry_forward', 'backfill', 'system')
            ),
            source_correlation_id TEXT,
            attribution_asked_at TIMESTAMPTZ,
            first_seen_on DATE NOT NULL,
            last_seen_on DATE NOT NULL,
            resolved_on DATE,
            resolved_reason TEXT CHECK (
                resolved_reason IN (
                    'reported_resolved', 'omitted_in_correction', 'confirmed_no_blockers'
                )
            ),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, blocker_id),
            CHECK (last_seen_on >= first_seen_on),
            CHECK (resolved_on IS NULL OR resolved_on >= first_seen_on),
            CHECK ((resolved_on IS NULL) = (resolved_reason IS NULL))
        );
        """
    )
    # One OPEN blocker per (tenant, developer, normalized text); doubles as the
    # index behind "open blockers by developer" and makes retried writes and
    # the backfill idempotent.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS developer_blockers_open_key_uq
        ON developer_blockers (tenant_id, developer_id, normalized_key)
        WHERE resolved_on IS NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS developer_blockers_work_item_idx
        ON developer_blockers (tenant_id, work_item_id)
        WHERE work_item_id IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS developer_blockers_developer_idx
        ON developer_blockers (tenant_id, developer_id, first_seen_on);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS graph_edges_to_idx
        ON graph_edges (tenant_id, to_node_id, kind, valid_from, valid_to);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS graph_edges_to_idx;")
    op.execute("DROP TABLE IF EXISTS developer_blockers;")
