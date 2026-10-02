"""Scrub developer status summaries stored as raw check-in prompt context.

Before inferred summaries were built from keyed fields, a missed check-in
stored ``"No confirmed check-in after a nudge. Inferred from context: "``
followed by the check-in DM's LLM prompt context: every recent fact as
``key=value`` pairs, including text taken from replies. Persona APIs return a
summary as stored, so those rows still put that text in front of people.

The legacy text always runs to the end of the summary, and it is not only at
the start: a stale status quoted the previous summary after its own "Last known
... status on ...:" lead, and confirming copied a summary verbatim. So every
row holding the legacy sentence, in any tenant and of any source, has
everything from that sentence on replaced with a neutral one; the generated
wording before it is kept. A summary that is just the legacy text becomes
exactly the neutral sentence. The sentence is only ever produced by the old
code, so no other summary matches.

Forward-only: the downgrade does not bring the scrubbed text back, since that
would bring back the leak.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_scrub_legacy_inferred_summaries"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0029_backfill_developer_blockers"
branch_labels = None
depends_on = None

LEGACY_INFERRED_SENTENCE = "No confirmed check-in after a nudge. Inferred from context: "
NEUTRAL_INFERRED_SUMMARY = "No confirmed check-in after a nudge. Inferred from recent activity."

# `strpos` is 1-based and 0 when absent, so `substr(..., 1, 0)` keeps nothing
# of a summary that starts with the legacy sentence.
SCRUB_SQL = """
UPDATE developer_statuses
SET summary = substr(summary, 1, strpos(summary, :legacy) - 1) || :neutral,
    updated_at = CURRENT_TIMESTAMP
WHERE strpos(summary, :legacy) > 0
"""


def upgrade() -> None:
    op.execute(
        sa.text(SCRUB_SQL).bindparams(
            sa.bindparam("legacy", LEGACY_INFERRED_SENTENCE, type_=sa.Text()),
            sa.bindparam("neutral", NEUTRAL_INFERRED_SUMMARY, type_=sa.Text()),
        )
    )


def downgrade() -> None:
    # Forward-only: the scrubbed text is not restored.
    pass
