"""Profiles: the discipline a member brings to each pod they are in.

Two new tables, additive only. No existing table, the graph or the AGE mirror
changes, and the downgrade drops only these two.

profile_catalog: a tenant's changes to the shipped profile catalog (rename,
hide, add). No rows means the generic defaults in ``core.domain.profiles``.

pod_member_profiles: one dated row per pod membership and change. The current
profile is the latest row starting on or before the day read; a change adds a
row from its day, so earlier days keep the old one. A profile grants no rights:
it only routes who is asked, named and escalated to.

Data step: one ``backfill`` row for every pod membership in force (a
``contains`` edge from a pod to a developer that has not ended), its profile
taken from the membership's pod role (developer is development, product owner
is product, scrum master is delivery, manager is management, anything else is
development) and its start from the edge's own (1970-01-01 when it has none).
"""

from __future__ import annotations

from alembic import op

revision = "0041_pod_member_profiles"
down_revision = "0040_release_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_catalog (
            tenant_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            label TEXT NOT NULL,
            code_work BOOLEAN NOT NULL DEFAULT false,
            title_words JSONB NOT NULL DEFAULT '[]'::jsonb,
            sort INTEGER NOT NULL DEFAULT 100,
            hidden BOOLEAN NOT NULL DEFAULT false,
            updated_by TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, slug)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pod_member_profiles (
            tenant_id TEXT NOT NULL,
            pod_id TEXT NOT NULL,
            member_id TEXT NOT NULL,
            profile TEXT NOT NULL,
            valid_from DATE NOT NULL,
            source TEXT NOT NULL CHECK (
                source IN ('backfill', 'admin', 'import', 'suggestion_accepted')
            ),
            is_lead BOOLEAN NOT NULL DEFAULT false,
            set_by TEXT,
            set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, pod_id, member_id, valid_from)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS pod_member_profiles_member
        ON pod_member_profiles (tenant_id, member_id);
        """
    )
    op.execute(
        """
        INSERT INTO pod_member_profiles
            (tenant_id, pod_id, member_id, profile, valid_from, source, is_lead, set_by, set_at)
        SELECT
            edge.tenant_id,
            edge.from_node_id,
            edge.to_node_id,
            CASE lower(replace(trim(coalesce(edge.metadata->>'role', '')), ' ', '_'))
                WHEN 'developer' THEN 'development'
                WHEN 'product_owner' THEN 'product'
                WHEN 'scrum_master' THEN 'delivery'
                WHEN 'manager' THEN 'management'
                ELSE 'development'
            END,
            coalesce(edge.valid_from, DATE '1970-01-01'),
            'backfill',
            false,
            NULL,
            now()
        FROM graph_edges AS edge
        JOIN graph_nodes AS pod
            ON pod.tenant_id = edge.tenant_id
            AND pod.id = edge.from_node_id
            AND pod.kind = 'pod'
        JOIN graph_nodes AS member
            ON member.tenant_id = edge.tenant_id
            AND member.id = edge.to_node_id
            AND member.kind = 'developer'
        WHERE edge.kind = 'contains'
            AND (edge.valid_to IS NULL OR edge.valid_to > current_date)
        ORDER BY edge.tenant_id, edge.from_node_id, edge.to_node_id, edge.valid_from
        ON CONFLICT (tenant_id, pod_id, member_id, valid_from) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pod_member_profiles;")
    op.execute("DROP TABLE IF EXISTS profile_catalog;")
