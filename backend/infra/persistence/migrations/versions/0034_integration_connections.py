"""Store each tenant's connections to external systems, as an admin sets them up.

Until now the server's environment configured every integration. An admin can
now set up Jira, the code host, chat, mail and the rest from the console. A row
holds one connector's plain settings and the names of the secret fields that
have a value; the secret values themselves stay encrypted in connector_secrets,
under the same tenant and connector. The last connection test is kept as a
yes/no, a fixed sentence and its time, never an error text.
"""

from __future__ import annotations

from alembic import op

revision = "0034_integration_connections"
down_revision = "0033_tenant_logos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS integration_connections (
            tenant_id TEXT NOT NULL,
            connector TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT false,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            secret_keys TEXT[] NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by TEXT NOT NULL,
            last_test_ok BOOLEAN,
            last_test_message TEXT,
            last_test_at TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, connector)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS integration_connections;")
