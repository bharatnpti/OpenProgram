from __future__ import annotations

from datetime import time

import pytest

from config.settings import Settings
from core.domain.status import CheckInSendKind, CheckInSendSchedule, checkin_send_schedule

WEEKDAYS = (0, 1, 2, 3, 4)
EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)
WEEKLY = CheckInSendKind.WEEKLY
DATES = CheckInSendKind.DATES
OTHER = CheckInSendKind.OTHER


def test_the_default_schedule_is_09_30_utc_monday_to_friday(settings: Settings) -> None:
    assert settings.checkin_send_schedule() == CheckInSendSchedule(
        cron="30 9 * * 1-5",
        kind=WEEKLY,
        timezone="UTC",
        local_time=time(9, 30),
        weekdays=WEEKDAYS,
    )


def test_the_schedule_follows_the_configured_cron(settings: Settings) -> None:
    configured = settings.model_copy(update={"checkin_fanout_cron": "0 7 * * 1-4"})

    assert configured.checkin_send_schedule() == CheckInSendSchedule(
        cron="0 7 * * 1-4", kind=WEEKLY, local_time=time(7, 0), weekdays=(0, 1, 2, 3)
    )


@pytest.mark.parametrize(
    ("cron", "local_time", "weekdays"),
    [
        ("30 9 * * 1-5", time(9, 30), WEEKDAYS),
        # All seven days is the one weekly schedule that is every day.
        ("0 9 * * *", time(9, 0), EVERY_DAY),
        ("0 7 ? * *", time(7, 0), EVERY_DAY),
        ("@daily", time(0, 0), EVERY_DAY),
        # DBOS reads a six-field cron with the seconds first.
        ("0 30 9 * * 1-5", time(9, 30), WEEKDAYS),
        ("15 30 9 * * 1-5", time(9, 30, 15), WEEKDAYS),
        ("30 9 * * MON-FRI", time(9, 30), WEEKDAYS),
        ("30 9 * * mon,wed,fri", time(9, 30), (0, 2, 4)),
        # Sunday is 0 or 7 in a cron, and 6 with Monday 0.
        ("0 9 * * 0,6", time(9, 0), (5, 6)),
        ("30 9 * * 7", time(9, 30), (6,)),
        ("@weekly", time(0, 0), (6,)),
        ("30 9 * * 1-5/2", time(9, 30), (0, 2, 4)),
        # Sunday, Tuesday, Thursday, Saturday.
        ("30 9 * * */2", time(9, 30), (1, 3, 5, 6)),
    ],
)
def test_one_time_on_days_of_the_week_is_weekly(
    cron: str, local_time: time, weekdays: tuple[int, ...]
) -> None:
    assert checkin_send_schedule(cron) == CheckInSendSchedule(
        cron=cron, kind=WEEKLY, timezone="UTC", local_time=local_time, weekdays=weekdays
    )


@pytest.mark.parametrize(
    ("cron", "local_time", "month_days", "months"),
    [
        # qa2's paused schedule: 00:00 UTC on 1 January, and on no other day.
        ("0 0 1 1 *", time(0, 0), (1,), (1,)),
        ("@yearly", time(0, 0), (1,), (1,)),
        ("0 0 0 1 1 *", time(0, 0), (1,), (1,)),
        ("0 9 1,15 * *", time(9, 0), (1, 15), None),
        ("30 9 1 * ?", time(9, 30), (1,), None),
        ("@monthly", time(0, 0), (1,), None),
        ("0 9 * 1,7 *", time(9, 0), None, (1, 7)),
        ("0 9 15,1 JUL,jan *", time(9, 0), (1, 15), (1, 7)),
    ],
)
def test_one_time_on_listed_dates_is_dates(
    cron: str,
    local_time: time,
    month_days: tuple[int, ...] | None,
    months: tuple[int, ...] | None,
) -> None:
    schedule = checkin_send_schedule(cron)

    assert schedule == CheckInSendSchedule(
        cron=cron,
        kind=DATES,
        timezone="UTC",
        local_time=local_time,
        month_days=month_days,
        months=months,
    )
    # Dates have no days of the week: nothing reads them as every day.
    assert schedule.weekdays is None


@pytest.mark.parametrize(
    "cron",
    [
        # Steps and ranges in the time.
        "*/15 * * * *",
        "*/15 * * * 1-5",
        "0 9-17 * * 1-5",
        "0 9,15 * * 1-5",
        "*/30 30 9 * * 1-5",
        "@hourly",
        # Ranges and steps in the date.
        "30 9 1-7 * *",
        "30 9 * 1-6 *",
        "30 9 */2 * *",
        "30 9 L * *",
        # A day of the week beside a date: croniter asks on either.
        "30 9 1 * 1",
        "30 9 * 1-6 1-5",
        "30 9 * 1 1-5",
        # Out of range or unreadable.
        "30 9 32 * *",
        "30 9 1 13 *",
        "30 9 * * 8",
        "30 9 * * fri-mon",
        "30 9 * * 1#2",
        "30 24 * * 1-5",
        "30 9 * *",
        "0 30 9 * * 1-5 2027",
        "every day at nine",
    ],
)
def test_anything_else_is_other_and_keeps_only_the_cron(cron: str) -> None:
    assert checkin_send_schedule(cron) == CheckInSendSchedule(cron=cron, kind=OTHER)


def test_the_cron_is_kept_as_configured(settings: Settings) -> None:
    configured = settings.model_copy(update={"checkin_fanout_cron": "0 0 1 1 *"})

    assert configured.checkin_send_schedule().cron == "0 0 1 1 *"
    assert checkin_send_schedule("@YEARLY").cron == "@YEARLY"
