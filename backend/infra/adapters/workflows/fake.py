from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.domain.workflows import (
    CheckinReconcileScheduleConfig,
    CheckinScheduleConfig,
    ConversationPurgeScheduleConfig,
    DeveloperCheckinDispatch,
    InboundSweeperScheduleConfig,
    ScheduleBootstrapResult,
    SyncDispatchInput,
    SyncScheduleConfig,
)
from infra.workflows.dispatch import safe_workflow_id


@dataclass(frozen=True)
class FakeWorkflowScheduler:
    schedule_id: str

    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=self.schedule_id, status="ready")

    async def ensure_checkin_fanout_schedule(
        self, config: CheckinScheduleConfig
    ) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")

    async def ensure_checkin_reconcile_schedule(
        self, config: CheckinReconcileScheduleConfig
    ) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")

    async def ensure_conversation_purge_schedule(
        self, config: ConversationPurgeScheduleConfig
    ) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")

    async def ensure_inbound_sweeper_schedule(
        self, config: InboundSweeperScheduleConfig
    ) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")

    async def ensure_sync_schedules(
        self, configs: Sequence[SyncScheduleConfig]
    ) -> list[ScheduleBootstrapResult]:
        return [
            ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")
            for config in configs
        ]

    async def dispatch_developer_checkin(self, input: DeveloperCheckinDispatch) -> str:
        return safe_workflow_id(
            f"fake-checkin-{input.tenant_id}-{input.developer_id}-{input.checkin_date or 'today'}"
        )

    async def arm_reply_coalesce(self, conversation_key: str, tenant_id: str) -> None:
        return None

    async def dispatch_sync(self, input: SyncDispatchInput) -> str:
        return safe_workflow_id(f"fake-sync-{input.connector}-{input.scope}")


class FakeWorkflowWorker:
    async def run(self) -> None:
        return None


class FakeWorkflowReadinessProbe:
    async def check(self) -> bool:
        return True
