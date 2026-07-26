"""Drop dead infrastructure: pgvector store and Apache AGE graph.

Backs Plan 04 section 4c (remove dead infrastructure). Two subsystems are
removed because nothing in the application reads from them:

* The ``vector_items`` table and the ``vector`` (pgvector) extension backed a
  ``VectorStore`` port that had no adapter callers.
* The ``openprogram_graph`` Apache AGE graph and the ``age`` extension were
  written on every graph mutation but never read -- the relational
  ``graph_nodes``/``graph_edges`` recursive-CTE path is the real source of
  truth and is left fully intact.

The graph drop is best-effort: it is wrapped in a DO/EXCEPTION block so the
migration still succeeds on databases where AGE was never installed.

``downgrade`` is intentionally a no-op: these objects are dead, so re-creating
an empty graph/extension on downgrade would restore infrastructure that no
code populates or queries.
"""

from __future__ import annotations

from alembic import op

revision = "0024_remove_dead_infra"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0023_narrative_briefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector: drop the table before the extension it depends on.
    op.execute("DROP TABLE IF EXISTS vector_items;")
    op.execute("DROP EXTENSION IF EXISTS vector;")

    # Apache AGE: best-effort drop of the graph, tolerant of AGE being absent.
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
    op.execute("DROP EXTENSION IF EXISTS age CASCADE;")


def downgrade() -> None:
    # No-op: the dropped vector store and AGE graph are dead infrastructure
    # with no code that would repopulate them, so there is nothing useful to
    # restore on downgrade.
    pass
