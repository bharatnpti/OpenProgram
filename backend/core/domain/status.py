from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class StatusSource(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    STALE = "stale"
    UNKNOWN = "unknown"


class Mood(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


@dataclass(frozen=True, kw_only=True)
class CheckInSignals:
    progress_note: str
    blockers: tuple[str, ...] = ()
    eta_change_days: int | None = None
    mood: Mood | None = None


@dataclass(frozen=True, kw_only=True)
class CheckIn:
    tenant_id: str
    developer_id: str
    correlation_id: str
    asked_at: datetime
    replied_at: datetime | None
    raw_reply: str | None
    signals: CheckInSignals | None


@dataclass(frozen=True, kw_only=True)
class DeveloperStatus:
    tenant_id: str
    developer_id: str
    as_of: date
    source: StatusSource
    blockers: tuple[str, ...]
    summary: str
