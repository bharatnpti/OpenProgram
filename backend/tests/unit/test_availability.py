from __future__ import annotations

from datetime import date

from core.application.availability import AvailabilityService
from core.domain.integrations import CalendarEvent, UserRef
from tests.contract.fakes import FakeCalendarProvider


async def test_availability_marks_pto_event_unavailable_with_event_timezone() -> None:
    user = UserRef(tenant_id="demo", external_id="dev-1")
    service = AvailabilityService(
        FakeCalendarProvider(
            events=[
                CalendarEvent(
                    tenant_id="demo",
                    user=user,
                    starts_on=date(2026, 1, 10),
                    ends_on=date(2026, 1, 11),
                    kind="out of office",
                    metadata={"timezone": "Europe/Berlin"},
                )
            ]
        )
    )

    availability = await service.availability_for(
        user,
        date(2026, 1, 10),
        default_timezone="UTC",
    )

    assert availability.available is False
    assert availability.timezone == "Europe/Berlin"
    assert len(availability.blocking_events) == 1


async def test_availability_defaults_available_for_non_blocking_events() -> None:
    user = UserRef(tenant_id="demo", external_id="dev-1")
    service = AvailabilityService(
        FakeCalendarProvider(
            events=[
                CalendarEvent(
                    tenant_id="demo",
                    user=user,
                    starts_on=date(2026, 1, 10),
                    ends_on=date(2026, 1, 10),
                    kind="focus",
                )
            ]
        )
    )

    availability = await service.availability_for(
        user,
        date(2026, 1, 10),
        default_timezone="Asia/Kolkata",
    )

    assert availability.available is True
    assert availability.timezone == "Asia/Kolkata"
    assert availability.blocking_events == ()


async def test_availability_respects_blocks_availability_metadata_before_kind() -> None:
    user = UserRef(tenant_id="demo", external_id="dev-1")
    service = AvailabilityService(
        FakeCalendarProvider(
            events=[
                CalendarEvent(
                    tenant_id="demo",
                    user=user,
                    starts_on=date(2026, 1, 10),
                    ends_on=date(2026, 1, 11),
                    kind="busy",
                    metadata={"blocks_availability": True},
                )
            ]
        )
    )

    availability = await service.availability_for(
        user,
        date(2026, 1, 10),
        default_timezone="UTC",
    )

    assert availability.available is False
    assert availability.blocking_events[0].kind == "busy"
