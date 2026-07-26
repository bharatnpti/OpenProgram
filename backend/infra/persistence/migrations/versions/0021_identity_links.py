"""Add identity_links mapping developers to provider-specific external ids."""

from __future__ import annotations

from alembic import op

revision = "0021_identity_links"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0020_checkin_local_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Provider-neutral identity map keyed by (tenant_id, developer_id). The
    # developer_id is the canonical graph member id; the optional provider
    # columns let application code resolve the right external id per capability
    # port (for example querying Jira by jira_account_id). Plain relational
    # table -- no AGE graph projection is involved.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS identity_links (
            tenant_id TEXT NOT NULL,
            developer_id TEXT NOT NULL,
            chat_user_id TEXT,
            jira_account_id TEXT,
            jira_email TEXT,
            vcs_username TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, developer_id)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS identity_links;")
