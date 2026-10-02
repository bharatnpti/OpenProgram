"""Let a member's check-in preference leave fields to the team default.

A member's first save used to copy the team defaults (check-in time, days,
reply windows) into their row, so a later change to the defaults never reached
them. The schedule columns become nullable and lose their column defaults: a
NULL now means "not set for this member" and is resolved from the deployment's
defaults when read and when dispatched.

Existing rows are left exactly as they are. They can't tell a copied default
from a value someone chose, so they keep pinning whatever they hold; clearing a
field in the admin console makes it follow the team default again.

Downgrade is possible but lossy: NULLs are filled with the old column defaults
(09:30, Monday to Friday, 4 h and 8 h), not with the deployment's settings, so
anyone who was following a different default is pinned to those values.
"""

from __future__ import annotations

from alembic import op

revision = "0031_checkin_preferences_inherit_defaults"
# Alembic links to the revision id, not the longer migration filename.
down_revision = "0030_scrub_legacy_inferred_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE checkin_preferences
        ALTER COLUMN local_time DROP NOT NULL,
        ALTER COLUMN local_time DROP DEFAULT,
        ALTER COLUMN weekdays DROP NOT NULL,
        ALTER COLUMN weekdays DROP DEFAULT,
        ALTER COLUMN reply_wait_seconds DROP NOT NULL,
        ALTER COLUMN reply_wait_seconds DROP DEFAULT,
        ALTER COLUMN final_reply_wait_seconds DROP NOT NULL,
        ALTER COLUMN final_reply_wait_seconds DROP DEFAULT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE checkin_preferences
        SET local_time = COALESCE(local_time, TIME '09:30'),
            weekdays = COALESCE(weekdays, '{"items": [0, 1, 2, 3, 4]}'::jsonb),
            reply_wait_seconds = COALESCE(reply_wait_seconds, 14400),
            final_reply_wait_seconds = COALESCE(final_reply_wait_seconds, 28800)
        WHERE local_time IS NULL
           OR weekdays IS NULL
           OR reply_wait_seconds IS NULL
           OR final_reply_wait_seconds IS NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE checkin_preferences
        ALTER COLUMN local_time SET DEFAULT TIME '09:30',
        ALTER COLUMN local_time SET NOT NULL,
        ALTER COLUMN weekdays SET DEFAULT '{"items": [0, 1, 2, 3, 4]}'::jsonb,
        ALTER COLUMN weekdays SET NOT NULL,
        ALTER COLUMN reply_wait_seconds SET DEFAULT 14400,
        ALTER COLUMN reply_wait_seconds SET NOT NULL,
        ALTER COLUMN final_reply_wait_seconds SET DEFAULT 28800,
        ALTER COLUMN final_reply_wait_seconds SET NOT NULL;
        """
    )
