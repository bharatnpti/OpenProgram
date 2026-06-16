from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, kw_only=True)
class HeartbeatInput:
    tenant_id: str
    heartbeat_id: str


@dataclass(frozen=True, kw_only=True)
class HeartbeatResult:
    tenant_id: str
    heartbeat_id: str
    status: str
    recorded_at: str


@dataclass(frozen=True, kw_only=True)
class ScheduleBootstrapResult:
    schedule_id: str
    status: str


def record_heartbeat(
    payload: HeartbeatInput, recorded_at: datetime | None = None
) -> HeartbeatResult:
    return HeartbeatResult(
        tenant_id=payload.tenant_id,
        heartbeat_id=payload.heartbeat_id,
        status="ok",
        recorded_at=(recorded_at or datetime.now(tz=UTC)).isoformat(),
    )
