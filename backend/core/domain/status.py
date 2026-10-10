from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.domain.blockers import BlockerReport


def resolve_timezone(pref_tz: str | None, tenant_default: str) -> ZoneInfo:
    """Resolve a check-in timezone, preferring the developer preference.

    Resolution falls back preference -> tenant default -> UTC. Invalid IANA names
    are skipped so legacy or unvalidated rows never crash local-date math; new
    preferences are validated at the API and settings boundaries before they are
    persisted. ``tenant_default`` is passed in explicitly to keep the domain free
    of any dependency on ``config.settings``.
    """
    for candidate in (pref_tz, tenant_default):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def local_date(instant: datetime, tz: ZoneInfo) -> date:
    """Return the calendar date of ``instant`` as observed in timezone ``tz``.

    Naive datetimes are treated as UTC so the result stays deterministic.
    """
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(tz).date()


class StatusSource(StrEnum):
    CONFIRMED = "confirmed"
    PARTIAL = "partial"
    INFERRED = "inferred"
    STALE = "stale"
    UNKNOWN = "unknown"


class WriteBackConsent(StrEnum):
    """Per-developer standing consent for automated issue-tracker write-back.

    ``always_ask`` (the safe default) never writes automatically; ``auto_apply``
    grants standing consent; ``never`` opts out entirely.
    """

    ALWAYS_ASK = "always_ask"
    AUTO_APPLY = "auto_apply"
    NEVER = "never"


@dataclass(frozen=True, kw_only=True)
class CrossPersonMention:
    raw_name: str
    kind: str
    note: str
    email: str | None = None


@dataclass(frozen=True, kw_only=True)
class IssueClaim:
    issue_key: str
    claimed_done: bool = False
    claimed_state: str | None = None
    note: str = ""


@dataclass(frozen=True, kw_only=True)
class CheckInSignals:
    progress_note: str
    blockers: tuple[str, ...] = ()
    eta_change_days: int | None = None
    blockers_answered: bool = False
    eta_answered: bool = False
    requests: tuple[CrossPersonMention, ...] = ()
    issue_updates: tuple[IssueClaim, ...] = ()
    # Structured per-blocker statements (description + optional issue key or
    # pod). When present, ``blockers`` mirrors their descriptions; legacy
    # replies without details keep the flat strings only.
    blocker_reports: tuple[BlockerReport, ...] = ()
    # Real blocker ids the reply explicitly resolved (mapped from the
    # bracketed handles shown in the prior-blockers prompt context).
    resolved_blocker_ids: tuple[str, ...] = ()
    # False when the model output could not be parsed into structured signals, so
    # downstream rollups must not treat the check-in as confirmed/green.
    parser_confident: bool = True


@dataclass(frozen=True, kw_only=True)
class CheckIn:
    tenant_id: str
    developer_id: str
    correlation_id: str
    asked_at: datetime
    replied_at: datetime | None
    raw_reply: str | None
    signals: CheckInSignals | None
    last_accessed_at: datetime | None = None
    # Developer-local calendar date this check-in belongs to. Used for
    # timezone-correct roster/roll-up queries instead of UTC boundaries on
    # ``replied_at``. Optional so legacy rows (pre-migration) degrade to the
    # prior UTC-day behaviour.
    checkin_date: date | None = None


@dataclass(frozen=True, kw_only=True)
class CheckInDay:
    """One person's check-ins on one day: when they were first asked, and first replied.

    Identifiers and times only, never a reply's text, so a count of who has
    answered a day's check-in can be shown to anyone who reads the heat map.
    """

    developer_id: str
    first_asked_at: datetime
    first_replied_at: datetime | None = None

    @property
    def answered(self) -> bool:
        return self.first_replied_at is not None


@dataclass(frozen=True, kw_only=True)
class CheckInCorrelation:
    tenant_id: str
    correlation_id: str
    developer_id: str
    chat_user_ref: str
    chat_thread_ref: str
    outbound_message_id: str
    asked_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class CheckInPreference:
    """What has been set for one member's check-ins, and nothing more.

    A ``None`` field was never set for this member, so it follows the team
    default (``CheckInDefaults``) when it is read or dispatched. Storing only
    what was set is what lets a later change to the defaults reach the member;
    copying the defaults in on the first save froze them.
    """

    tenant_id: str
    developer_id: str
    local_time: time | None = None
    timezone: str | None = None
    weekdays: tuple[int, ...] | None = None
    reply_wait_seconds: int | None = None
    final_reply_wait_seconds: int | None = None
    write_back_consent: WriteBackConsent = WriteBackConsent.ALWAYS_ASK


class CheckInPreferenceField(StrEnum):
    """The preference fields a member can leave to the team default, in display order."""

    LOCAL_TIME = "local_time"
    TIMEZONE = "timezone"
    WEEKDAYS = "weekdays"
    REPLY_WAIT_SECONDS = "reply_wait_seconds"
    FINAL_REPLY_WAIT_SECONDS = "final_reply_wait_seconds"


@dataclass(frozen=True, kw_only=True)
class CheckInDefaults:
    """The team's check-in defaults: what a member follows for anything not set for them.

    The values come from the deployment's settings; the domain keeps no
    dependency on ``config.settings``, so callers pass them in.
    """

    local_time: time = time(9, 30)
    timezone: str = "UTC"
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    reply_wait_seconds: int = 14400
    final_reply_wait_seconds: int = 28800


# The zone the check-in schedule is read in: the workflow layer registers the
# cron with no zone of its own, so every workflow provider reads it in UTC.
CHECKIN_SEND_TIMEZONE = "UTC"

_CRON_DAY_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
_CRON_MONTH_NAMES = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
# The shorthands the workflow layer's scheduler (croniter) expands; it gives croniter no hash id.
_CRON_ALIASES = {
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
_CRON_ANY = {"*", "?"}


class CheckInSendKind(StrEnum):
    """What the check-in schedule is, so a screen says only what is true of it.

    ``WEEKLY``: one time of day on days of the week, in every month (all seven
    days is every day). ``DATES``: one time of day on listed days of the month
    and/or in listed months, such as 1 January only. ``OTHER``: anything else,
    such as a step or a range in the time; only the cron says when. ``OFF``:
    the scheduled send is switched off (``OPENPROGRAM_CHECKIN_FANOUT_ENABLED``),
    so nobody is asked on a schedule, whatever the cron says.
    """

    WEEKLY = "weekly"
    DATES = "dates"
    OTHER = "other"
    OFF = "off"


@dataclass(frozen=True, kw_only=True)
class CheckInSendSchedule:
    """When the bot asks: one send of the check-ins for the whole tenant.

    The workflow layer starts one fan-out on ``cron``
    (``OPENPROGRAM_CHECKIN_FANOUT_CRON``), read in ``timezone`` (always UTC),
    and asks every member then. A member's own days only decide whether they
    are skipped on a send, judged by the send's UTC date; no member's own time
    is used. ``kind`` says how much of the cron is read:

    - ``WEEKLY``: ``local_time`` and ``weekdays`` (Monday 0; all seven is every
      day). ``30 9 * * 1-5`` is 09:30, Monday to Friday.
    - ``DATES``: ``local_time``, ``month_days`` (1 to 31; ``None`` for any day
      of the month) and ``months`` (1 to 12; ``None`` for every month), at least
      one of the two set. ``0 0 1 1 *`` is 00:00 on 1 January only.
    - ``OTHER``: only ``cron``.
    - ``OFF``: no send at all; ``cron`` is what would apply once it is on again,
      and nothing else is set.
    """

    cron: str
    kind: CheckInSendKind = CheckInSendKind.OTHER
    timezone: str = CHECKIN_SEND_TIMEZONE
    local_time: time | None = None
    weekdays: tuple[int, ...] | None = None
    month_days: tuple[int, ...] | None = None
    months: tuple[int, ...] | None = None


def checkin_send_schedule(cron: str, *, enabled: bool = True) -> CheckInSendSchedule:
    """Read the tenant's check-in cron as a weekly time, a time on dates, or neither.

    ``enabled`` false is ``OFF`` whatever the cron: the fan-out schedule is
    then removed, so no reading of the cron is true of when anyone is asked.

    Five fields, six with the seconds first (the workflow layer's scheduler runs
    croniter with ``second_at_beginning``), or one of croniter's ``@`` shorthands. Only a
    single second, minute and hour is read as a time. A day of the week
    restricted beside a day of the month or a month is ``OTHER``: croniter then
    asks on either kind of day (``day_or``), which neither words for weekdays
    nor words for dates would say.
    """
    if not enabled:
        return CheckInSendSchedule(cron=cron, kind=CheckInSendKind.OFF)
    fields = _CRON_ALIASES.get(cron.strip().lower(), cron).split()
    seconds = "0"
    if len(fields) == 6:
        seconds, *fields = fields
    if len(fields) != 5:
        return CheckInSendSchedule(cron=cron)
    minute, hour, day_of_month, month, day_of_week = (field.lower() for field in fields)
    second_value = _cron_single(seconds, 59)
    minute_value = _cron_single(minute, 59)
    hour_value = _cron_single(hour, 23)
    if second_value is None or minute_value is None or hour_value is None:
        return CheckInSendSchedule(cron=cron)
    send_time = time(hour_value, minute_value, second_value)
    any_day, any_month = day_of_month in _CRON_ANY, month == "*"
    if any_day and any_month:
        weekdays = _cron_weekdays(day_of_week)
        if weekdays is None:
            return CheckInSendSchedule(cron=cron)
        return CheckInSendSchedule(
            cron=cron, kind=CheckInSendKind.WEEKLY, local_time=send_time, weekdays=weekdays
        )
    if day_of_week not in _CRON_ANY:
        return CheckInSendSchedule(cron=cron)
    month_days = None if any_day else _cron_values(day_of_month, 1, 31, {})
    months = None if any_month else _cron_values(month, 1, 12, _CRON_MONTH_NAMES)
    if (not any_day and month_days is None) or (not any_month and months is None):
        return CheckInSendSchedule(cron=cron)
    return CheckInSendSchedule(
        cron=cron,
        kind=CheckInSendKind.DATES,
        local_time=send_time,
        month_days=month_days,
        months=months,
    )


def _cron_single(field: str, highest: int) -> int | None:
    return int(field) if field.isdigit() and int(field) <= highest else None


def _cron_values(
    field: str, lowest: int, highest: int, names: dict[str, int]
) -> tuple[int, ...] | None:
    """A field of single values ("1", "1,15", "jan,jul"); None for a range, a step or a word."""
    values: set[int] = set()
    for part in field.split(","):
        value = int(part) if part.isdigit() else names.get(part)
        if value is None or not lowest <= value <= highest:
            return None
        values.add(value)
    return tuple(sorted(values))


def _cron_weekdays(field: str) -> tuple[int, ...] | None:
    """A cron's day-of-week field (Sunday 0 or 7) as days with Monday 0."""
    if field in _CRON_ANY:
        return (0, 1, 2, 3, 4, 5, 6)
    days: set[int] = set()
    for part in field.split(","):
        base, slash, step_text = part.partition("/")
        if slash and not (step_text.isdigit() and int(step_text) > 0):
            return None
        step = int(step_text) if slash else 1
        if base == "*":
            start, end = 0, 6
        else:
            low, dash, high = base.partition("-")
            start_day = _cron_day(low)
            end_day = _cron_day(high) if dash else (6 if slash else start_day)
            if start_day is None or end_day is None or start_day > end_day:
                return None
            start, end = start_day, end_day
        days.update(range(start, end + 1, step))
    # Sunday is 0 (or 7) in a cron and 6 here.
    return tuple(sorted({(day - 1) % 7 for day in days}))


def _cron_day(text: str) -> int | None:
    if text.isdigit():
        return int(text) if int(text) <= 7 else None
    return _CRON_DAY_NAMES.get(text)


@dataclass(frozen=True, kw_only=True)
class EffectiveCheckInPreference:
    """A member's check-in preference with every gap filled from the team defaults.

    ``inherited`` names the fields that follow the defaults, in
    ``CheckInPreferenceField`` order, so a screen can say which values were set
    for this member.
    """

    tenant_id: str
    developer_id: str
    local_time: time
    timezone: str
    weekdays: tuple[int, ...]
    reply_wait_seconds: int
    final_reply_wait_seconds: int
    write_back_consent: WriteBackConsent
    inherited: tuple[CheckInPreferenceField, ...]
    defaults: CheckInDefaults


def effective_checkin_preference(
    tenant_id: str,
    developer_id: str,
    preference: CheckInPreference | None,
    defaults: CheckInDefaults,
) -> EffectiveCheckInPreference:
    """Resolve each field: the member's own value, else the team default.

    Resolved when read and when dispatched, never when saved, so a change to
    the defaults reaches everyone who hasn't set that field. An empty
    ``weekdays`` tuple is a stored value (an older row saved with no days), not
    a gap, and is kept as it is.
    """
    stored = preference or CheckInPreference(tenant_id=tenant_id, developer_id=developer_id)
    inherited = tuple(field for field in CheckInPreferenceField if getattr(stored, field) is None)
    return EffectiveCheckInPreference(
        tenant_id=tenant_id,
        developer_id=developer_id,
        local_time=stored.local_time if stored.local_time is not None else defaults.local_time,
        timezone=stored.timezone if stored.timezone is not None else defaults.timezone,
        weekdays=stored.weekdays if stored.weekdays is not None else defaults.weekdays,
        reply_wait_seconds=(
            stored.reply_wait_seconds
            if stored.reply_wait_seconds is not None
            else defaults.reply_wait_seconds
        ),
        final_reply_wait_seconds=(
            stored.final_reply_wait_seconds
            if stored.final_reply_wait_seconds is not None
            else defaults.final_reply_wait_seconds
        ),
        write_back_consent=stored.write_back_consent,
        inherited=inherited,
        defaults=defaults,
    )


@dataclass(frozen=True, kw_only=True)
class CheckInScheduleRun:
    tenant_id: str
    developer_id: str
    checkin_date: date
    correlation_id: str
    status: str
    scheduled_at: datetime
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class CheckInNudge:
    tenant_id: str
    correlation_id: str
    nudge_number: int
    sent_at: datetime | None = None
    outbound_message_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class CheckInClarification:
    tenant_id: str
    correlation_id: str
    clarification_number: int
    question: str
    sent_at: datetime | None = None
    outbound_message_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class DeveloperStatus:
    tenant_id: str
    developer_id: str
    as_of: date
    source: StatusSource
    blockers: tuple[str, ...]
    summary: str
    eta_change_days: int | None = None
    developer_confirmed: bool = False
    confirmed_at: datetime | None = None
