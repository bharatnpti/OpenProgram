"""Jira hygiene: the tenant's per-issue rules, their findings, the scan's runs and its audit.

Five new tables, additive only: no existing table, the graph or the AGE mirror
changes, and the downgrade drops only these. There is no data step: no rule is
on until an admin adds one.

hygiene_settings: the scan's switch per tenant, its change labels (the
tenant's own words for scope agreed after planning) and its stale days.

hygiene_rules: the tenant's rules, one row each, the rule itself as JSON in
``spec``. A removed rule keeps its row (``deleted_at``), so its findings and
audit still read.

hygiene_runs: one row per run slot (a tick's time, or a press of Check now per
scope and minute), so a doubled tick does nothing new.

hygiene_findings: one row per rule, issue and scope while open; a resolved one
is kept, and the same breach later opens a new row. ``detail`` holds field
names, stages, labels, dates and keys only, never Jira text. A person's waive
or "not applicable" sits on the row (``decided_*``) and in the audit.

hygiene_actions: the append-only audit of every person's action and every change
the scan made.
"""

from __future__ import annotations

from alembic import op

revision = "0043_jira_hygiene"
down_revision = "0042_baselines_forecast_days"
branch_labels = None
depends_on = None

_TABLES = (
    "hygiene_actions",
    "hygiene_findings",
    "hygiene_runs",
    "hygiene_rules",
    "hygiene_settings",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hygiene_settings (
            tenant_id TEXT PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT false,
            change_labels JSONB NOT NULL DEFAULT '[]'::jsonb,
            stale_working_days INTEGER NOT NULL DEFAULT 5
                CHECK (stale_working_days > 0),
            updated_by TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hygiene_rules (
            tenant_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            name TEXT NOT NULL,
            spec JSONB NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            deleted_at TIMESTAMPTZ,
            updated_by TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, rule_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hygiene_runs (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            slot TEXT NOT NULL,
            trigger TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual', 'scope_change')),
            status TEXT NOT NULL
                CHECK (status IN ('running', 'ok', 'partial', 'failed', 'off')),
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            counts JSONB NOT NULL DEFAULT '{}'::jsonb,
            data_as_of TIMESTAMPTZ,
            error_category TEXT,
            actor TEXT,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, slot)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hygiene_findings (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            rule_version INTEGER NOT NULL,
            issue_key TEXT NOT NULL,
            scope_kind TEXT NOT NULL CHECK (scope_kind IN ('project', 'release', 'pod')),
            scope_id TEXT NOT NULL,
            project_id TEXT,
            state TEXT NOT NULL
                CHECK (state IN ('open', 'resolved', 'waived', 'not_applicable')),
            owner_member_id TEXT,
            detail JSONB NOT NULL DEFAULT '{}'::jsonb,
            fingerprint TEXT NOT NULL,
            held BOOLEAN NOT NULL DEFAULT false,
            first_seen_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL,
            resolved_at TIMESTAMPTZ,
            resolution TEXT CHECK (resolution IN ('fixed', 'left_scope')),
            decided_by TEXT,
            decided_at TIMESTAMPTZ,
            decision_reason TEXT,
            PRIMARY KEY (tenant_id, id)
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS hygiene_findings_one_open
            ON hygiene_findings (tenant_id, rule_id, issue_key, scope_kind, scope_id)
            WHERE state = 'open';
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS hygiene_findings_by_scope
            ON hygiene_findings (tenant_id, scope_kind, scope_id, state);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hygiene_actions (
            tenant_id TEXT NOT NULL,
            id TEXT NOT NULL,
            finding_id TEXT,
            rule_id TEXT,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            at TIMESTAMPTZ NOT NULL,
            note TEXT,
            detail JSONB,
            PRIMARY KEY (tenant_id, id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS hygiene_actions_by_finding
            ON hygiene_actions (tenant_id, finding_id, at);
        """
    )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table};")
