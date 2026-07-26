from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

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


class WorkflowScheduler(Protocol):
    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult: ...

    async def ensure_checkin_fanout_schedule(
        self, config: CheckinScheduleConfig
    ) -> ScheduleBootstrapResult: ...

    async def ensure_checkin_reconcile_schedule(
        self, config: CheckinReconcileScheduleConfig
    ) -> ScheduleBootstrapResult: ...

    async def ensure_conversation_purge_schedule(
        self, config: ConversationPurgeScheduleConfig
    ) -> ScheduleBootstrapResult: ...

    async def ensure_inbound_sweeper_schedule(
        self, config: InboundSweeperScheduleConfig
    ) -> ScheduleBootstrapResult: ...

    async def ensure_sync_schedules(
        self, configs: Sequence[SyncScheduleConfig]
    ) -> list[ScheduleBootstrapResult]: ...

    async def dispatch_developer_checkin(self, input: DeveloperCheckinDispatch) -> str: ...

    async def arm_reply_coalesce(self, conversation_key: str, tenant_id: str) -> None: ...

    async def dispatch_sync(self, input: SyncDispatchInput) -> str: ...


class WorkflowWorker(Protocol):
    async def run(self) -> None: ...
