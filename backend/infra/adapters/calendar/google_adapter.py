from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import cast
from urllib.parse import quote

import httpx
from opentelemetry import trace

from core.domain.errors import ProviderUnavailable, SecretNotFound
from core.domain.graph import JsonScalar
from core.domain.integrations import CalendarEvent, UserRef
from core.ports.secrets import SecretRef, SecretStore

_tracer = trace.get_tracer("pulseops.adapters.calendar.google")


@dataclass(frozen=True)
class GoogleCalendarCredentials:
    base_url: str
    token: str
    calendar_id: str | None


@dataclass(frozen=True)
class GoogleCalendarAdapter:
    base_url: str = "https://www.googleapis.com/calendar/v3"
    token: str | None = None
    token_factory: Callable[[], str] | None = None
    calendar_id: str | None = None
    secret_store: SecretStore | None = None
    timeout_seconds: float = 10.0

    async def list_events(self, user: UserRef, start: date, end: date) -> list[CalendarEvent]:
        with _tracer.start_as_current_span("google_calendar.list_events"):
            credentials = await self._credentials(user.tenant_id)
            calendar_id = _calendar_id(credentials.calendar_id, user)
            payload = await self._get(
                credentials,
                f"/calendars/{quote(calendar_id, safe='')}/events",
                params={
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "timeMin": _rfc3339_day_start(start),
                    "timeMax": _rfc3339_day_start(end),
                },
            )
            return [_map_event(user, item) for item in _items(payload)]

    async def _get(
        self,
        credentials: GoogleCalendarCredentials,
        path: str,
        *,
        params: Mapping[str, str],
    ) -> Mapping[str, object]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {credentials.token}",
        }
        try:
            async with httpx.AsyncClient(
                base_url=credentials.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.get(path, headers=headers, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("calendar request failed") from exc

        if not isinstance(payload, Mapping):
            raise ProviderUnavailable("calendar response was not an object")
        return cast(Mapping[str, object], payload)

    async def _credentials(self, tenant_id: str) -> GoogleCalendarCredentials:
        base_url = self.base_url or await self._secret(tenant_id, "base_url")
        token = self.token_factory() if self.token_factory is not None else self.token
        token = token or await self._secret(tenant_id, "token")
        calendar_id = self.calendar_id or await self._secret(tenant_id, "calendar_id")
        if not base_url or not token:
            raise ProviderUnavailable("calendar credentials are not configured")
        return GoogleCalendarCredentials(
            base_url=base_url.rstrip("/"),
            token=token,
            calendar_id=calendar_id,
        )

    async def _secret(self, tenant_id: str, key: str) -> str | None:
        if self.secret_store is None:
            return None
        try:
            return await self.secret_store.get(
                SecretRef(tenant_id=tenant_id, connector="google_calendar", key=key)
            )
        except SecretNotFound:
            return None


def _map_event(user: UserRef, payload: Mapping[str, object]) -> CalendarEvent:
    start_payload = _mapping_field(payload, "start")
    end_payload = _mapping_field(payload, "end")
    starts_on = _event_date(start_payload)
    ends_on = _event_date(end_payload)
    kind = _event_kind(payload)
    timezone = _timezone(start_payload, end_payload)
    blocks_availability = _blocks_availability(kind, payload)
    return CalendarEvent(
        tenant_id=user.tenant_id,
        user=user,
        starts_on=starts_on,
        ends_on=ends_on,
        kind=kind,
        metadata=_metadata(
            {
                "timezone": timezone,
                "status": payload.get("status"),
                "availability": "unavailable" if blocks_availability else "available",
                "blocks_availability": blocks_availability,
                "pto": kind in {"pto", "out_of_office", "vacation", "leave"},
            }
        ),
    )


def _items(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    value = payload.get("items")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)]


def _calendar_id(configured: str | None, user: UserRef) -> str:
    if not configured:
        return user.external_id
    return configured.replace("{user}", user.external_id)


def _event_date(payload: Mapping[str, object]) -> date:
    date_value = payload.get("date")
    if isinstance(date_value, str) and date_value:
        try:
            return date.fromisoformat(date_value)
        except ValueError as exc:
            raise ProviderUnavailable("calendar event contained invalid date") from exc
    datetime_value = payload.get("dateTime")
    if isinstance(datetime_value, str) and datetime_value:
        try:
            return datetime.fromisoformat(datetime_value.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise ProviderUnavailable("calendar event contained invalid datetime") from exc
    raise ProviderUnavailable("calendar event missing start or end date")


def _event_kind(payload: Mapping[str, object]) -> str:
    event_type = _optional_string(payload, "eventType")
    summary = (_optional_string(payload, "summary") or "").lower()
    if event_type == "outOfOffice":
        return "out_of_office"
    if "pto" in summary:
        return "pto"
    if "ooo" in summary or "out of office" in summary:
        return "out_of_office"
    if "vacation" in summary:
        return "vacation"
    if "leave" in summary:
        return "leave"
    transparency = _optional_string(payload, "transparency")
    if transparency == "transparent":
        return "available"
    return "busy"


def _blocks_availability(kind: str, payload: Mapping[str, object]) -> bool:
    if kind in {"pto", "out_of_office", "vacation", "leave"}:
        return True
    return _optional_string(payload, "transparency") != "transparent"


def _timezone(
    start_payload: Mapping[str, object],
    end_payload: Mapping[str, object],
) -> str | None:
    return _optional_string(start_payload, "timeZone") or _optional_string(end_payload, "timeZone")


def _mapping_field(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    raise ProviderUnavailable(f"calendar payload missing object field {key}")


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _metadata(values: Mapping[str, object]) -> Mapping[str, JsonScalar]:
    return {
        key: value
        for key, value in values.items()
        if value is None or isinstance(value, str | int | float | bool)
    }


def _rfc3339_day_start(value: date) -> str:
    return datetime.combine(value, time.min, tzinfo=UTC).isoformat().replace("+00:00", "Z")
