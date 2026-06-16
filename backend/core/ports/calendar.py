from __future__ import annotations

from datetime import date
from typing import Protocol

from core.domain.integrations import CalendarEvent, UserRef


class CalendarProvider(Protocol):
    async def list_events(self, user: UserRef, start: date, end: date) -> list[CalendarEvent]: ...
