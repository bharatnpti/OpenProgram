"""Restore the pgvector store and Apache AGE graph dropped by 0024.

Migration ``0024_remove_dead_infra`` removed both subsystems as dead. They are
being brought back into use, so this migration recreates them:

* the ``vector`` extension, the ``vector_items`` table, and its HNSW index --
  matching the definitions originally created by ``0001_phase_0_foundation``,
  including the ``OPENPROGRAM_EMBEDDING_DIMENSION`` sizing;
* the ``age`` extension and the ``openprogram_graph`` graph, which the graph
  repository writes to again on every node/edge mutation.

This is a forward migration rather than an edit to 0024, because 0024 has
already been applied to deployed databases. Every statement is guarded so the
migration is a no-op on a database that still has these objects (for example
one whose revision predates 0024).

The relational ``graph_nodes``/``graph_edges`` recursive-CTE path remains the
read source of truth; the AGE graph is a write-side mirror.
"""

from __future__ import annotations

import os

from alembic import op

revision = "0026_restore_vector_and_age_graph"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0025_dead_letters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    vector_type = f"vector({_embedding_dimension()})"
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_items (
            tenant_id TEXT NOT NULL,
            entity_kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            embedding __VECTOR_TYPE__ NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, entity_kind, entity_id)
        );
        """.replace("__VECTOR_TYPE__", vector_type)
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS vector_items_embedding_hnsw_idx
        ON vector_items USING hnsw (embedding vector_cosine_ops);
        """
    )

    op.execute("CREATE EXTENSION IF NOT EXISTS age;")
    op.execute("LOAD 'age';")
    op.execute('SET search_path = ag_catalog, "$user", public;')
    op.execute(
        """
        SELECT create_graph('openprogram_graph')
        WHERE NOT EXISTS (
            SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'openprogram_graph'
        );
        """
    )
    op.execute("SET search_path = public;")


def downgrade() -> None:
    """Drop the objects this migration created, but keep both extensions.

    Deliberately *not* a mirror of 0024's upgrade. 0024 dropped the ``age`` and
    ``vector`` extensions themselves, which breaks the downgrade path of every
    earlier revision that assumes they exist -- ``0015`` calls ``create_graph``
    and ``0001`` creates a ``vector`` column, so both fail with
    "function create_graph(unknown) does not exist" once the extension is gone.

    Extensions are database-wide shared objects rather than something this
    revision owns, so downgrading here removes only the graph and the table.
    Leaving the extensions installed is harmless (they hold no data on their
    own) and keeps ``downgrade`` past this revision working.
    """
    op.execute("DROP TABLE IF EXISTS vector_items;")
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE 'LOAD ''age''';
            PERFORM ag_catalog.drop_graph('openprogram_graph', true);
        EXCEPTION
            WHEN OTHERS THEN
                -- AGE not installed or graph already absent; nothing to drop.
                NULL;
        END
        $$;
        """
    )


def _embedding_dimension() -> int:
    raw_value = os.getenv("OPENPROGRAM_EMBEDDING_DIMENSION", "1536")
    try:
        dimension = int(raw_value)
    except ValueError as exc:
        raise ValueError("OPENPROGRAM_EMBEDDING_DIMENSION must be an integer") from exc
    if dimension <= 0:
        raise ValueError("OPENPROGRAM_EMBEDDING_DIMENSION must be positive")
    return dimension
