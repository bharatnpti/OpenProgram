from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

type WorkflowPayloadValue = str | int | float | bool | None


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


@dataclass(frozen=True, kw_only=True)
class CheckinScheduleConfig:
    schedule_id: str
    tenant_id: str
    cron: str


@dataclass(frozen=True, kw_only=True)
class DeveloperCheckinDispatch:
    tenant_id: str
    developer_id: str
    developer_name: str | None = None
    chat_external_id: str | None = None
    checkin_date: str | None = None


@dataclass(frozen=True, kw_only=True)
class CheckinFanoutInput:
    tenant_id: str
    checkin_date: str


@dataclass(frozen=True, kw_only=True)
class CheckinFanoutResult:
    tenant_id: str
    checkin_date: str
    dispatched: int
    workflow_ids: list[str]


@dataclass(frozen=True, kw_only=True)
class SyncDispatchInput:
    tenant_id: str
    connector: str
    scope: str
    payload: dict[str, WorkflowPayloadValue]


@dataclass(frozen=True, kw_only=True)
class SyncScheduleConfig:
    schedule_id: str
    tenant_id: str
    connector: str
    scope: str
    payload: dict[str, WorkflowPayloadValue]
    cron: str


@dataclass(frozen=True, kw_only=True)
class WorkflowDispatchResult:
    workflow_id: str
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
