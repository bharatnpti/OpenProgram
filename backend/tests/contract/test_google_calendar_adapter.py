from __future__ import annotations

from datetime import date

import httpx
import respx

from core.domain.integrations import UserRef
from infra.adapters.calendar.google_adapter import GoogleCalendarAdapter


@respx.mock
async def test_google_calendar_adapter_maps_events_without_raw_payloads() -> None:
    adapter = GoogleCalendarAdapter(
        base_url="https://calendar.test/calendar/v3",
        token="ya29-test",
        calendar_id="{user}",
    )
    respx.get("https://calendar.test/calendar/v3/calendars/dev%40example.com/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "event-1",
                        "status": "confirmed",
                        "summary": "PTO private appointment",
                        "eventType": "outOfOffice",
                        "start": {"date": "2026-01-10", "timeZone": "Europe/Berlin"},
                        "end": {"date": "2026-01-11", "timeZone": "Europe/Berlin"},
                    },
                    {
                        "id": "event-2",
                        "status": "confirmed",
                        "summary": "Focus time",
                        "transparency": "transparent",
                        "start": {
                            "dateTime": "2026-01-12T09:00:00+05:30",
                            "timeZone": "Asia/Kolkata",
                        },
                        "end": {
                            "dateTime": "2026-01-12T10:00:00+05:30",
                            "timeZone": "Asia/Kolkata",
                        },
                    },
                ]
            },
        )
    )

    events = await adapter.list_events(
        UserRef(tenant_id="demo", external_id="dev@example.com"),
        date(2026, 1, 10),
        date(2026, 1, 13),
    )

    assert events[0].kind == "out_of_office"
    assert events[0].starts_on == date(2026, 1, 10)
    assert events[0].ends_on == date(2026, 1, 11)
    assert events[0].metadata["timezone"] == "Europe/Berlin"
    assert events[0].metadata["blocks_availability"] is True
    assert events[0].metadata["pto"] is True
    assert events[1].kind == "available"
    assert events[1].metadata["timezone"] == "Asia/Kolkata"
    assert "summary" not in events[0].metadata
    assert {call.request.method for call in respx.calls} == {"GET"}
