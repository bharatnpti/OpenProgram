from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum


class StatusSource(StrEnum):
    CONFIRMED = "confirmed"
    PARTIAL = "partial"
    INFERRED = "inferred"
    STALE = "stale"
    UNKNOWN = "unknown"


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
