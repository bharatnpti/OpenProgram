from __future__ import annotations

from datetime import time

import pytest

from config.settings import Settings
from core.domain.status import CheckInSendSchedule, checkin_send_schedule

WEEKDAYS = (0, 1, 2, 3, 4)
EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)


def test_the_default_schedule_is_09_30_utc_monday_to_friday(settings: Settings) -> None:
    assert settings.checkin_send_schedule() == CheckInSendSchedule(
        cron="30 9 * * 1-5",
        timezone="UTC",
        local_time=time(9, 30),
        weekdays=WEEKDAYS,
    )


def test_the_schedule_follows_the_configured_cron(settings: Settings) -> None:
    configured = settings.model_copy(update={"checkin_fanout_cron": "0 7 * * 1-4"})

    assert configured.checkin_send_schedule() == CheckInSendSchedule(
        cron="0 7 * * 1-4", local_time=time(7, 0), weekdays=(0, 1, 2, 3)
    )


@pytest.mark.parametrize(
    ("cron", "local_time", "weekdays"),
    [
        ("30 9 * * 1-5", time(9, 30), WEEKDAYS),
        # DBOS reads a six-field cron with the seconds first.
        ("0 30 9 * * 1-5", time(9, 30), WEEKDAYS),
        ("15 30 9 * * 1-5", time(9, 30, 15), WEEKDAYS),
        ("0 7 * * *", time(7, 0), EVERY_DAY),
        ("0 7 ? * *", time(7, 0), EVERY_DAY),
        ("30 9 * * MON-FRI", time(9, 30), WEEKDAYS),
        ("30 9 * * mon,wed,fri", time(9, 30), (0, 2, 4)),
        # Sunday is 0 or 7 in a cron, and 6 with Monday 0.
        ("30 9 * * 0,6", time(9, 30), (5, 6)),
        ("30 9 * * 7", time(9, 30), (6,)),
        ("30 9 * * 1-5/2", time(9, 30), (0, 2, 4)),
        # Sunday, Tuesday, Thursday, Saturday.
        ("30 9 * * */2", time(9, 30), (1, 3, 5, 6)),
    ],
)
def test_a_cron_with_one_time_of_day_reads_as_that_time_and_its_days(
    cron: str, local_time: time, weekdays: tuple[int, ...]
) -> None:
    schedule = checkin_send_schedule(cron)

    assert schedule.timezone == "UTC"
    assert schedule.local_time == local_time
    assert schedule.weekdays == weekdays


def test_a_cron_running_several_times_a_day_has_no_one_time() -> None:
    schedule = checkin_send_schedule("*/15 * * * 1-5")

    assert schedule.local_time is None
    assert schedule.weekdays == WEEKDAYS


def test_days_of_the_month_give_no_days_of_the_week() -> None:
    assert checkin_send_schedule("30 9 1 * *").weekdays is None
    assert checkin_send_schedule("30 9 * 1-6 1-5").weekdays is None


@pytest.mark.parametrize("cron", ["@daily", "30 9 * *", "30 9 * * 8", "30 9 * * fri-mon"])
def test_a_cron_it_cannot_read_keeps_only_the_cron(cron: str) -> None:
    schedule = checkin_send_schedule(cron)

    assert schedule.cron == cron
    assert schedule.weekdays is None
    if len(cron.split()) != 5:
        assert schedule.local_time is None
