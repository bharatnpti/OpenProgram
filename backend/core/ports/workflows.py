from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from core.domain.workflows import (
    CheckinReconcileScheduleConfig,
    CheckinScheduleConfig,
    ConversationPurgeScheduleConfig,
    CrossPersonNotifyRetryScheduleConfig,
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

    async def ensure_cross_person_notify_retry_schedule(
        self, config: CrossPersonNotifyRetryScheduleConfig
    ) -> ScheduleBootstrapResult: ...

    async def ensure_sync_schedules(
        self, configs: Sequence[SyncScheduleConfig]
    ) -> list[ScheduleBootstrapResult]: ...

    async def remove_schedule(self, schedule_id: str) -> ScheduleBootstrapResult:
        """Delete a schedule whose feature is switched off: status removed, or absent."""
        ...

    async def dispatch_developer_checkin(self, input: DeveloperCheckinDispatch) -> str: ...

    async def arm_reply_coalesce(
        self, conversation_key: str, tenant_id: str, *, burst_key: str
    ) -> None:
        """Start, or reset the debounce of, the coalesce workflow for one burst.

        ``burst_key`` names the buffered burst (see ``reply_burst_key``). An
        engine whose workflow ids dedupe forever must fold it into the id, so a
        message after a finished burst starts a new workflow.
        """
        ...

    async def dispatch_sync(self, input: SyncDispatchInput) -> str: ...


class RollupRefresher(Protocol):
    async def refresh_rollup(self, tenant_id: str, as_of: date) -> None:
        """Re-record the tenant's rollup for ``as_of`` soon, outside the hourly schedule.

        For a change made outside a check-in (a blocker the merge pass, a
        counterpart's reply or the console resolved): the hourly rollup may
        have run just before it. Requests that arrive close together run once,
        after the last of them, and the run reads what is stored then. Safe to
        call from anywhere, a workflow step included.
        """
        ...


class WorkflowWorker(Protocol):
    async def run(self) -> None: ...
