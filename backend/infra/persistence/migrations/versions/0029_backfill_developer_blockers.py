"""Backfill developer_blockers from the legacy developer_statuses JSONB blockers.

Only blockers open in each developer's LATEST status row become rows, but
``first_seen_on`` is the earliest ``as_of`` across the developer's whole
history where the same normalized text appears — that is the point of the age
fix (a blocker re-confirmed daily must not read as 0 days old). If identical
text resolved and recurred before this migration, age is overstated; accepted
pragmatically since the JSONB history has no lifecycle to distinguish the two.

The synthetic "no confirmed reply" sentinel never becomes a row. The insert is
idempotent against the partial unique index, so the statement can be re-run at
deploy time to converge rows written between backfill and the dual-write
rollout.
"""

from __future__ import annotations

from alembic import op

revision = "0029_backfill_developer_blockers"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0028_developer_blockers"
branch_labels = None
depends_on = None

_BACKFILL_SQL = r"""
INSERT INTO developer_blockers (
    tenant_id, blocker_id, developer_id, description, normalized_key,
    source, first_seen_on, last_seen_on
)
SELECT
    hist.tenant_id,
    replace(gen_random_uuid()::text, '-', ''),
    hist.developer_id,
    cur.description,
    hist.normalized_key,
    'backfill',
    min(hist.as_of),
    latest.as_of
FROM (
    SELECT tenant_id, developer_id, as_of,
           rtrim(lower(btrim(regexp_replace(item, '\s+', ' ', 'g'))), '.!') AS normalized_key
    FROM developer_statuses,
         jsonb_array_elements_text(blockers -> 'items') AS item
) hist
JOIN (
    SELECT DISTINCT ON (tenant_id, developer_id) tenant_id, developer_id, as_of
    FROM developer_statuses
    ORDER BY tenant_id, developer_id, as_of DESC
) latest USING (tenant_id, developer_id)
JOIN (
    SELECT tenant_id, developer_id, as_of, item AS description,
           rtrim(lower(btrim(regexp_replace(item, '\s+', ' ', 'g'))), '.!') AS normalized_key
    FROM developer_statuses,
         jsonb_array_elements_text(blockers -> 'items') AS item
) cur
  ON cur.tenant_id = hist.tenant_id
 AND cur.developer_id = hist.developer_id
 AND cur.as_of = latest.as_of
 AND cur.normalized_key = hist.normalized_key
WHERE hist.normalized_key <> 'no confirmed reply'
GROUP BY hist.tenant_id, hist.developer_id, hist.normalized_key, cur.description, latest.as_of
ON CONFLICT (tenant_id, developer_id, normalized_key) WHERE resolved_on IS NULL
DO NOTHING;
"""


def upgrade() -> None:
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    op.execute("DELETE FROM developer_blockers WHERE source = 'backfill';")
