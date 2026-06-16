from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from temporalio import activity, workflow


@dataclass(frozen=True, kw_only=True)
class HeartbeatInput:
    tenant_id: str
    heartbeat_id: str


@dataclass(frozen=True, kw_only=True)
class HeartbeatResult:
    tenant_id: str
    heartbeat_id: str
    status: str
    recorded_at: datetime


@activity.defn
async def record_heartbeat_activity(payload: HeartbeatInput) -> HeartbeatResult:
    return HeartbeatResult(
        tenant_id=payload.tenant_id,
        heartbeat_id=payload.heartbeat_id,
        status="ok",
        recorded_at=datetime.now(tz=UTC),
    )


@workflow.defn
class HeartbeatWorkflow:
    @workflow.run
    async def run(self, payload: HeartbeatInput) -> HeartbeatResult:
        return await record_heartbeat_activity(payload)
