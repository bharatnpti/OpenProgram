"""Release readiness: criteria, the agent's findings and drafts, its runs, and its audit.

Seven new tables, additive only: no existing table, the graph or the AGE mirror
changes, and the downgrade drops only these.

readiness_settings: the agent's switches per tenant (on, draft issues, create
in Jira) and what a new issue gets (type, labels), as JSON.

readiness_criteria: the tenant's release criteria, one row each, its rules and
draft template as JSON in ``spec``. A removed criterion keeps its row
(``deleted_at``), so its findings and audit still read.

readiness_overrides: per-scope changes to a criterion (off, lead, target
project); the table ships now and is used later.

readiness_runs: one row per run slot (a tick's time, or a manual press per scope
and minute), so a doubled tick does nothing new.

readiness_findings: one row per criterion and scope with the rules' state, the
evidence, the urgency, a person's decision and the fingerprint that keeps an
unchanged run from writing.

readiness_suggestions: the drafted Jira issue of a finding, one for its whole
life, so a dismissed draft is never drafted again.

readiness_actions: the append-only audit of every person's action and every
change the agent made.
"""

from __future__ import annotations

from alembic import op

revision = "0040_release_readiness"
down_revision = "0039_escalation_matrix"
branch_labels = None
depends_on = None

_TABLES = (
    "readiness_actions",
    "readiness_suggestions",
    "readiness_findings",
    "readiness_runs",
    "readiness_overrides",
    "readiness_criteria",
    "readiness_settings",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS readiness_settings (
            tenant_id TEXT PRIMARY KEY,
            settings JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by TEXT NOT NULL
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS readiness_criteria (
            tenant_id TEXT NOT NULL,
            criterion_id TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            name TEXT NOT NULL,
            spec JSONB NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            source_ref TEXT,
            deleted_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by TEXT NOT NULL,
            PRIMARY KEY (tenant_id, criterion_id)
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS readiness_criteria_source
            ON readiness_criteria (tenant_id, source_ref) WHERE source_ref IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS readiness_overrides (
            tenant_id TEXT NOT NULL,
            criterion_id TEXT NOT NULL,
            scope_kind TEXT NOT NULL CHECK (scope_kind IN ('project', 'release', 'pod')),
            scope_id TEXT NOT NULL,
            override JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by TEXT NOT NULL,
            PRIMARY KEY (tenant_id, criterion_id, scope_kind, scope_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS readiness_runs (
            tenant_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            slot TEXT NOT NULL,
            trigger TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual', 'scope_change')),
            scope_kind TEXT,
            scope_id TEXT,
            status TEXT NOT NULL
                CHECK (status IN ('running', 'ok', 'partial', 'failed', 'off')),
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            counts JSONB NOT NULL DEFAULT '{}'::jsonb,
            data_as_of TIMESTAMPTZ,
            model_calls INTEGER NOT NULL DEFAULT 0,
            model_tokens INTEGER NOT NULL DEFAULT 0,
            error_category TEXT,
            actor TEXT,
            PRIMARY KEY (tenant_id, run_id),
            UNIQUE (tenant_id, slot)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS readiness_findings (
            tenant_id TEXT NOT NULL,
            finding_id TEXT NOT NULL,
            criterion_id TEXT NOT NULL,
            criterion_version INTEGER NOT NULL,
            scope_kind TEXT NOT NULL CHECK (scope_kind IN ('project', 'release', 'pod')),
            scope_id TEXT NOT NULL,
            project_id TEXT,
            state TEXT NOT NULL CHECK (state IN ('covered', 'missing', 'unsure')),
            done BOOLEAN NOT NULL DEFAULT false,
            decided_by TEXT NOT NULL CHECK (decided_by IN ('rules', 'model', 'person')),
            evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
            candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
            reason TEXT NOT NULL DEFAULT '',
            confidence REAL,
            urgency TEXT NOT NULL,
            due_on DATE,
            delivery_date DATE,
            stage_key TEXT,
            held BOOLEAN NOT NULL DEFAULT false,
            applies BOOLEAN NOT NULL DEFAULT true,
            person_decision TEXT CHECK (person_decision IN ('not_applicable', 'linked')),
            person_evidence JSONB,
            person_reason TEXT,
            person_by TEXT,
            person_at TIMESTAMPTZ,
            fingerprint TEXT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL,
            window_entered_at TIMESTAMPTZ,
            last_run_id TEXT,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, finding_id),
            UNIQUE (tenant_id, criterion_id, scope_kind, scope_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS readiness_findings_by_scope
            ON readiness_findings (tenant_id, scope_kind, scope_id);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS readiness_findings_by_project
            ON readiness_findings (tenant_id, project_id);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS readiness_suggestions (
            tenant_id TEXT NOT NULL,
            suggestion_id TEXT NOT NULL,
            finding_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN
                ('open', 'creating', 'created', 'dismissed', 'superseded')),
            version INTEGER NOT NULL DEFAULT 1,
            criterion_version INTEGER NOT NULL DEFAULT 1,
            draft JSONB NOT NULL,
            edited_by TEXT,
            marker_label TEXT NOT NULL,
            created_issue_key TEXT,
            created_by TEXT,
            created_at TIMESTAMPTZ,
            dismissed_by TEXT,
            dismissed_reason TEXT,
            dismissed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (tenant_id, suggestion_id),
            UNIQUE (tenant_id, finding_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS readiness_actions (
            tenant_id TEXT NOT NULL,
            action_id TEXT NOT NULL,
            at TIMESTAMPTZ NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            finding_id TEXT,
            suggestion_id TEXT,
            criterion_id TEXT,
            before JSONB,
            after JSONB,
            reason TEXT,
            correlation_id TEXT,
            PRIMARY KEY (tenant_id, action_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS readiness_actions_by_finding
            ON readiness_actions (tenant_id, finding_id, at);
        """
    )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table};")
