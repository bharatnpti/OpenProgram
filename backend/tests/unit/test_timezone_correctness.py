from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from api.dtos import CheckinPreferenceUpdateRequest
from core.domain.status import (
    CheckIn,
    CheckInPreference,
    CheckInSignals,
    local_date,
    resolve_timezone,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.workflows.daily_checkin import _scheduled_at


def test_resolve_timezone_prefers_preference_then_default_then_utc() -> None:
    assert resolve_timezone("Asia/Kolkata", "UTC") == ZoneInfo("Asia/Kolkata")
    assert resolve_timezone(None, "America/New_York") == ZoneInfo("America/New_York")
    # An unknown preference falls through to the tenant default.
    assert resolve_timezone("Not/AZone", "Europe/Berlin") == ZoneInfo("Europe/Berlin")
    # Everything invalid or blank ends at UTC without raising.
    assert resolve_timezone("Not/AZone", "Also/Bogus") == ZoneInfo("UTC")
    assert resolve_timezone(None, "") == ZoneInfo("UTC")


def test_local_date_uses_observer_timezone() -> None:
    instant = datetime(2026, 1, 10, 23, 30, tzinfo=UTC)
    assert local_date(instant, ZoneInfo("UTC")) == date(2026, 1, 10)
    # +05:30 rolls the same instant into the next calendar day.
    assert local_date(instant, ZoneInfo("Asia/Kolkata")) == date(2026, 1, 11)


def test_local_date_treats_naive_as_utc() -> None:
    naive = datetime(2026, 1, 10, 23, 30)
    assert local_date(naive, ZoneInfo("Asia/Kolkata")) == date(2026, 1, 11)


def _preference(local_time: time) -> CheckInPreference:
    return CheckInPreference(tenant_id="demo", developer_id="dev-1", local_time=local_time)


def test_scheduled_at_converts_local_wall_time_to_utc() -> None:
    scheduled = _scheduled_at(date(2026, 1, 10), _preference(time(9, 30)), "Asia/Kolkata")
    # 09:30 IST (UTC+5:30) == 04:00 UTC.
    assert scheduled == datetime(2026, 1, 10, 4, 0, tzinfo=UTC)


def test_scheduled_at_advances_past_spring_forward_gap() -> None:
    # 2026-03-08 02:30 America/New_York does not exist (clocks jump 02:00->03:00).
    scheduled = _scheduled_at(date(2026, 3, 8), _preference(time(2, 30)), "America/New_York")
    local = scheduled.astimezone(ZoneInfo("America/New_York"))
    # The non-existent wall time is advanced past the transition, not left at 02:30.
    assert local.hour == 3
    assert local.time() != time(2, 30)


def test_scheduled_at_falls_back_to_utc_for_unknown_zone() -> None:
    scheduled = _scheduled_at(date(2026, 1, 10), _preference(time(9, 30)), "Not/AZone")
    assert scheduled == datetime(2026, 1, 10, 9, 30, tzinfo=UTC)


def _checkin(
    developer_id: str,
    *,
    replied_at: datetime,
    checkin_date: date | None,
) -> CheckIn:
    return CheckIn(
        tenant_id="demo",
        developer_id=developer_id,
        correlation_id=f"corr-{developer_id}",
        asked_at=replied_at,
        replied_at=replied_at,
        raw_reply="done",
        signals=CheckInSignals(progress_note="done"),
        last_accessed_at=replied_at,
        checkin_date=checkin_date,
    )


async def test_roster_uses_local_checkin_date_not_utc_reply_boundary() -> None:
    store = InMemoryGraphStore()
    # Replied just after midnight UTC, but the developer's local check-in date is
    # the prior day. UTC-boundary logic would mis-flag this developer.
    await store.record_checkin(
        _checkin(
            "dev-local",
            replied_at=datetime(2026, 1, 11, 3, 0, tzinfo=UTC),
            checkin_date=date(2026, 1, 10),
        )
    )
    # Legacy row without a stored local date falls back to the UTC reply date.
    await store.record_checkin(
        _checkin(
            "dev-legacy",
            replied_at=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
            checkin_date=None,
        )
    )

    missing_on_local_date = await store.developers_without_checkin("demo", date(2026, 1, 10))
    assert "dev-local" not in missing_on_local_date
    assert "dev-legacy" not in missing_on_local_date

    # On the following local day the check-in no longer counts.
    missing_next_day = await store.developers_without_checkin("demo", date(2026, 1, 11))
    assert "dev-local" in missing_next_day


def test_checkin_preference_dto_rejects_invalid_timezone() -> None:
    assert CheckinPreferenceUpdateRequest(timezone="Asia/Kolkata").timezone == "Asia/Kolkata"
    assert CheckinPreferenceUpdateRequest(timezone=None).timezone is None
    with pytest.raises(ValidationError):
        CheckinPreferenceUpdateRequest(timezone="Not/AZone")
