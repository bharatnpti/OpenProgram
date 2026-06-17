from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.domain.integrations import CalendarEvent, UserRef
from core.ports.calendar import CalendarProvider


@dataclass(frozen=True, kw_only=True)
class Availability:
    user: UserRef
    as_of: date
    available: bool
    timezone: str
    blocking_events: tuple[CalendarEvent, ...]


class AvailabilityService:
    def __init__(self, calendar_provider: CalendarProvider) -> None:
        self._calendar_provider = calendar_provider

    async def availability_for(
        self,
        user: UserRef,
        as_of: date,
        *,
        default_timezone: str = "UTC",
    ) -> Availability:
        events = await self._calendar_provider.list_events(
            user,
            as_of,
            as_of + timedelta(days=1),
        )
        blocking_events = tuple(
            event for event in events if _event_blocks_availability(event, as_of)
        )
        return Availability(
            user=user,
            as_of=as_of,
            available=not blocking_events,
            timezone=_timezone_from_events(blocking_events or tuple(events), default_timezone),
            blocking_events=blocking_events,
        )


def _event_blocks_availability(event: CalendarEvent, as_of: date) -> bool:
    return _kind_blocks_availability(event.kind) and _event_covers(event, as_of)


def _kind_blocks_availability(kind: str) -> bool:
    normalized = kind.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in {
        "pto",
        "ooo",
        "out_of_office",
        "unavailable",
        "vacation",
        "leave",
    }


def _event_covers(event: CalendarEvent, as_of: date) -> bool:
    if event.starts_on == event.ends_on:
        return event.starts_on == as_of
    return event.starts_on <= as_of < event.ends_on


def _timezone_from_events(events: tuple[CalendarEvent, ...], default_timezone: str) -> str:
    for event in events:
        for key in ("timezone", "time_zone", "tz"):
            value = event.metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return default_timezone
