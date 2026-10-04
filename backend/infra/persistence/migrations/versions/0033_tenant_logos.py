"""Store each tenant's console logo.

The console header showed a fixed mark for every tenant. An admin can now
upload the tenant's own logo. The tenant id is the primary key, so a tenant has
at most one logo; removing it deletes the row and the header goes back to the
default mark. The image is kept as bytes with its type, its SHA-256 and who
replaced it last. Only PNG, JPEG and WebP are accepted, never SVG.
"""

from __future__ import annotations

from alembic import op

revision = "0033_tenant_logos"
down_revision = "0032_cross_person_notify_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_logos (
            tenant_id TEXT PRIMARY KEY,
            content_type TEXT NOT NULL
                CHECK (content_type IN ('image/png', 'image/jpeg', 'image/webp')),
            data BYTEA NOT NULL,
            sha256 TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by TEXT NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_logos;")
