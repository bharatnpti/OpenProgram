from __future__ import annotations

from datetime import time

from core.domain.status import (
    CheckInDefaults,
    CheckInPreference,
    CheckInPreferenceField,
    WriteBackConsent,
    effective_checkin_preference,
)

DEFAULTS = CheckInDefaults(
    timezone="Asia/Kolkata",
    reply_wait_seconds=600,
    final_reply_wait_seconds=1200,
)


def test_a_member_with_no_row_follows_every_default() -> None:
    effective = effective_checkin_preference("demo", "dev-1", None, DEFAULTS)

    assert effective.developer_id == "dev-1"
    assert effective.local_time == time(9, 30)
    assert effective.timezone == "Asia/Kolkata"
    assert effective.weekdays == (0, 1, 2, 3, 4)
    assert effective.reply_wait_seconds == 600
    assert effective.final_reply_wait_seconds == 1200
    assert effective.write_back_consent is WriteBackConsent.ALWAYS_ASK
    assert effective.inherited == tuple(CheckInPreferenceField)
    assert effective.defaults == DEFAULTS


def test_a_member_value_wins_and_only_gaps_are_inherited() -> None:
    stored = CheckInPreference(
        tenant_id="demo",
        developer_id="dev-1",
        timezone="Europe/Berlin",
        reply_wait_seconds=0,
        write_back_consent=WriteBackConsent.NEVER,
    )

    effective = effective_checkin_preference("demo", "dev-1", stored, DEFAULTS)

    assert effective.timezone == "Europe/Berlin"
    # Zero is a value someone set, not a gap.
    assert effective.reply_wait_seconds == 0
    assert effective.final_reply_wait_seconds == 1200
    assert effective.write_back_consent is WriteBackConsent.NEVER
    assert effective.inherited == (
        CheckInPreferenceField.LOCAL_TIME,
        CheckInPreferenceField.WEEKDAYS,
        CheckInPreferenceField.FINAL_REPLY_WAIT_SECONDS,
    )


def test_an_older_row_saved_with_no_days_keeps_no_days() -> None:
    stored = CheckInPreference(tenant_id="demo", developer_id="dev-1", weekdays=())

    effective = effective_checkin_preference("demo", "dev-1", stored, DEFAULTS)

    assert effective.weekdays == ()
    assert CheckInPreferenceField.WEEKDAYS not in effective.inherited
