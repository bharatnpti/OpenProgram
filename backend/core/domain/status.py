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
