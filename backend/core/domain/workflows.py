from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

type WorkflowPayloadValue = str | int | float | bool | None
type CheckinReconcileStatus = Literal["skipped_early", "no_missing", "dispatched"]


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
class CheckinReconcileScheduleConfig:
    schedule_id: str
    tenant_id: str
    cron: str
    after_local_time: str
    timezone: str


@dataclass(frozen=True, kw_only=True)
class ConversationPurgeScheduleConfig:
    schedule_id: str
    tenant_id: str
    retention_days: int
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
class CheckinReconcileInput:
    tenant_id: str
    observed_at: str | None = None
    after_local_time: str = "09:45"
    timezone: str = "UTC"


@dataclass(frozen=True, kw_only=True)
class CheckinReconcileResult:
    tenant_id: str
    checkin_date: str
    status: CheckinReconcileStatus
    dispatched: int
    workflow_ids: list[str]
    skipped_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class CheckinReconcileDispatchPlan:
    result: CheckinReconcileResult
    dispatches: list[DeveloperCheckinDispatch]


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
class DirectorySyncInput:
    tenant_id: str


@dataclass(frozen=True, kw_only=True)
class DirectorySyncResult:
    tenant_id: str
    synced_count: int
    deactivated_count: int


@dataclass(frozen=True, kw_only=True)
class ConversationPurgeInput:
    tenant_id: str
    retention_days: int
    now: str | None = None


@dataclass(frozen=True, kw_only=True)
class ConversationPurgeResult:
    tenant_id: str
    cutoff: str
    deleted_count: int
    checkin_raw_cleared: int = 0
    inbound_events_cleared: int = 0


@dataclass(frozen=True, kw_only=True)
class ReplyCoalesceInput:
    tenant_id: str
    conversation_key: str
    debounce_seconds: int = 30


@dataclass(frozen=True, kw_only=True)
class ReplyCoalesceResult:
    tenant_id: str
    conversation_key: str
    processed: int
    passes: int


@dataclass(frozen=True, kw_only=True)
class InboundSweeperInput:
    tenant_id: str
    grace_seconds: int
    now: str | None = None


@dataclass(frozen=True, kw_only=True)
class InboundSweeperResult:
    tenant_id: str
    rearmed: int
    conversation_keys: list[str]


@dataclass(frozen=True, kw_only=True)
class InboundSweeperScheduleConfig:
    schedule_id: str
    tenant_id: str
    cron: str
    grace_seconds: int


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
