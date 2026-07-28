from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
    tenant_id: str
    developer_id: str
    local_time: time = time(9, 30)
    timezone: str | None = None
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    reply_wait_seconds: int = 14400
    final_reply_wait_seconds: int = 28800
    write_back_consent: WriteBackConsent = WriteBackConsent.ALWAYS_ASK


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
