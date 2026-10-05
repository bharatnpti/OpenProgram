from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from temporalio import activity, workflow

from core.domain.workflows import (
    CheckinFanoutInput,
    CheckinFanoutResult,
    CheckinReconcileInput,
    CheckinReconcileResult,
    CheckinReconcileScheduleConfig,
    CheckinScheduleConfig,
    ConversationPurgeInput,
    ConversationPurgeResult,
    ConversationPurgeScheduleConfig,
    CrossPersonNotifyRetryInput,
    CrossPersonNotifyRetryResult,
    CrossPersonNotifyRetryScheduleConfig,
    DeveloperCheckinDispatch,
    DirectorySyncInput,
    DirectorySyncResult,
    HeartbeatInput,
    HeartbeatResult,
    InboundSweeperInput,
    InboundSweeperResult,
    InboundSweeperScheduleConfig,
    ReplyCoalesceInput,
    ReplyCoalesceResult,
    ScheduleBootstrapResult,
    SyncDispatchInput,
    SyncScheduleConfig,
    record_heartbeat,
)
from infra.workflows import (
    brief_generation,
    calendar_sync,
    checkin_fanout,
    conversation_purge,
    cross_person_notify_retry,
    daily_checkin,
    delivery_reports,
    directory_sync,
    drift_scan,
    git_sync,
    inbound_events,
    jira_sync,
    nudge,
    risk_assessment,
    rollup,
    runtime_sync,
)
from infra.workflows.brief_generation import BriefGenerationInput, BriefGenerationResult
from infra.workflows.calendar_sync import CalendarSyncInput, CalendarSyncWorkflowResult
from infra.workflows.daily_checkin import DailyCheckinInput, DailyCheckinResult
from infra.workflows.delivery_reports import (
    DayReportDispatchInput,
    DayReportDispatchResult,
    DeliverySnapshotInput,
    DeliverySnapshotResult,
    GateScanInput,
    GateScanResult,
)
from infra.workflows.dispatch import (
    daily_checkin_input,
    safe_workflow_id,
    sync_dispatch_for_schedule,
    sync_workflow_input,
    sync_workflow_name,
)
from infra.workflows.drift_scan import DriftScanInput, DriftScanWorkflowResult
from infra.workflows.git_sync import GitSyncInput, GitSyncWorkflowResult
from infra.workflows.jira_sync import JiraSyncInput, ReadSyncWorkflowResult
from infra.workflows.nudge import EscalationStepInput, NudgeInput, NudgeResult
from infra.workflows.risk_assessment import RiskAssessmentInput, RiskAssessmentWorkflowResult
from infra.workflows.rollup import (
    ROLLUP_REFRESH_DEBOUNCE_SECONDS,
    RollupInput,
    RollupWorkflowResult,
    rollup_refresh_input,
    rollup_refresh_key,
)
from infra.workflows.runtime_sync import RuntimeSyncInput, RuntimeSyncWorkflowResult

SyncWorkflowResult = (
    ReadSyncWorkflowResult
    | GitSyncWorkflowResult
    | CalendarSyncWorkflowResult
    | DirectorySyncResult
    | RuntimeSyncWorkflowResult
    | RiskAssessmentWorkflowResult
    | RollupWorkflowResult
    | DriftScanWorkflowResult
    | BriefGenerationResult
    | DeliverySnapshotResult
    | DayReportDispatchResult
    | GateScanResult
)

if TYPE_CHECKING:
    from temporalio.client import Client
    from temporalio.client import Schedule as TemporalSchedule


@activity.defn
async def record_heartbeat_activity(payload: HeartbeatInput) -> HeartbeatResult:
    return record_heartbeat(payload)


@workflow.defn
class HeartbeatWorkflow:
    @workflow.run
    async def run(self, payload: HeartbeatInput) -> HeartbeatResult:
        return await workflow.execute_activity(
            record_heartbeat_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=10),
        )


@activity.defn
async def dispatch_checkins_for_tenant_activity(
    payload: CheckinFanoutInput,
) -> CheckinFanoutResult:
    return await checkin_fanout.dispatch_checkins_for_tenant_activity(payload)


@workflow.defn
class CheckinFanoutWorkflow:
    @workflow.run
    async def run(self, payload: CheckinFanoutInput) -> CheckinFanoutResult:
        return await workflow.execute_activity(
            dispatch_checkins_for_tenant_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
        )


@workflow.defn
class ScheduledCheckinFanoutWorkflow:
    @workflow.run
    async def run(self, config: CheckinScheduleConfig) -> CheckinFanoutResult:
        return await workflow.execute_activity(
            dispatch_checkins_for_tenant_activity,
            CheckinFanoutInput(
                tenant_id=config.tenant_id,
                checkin_date=workflow.now().date().isoformat(),
            ),
            start_to_close_timeout=timedelta(minutes=10),
        )


@activity.defn
async def reconcile_checkins_for_tenant_activity(
    payload: CheckinReconcileInput,
) -> CheckinReconcileResult:
    return await checkin_fanout.reconcile_checkins_for_tenant_activity(payload)


@workflow.defn
class ScheduledCheckinReconcileWorkflow:
    @workflow.run
    async def run(self, config: CheckinReconcileScheduleConfig) -> CheckinReconcileResult:
        return await workflow.execute_activity(
            reconcile_checkins_for_tenant_activity,
            CheckinReconcileInput(
                tenant_id=config.tenant_id,
                observed_at=workflow.now().isoformat(),
                after_local_time=config.after_local_time,
                timezone=config.timezone,
            ),
            start_to_close_timeout=timedelta(minutes=10),
        )


@activity.defn
async def purge_conversation_turns_activity(
    payload: ConversationPurgeInput,
) -> ConversationPurgeResult:
    return await conversation_purge.purge_conversation_turns_activity(payload)


@workflow.defn
class ConversationPurgeWorkflow:
    @workflow.run
    async def run(self, payload: ConversationPurgeInput) -> ConversationPurgeResult:
        return await workflow.execute_activity(
            purge_conversation_turns_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


@workflow.defn
class ScheduledConversationPurgeWorkflow:
    @workflow.run
    async def run(self, config: ConversationPurgeScheduleConfig) -> ConversationPurgeResult:
        return await workflow.execute_activity(
            purge_conversation_turns_activity,
            ConversationPurgeInput(
                tenant_id=config.tenant_id,
                retention_days=config.retention_days,
                now=workflow.now().isoformat(),
            ),
            start_to_close_timeout=timedelta(minutes=5),
        )


@activity.defn
async def sync_jira_project_activity(payload: JiraSyncInput) -> ReadSyncWorkflowResult:
    return await jira_sync.sync_jira_project_activity(payload)


@workflow.defn
class JiraSyncWorkflow:
    @workflow.run
    async def run(self, payload: JiraSyncInput) -> ReadSyncWorkflowResult:
        return await workflow.execute_activity(
            sync_jira_project_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


@activity.defn
async def sync_git_repo_activity(payload: GitSyncInput) -> GitSyncWorkflowResult:
    return await git_sync.sync_git_repo_activity(payload)


@workflow.defn
class GitSyncWorkflow:
    @workflow.run
    async def run(self, payload: GitSyncInput) -> GitSyncWorkflowResult:
        return await workflow.execute_activity(
            sync_git_repo_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


@activity.defn
async def sync_calendar_user_activity(payload: CalendarSyncInput) -> CalendarSyncWorkflowResult:
    return await calendar_sync.sync_calendar_user_activity(payload)


@workflow.defn
class CalendarSyncWorkflow:
    @workflow.run
    async def run(self, payload: CalendarSyncInput) -> CalendarSyncWorkflowResult:
        return await workflow.execute_activity(
            sync_calendar_user_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


@activity.defn
async def sync_directory_activity(payload: DirectorySyncInput) -> DirectorySyncResult:
    return await directory_sync.sync_directory_activity(payload)


@workflow.defn
class DirectorySyncWorkflow:
    @workflow.run
    async def run(self, payload: DirectorySyncInput) -> DirectorySyncResult:
        return await workflow.execute_activity(
            sync_directory_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


@activity.defn
async def run_runtime_config_sync_activity(
    payload: RuntimeSyncInput,
) -> RuntimeSyncWorkflowResult:
    return await runtime_sync.run_runtime_config_sync_activity(payload)


@workflow.defn
class RuntimeSyncWorkflow:
    @workflow.run
    async def run(self, payload: RuntimeSyncInput) -> RuntimeSyncWorkflowResult:
        return await workflow.execute_activity(
            run_runtime_config_sync_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


@activity.defn
async def run_risk_assessment_activity(
    payload: RiskAssessmentInput,
) -> RiskAssessmentWorkflowResult:
    return await risk_assessment.run_risk_assessment_activity(payload)


@activity.defn
async def run_rollup_activity(payload: RollupInput) -> RollupWorkflowResult:
    return await rollup.run_rollup_activity(payload)


@workflow.defn
class RollupWorkflow:
    @workflow.run
    async def run(self, payload: RollupInput) -> RollupWorkflowResult:
        return await workflow.execute_activity(
            run_rollup_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
        )


@workflow.defn
class RiskAssessmentWorkflow:
    @workflow.run
    async def run(self, payload: RiskAssessmentInput) -> RiskAssessmentWorkflowResult:
        return await workflow.execute_activity(
            run_risk_assessment_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


@activity.defn
async def run_drift_scan_activity(payload: DriftScanInput) -> DriftScanWorkflowResult:
    return await drift_scan.run_drift_scan_activity(payload)


@workflow.defn
class DriftScanWorkflow:
    @workflow.run
    async def run(self, payload: DriftScanInput) -> DriftScanWorkflowResult:
        return await workflow.execute_activity(
            run_drift_scan_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


@activity.defn
async def run_brief_generation_activity(
    payload: BriefGenerationInput,
) -> BriefGenerationResult:
    return await brief_generation.run_brief_generation_activity(payload)


@workflow.defn
class BriefGenerationWorkflow:
    @workflow.run
    async def run(self, payload: BriefGenerationInput) -> BriefGenerationResult:
        return await workflow.execute_activity(
            run_brief_generation_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
        )


@activity.defn
async def run_delivery_snapshot_activity(
    payload: DeliverySnapshotInput,
) -> DeliverySnapshotResult:
    return await delivery_reports.run_delivery_snapshot_activity(payload)


@workflow.defn
class DeliverySnapshotWorkflow:
    @workflow.run
    async def run(self, payload: DeliverySnapshotInput) -> DeliverySnapshotResult:
        return await workflow.execute_activity(
            run_delivery_snapshot_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
        )


@activity.defn
async def run_day_report_dispatch_activity(
    payload: DayReportDispatchInput,
) -> DayReportDispatchResult:
    return await delivery_reports.run_day_report_dispatch_activity(payload)


@workflow.defn
class DayReportDispatchWorkflow:
    @workflow.run
    async def run(self, payload: DayReportDispatchInput) -> DayReportDispatchResult:
        return await workflow.execute_activity(
            run_day_report_dispatch_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
        )


@activity.defn
async def run_gate_scan_activity(payload: GateScanInput) -> GateScanResult:
    return await delivery_reports.run_gate_scan_activity(payload)


@workflow.defn
class GateScanWorkflow:
    @workflow.run
    async def run(self, payload: GateScanInput) -> GateScanResult:
        return await workflow.execute_activity(
            run_gate_scan_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=20),
        )


@workflow.defn
class ScheduledSyncWorkflow:
    @workflow.run
    async def run(
        self,
        config: SyncScheduleConfig,
    ) -> SyncWorkflowResult:
        return await _execute_sync_activity(
            sync_workflow_input(sync_dispatch_for_schedule(config, workflow.now()))
        )


@activity.defn
async def start_daily_checkin_activity(payload: DailyCheckinInput) -> DailyCheckinResult:
    return await daily_checkin.start_daily_checkin_activity(payload)


@workflow.defn
class DailyCheckinWorkflow:
    @workflow.run
    async def run(self, payload: DailyCheckinInput) -> DailyCheckinResult:
        scheduled = daily_checkin.prepare_daily_checkin_payload(
            payload,
            workflow_id=workflow.info().workflow_id,
            now=workflow.now(),
        )
        result = await workflow.execute_activity(
            start_daily_checkin_activity,
            scheduled,
            start_to_close_timeout=timedelta(minutes=5),
        )
        if result.status == "sent" and not result.already_recorded:
            nudge_workflow_id = f"nudge-{result.correlation_id}"
            await workflow.start_child_workflow(
                NudgeWorkflow.run,
                daily_checkin.nudge_input_for_daily_checkin_result(
                    result,
                    scheduled,
                    now=workflow.now(),
                ),
                id=nudge_workflow_id,
                parent_close_policy=workflow.ParentClosePolicy.ABANDON,
            )
            return replace(result, nudge_workflow_id=nudge_workflow_id)
        return result


@activity.defn
async def send_checkin_nudge_activity(payload: NudgeInput) -> NudgeResult:
    return await nudge.send_checkin_nudge_activity(payload)


@activity.defn
async def send_escalation_step_activity(payload: EscalationStepInput) -> NudgeResult:
    return await nudge.send_escalation_step_activity(payload)


@activity.defn
async def close_checkin_non_response_activity(payload: NudgeInput) -> NudgeResult:
    return await nudge.close_checkin_non_response_activity(payload)


@workflow.defn
class NudgeWorkflow:
    @workflow.run
    async def run(self, payload: NudgeInput) -> NudgeResult:
        last_nudge: NudgeResult | None = None
        for number, step in enumerate(payload.resolved_steps(), start=1):
            if step.wait_seconds > 0:
                await workflow.sleep(timedelta(seconds=step.wait_seconds))
            step_result = await workflow.execute_activity(
                send_escalation_step_activity,
                payload.step_input(number, step.target),
                start_to_close_timeout=timedelta(minutes=5),
            )
            if step_result.status == "already_replied":
                return step_result
            if step_result.nudge_message_id is not None:
                last_nudge = step_result
        if payload.final_reply_wait_seconds > 0:
            await workflow.sleep(timedelta(seconds=payload.final_reply_wait_seconds))
        close_result = await workflow.execute_activity(
            close_checkin_non_response_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
        if close_result.nudge_message_id is None:
            return NudgeResult(
                tenant_id=close_result.tenant_id,
                developer_id=close_result.developer_id,
                correlation_id=close_result.correlation_id,
                status=close_result.status,
                nudge_message_id=last_nudge.nudge_message_id if last_nudge else None,
                terminal_source=close_result.terminal_source,
            )
        return close_result


@activity.defn
async def drain_inbound_conversation_activity(
    payload: ReplyCoalesceInput,
) -> ReplyCoalesceResult:
    return await inbound_events.drain_conversation_activity(payload)


@activity.defn
async def sweep_inbound_events_activity(payload: InboundSweeperInput) -> InboundSweeperResult:
    return await inbound_events.sweep_inbound_events_activity(payload)


# Max consecutive debounce resets before continue_as_new truncates history. Each
# reset appends timer events to the Temporal history; capping the run keeps it
# bounded on chatty conversations. Mirrors the intent of DBOS `_MAX_COALESCE_PASSES`.
_MAX_COALESCE_RESETS = 500


@workflow.defn
class ReplyCoalesceWorkflow:
    """Per-conversation debounce/coalesce workflow (signal_with_start driven)."""

    def __init__(self) -> None:
        self._pending: bool = False

    @workflow.signal
    def new_event(self) -> None:
        self._pending = True

    @workflow.run
    async def run(self, payload: ReplyCoalesceInput) -> ReplyCoalesceResult:
        debounce = timedelta(seconds=payload.debounce_seconds)
        # Reset-on-message quiet window: consume the pending signal, then wait up
        # to `debounce` for another. A new signal returns early (timer resets); a
        # quiet window raises TimeoutError, which ends the loop and drains.
        resets = 0
        while True:
            self._pending = False
            try:
                await workflow.wait_condition(lambda: self._pending, timeout=debounce)
            except TimeoutError:
                break
            resets += 1
            # Bound Temporal event history on a pathologically chatty conversation:
            # each reset appends timer events, so after enough consecutive resets
            # continue-as-new into a fresh run with a truncated history. The buffered
            # events stay durably in inbound_chat_events and the fresh run's quiet
            # window still drains them, so no reply is dropped -- only the same
            # (conversation_key, tenant, debounce) input is carried forward. Mirrors
            # the intent of the DBOS coalesce bound.
            if resets >= _MAX_COALESCE_RESETS:
                workflow.continue_as_new(payload)
        return await workflow.execute_activity(
            drain_inbound_conversation_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


@workflow.defn
class ScheduledInboundSweeperWorkflow:
    @workflow.run
    async def run(self, config: InboundSweeperScheduleConfig) -> InboundSweeperResult:
        return await workflow.execute_activity(
            sweep_inbound_events_activity,
            InboundSweeperInput(
                tenant_id=config.tenant_id,
                grace_seconds=config.grace_seconds,
                now=workflow.now().isoformat(),
            ),
            start_to_close_timeout=timedelta(minutes=5),
        )


@activity.defn
async def retry_cross_person_notifications_activity(
    payload: CrossPersonNotifyRetryInput,
) -> CrossPersonNotifyRetryResult:
    return await cross_person_notify_retry.retry_cross_person_notifications_activity(payload)


@workflow.defn
class ScheduledCrossPersonNotifyRetryWorkflow:
    @workflow.run
    async def run(
        self, config: CrossPersonNotifyRetryScheduleConfig
    ) -> CrossPersonNotifyRetryResult:
        # A retried activity is safe: each DM is claimed before it is sent.
        return await workflow.execute_activity(
            retry_cross_person_notifications_activity,
            CrossPersonNotifyRetryInput(
                tenant_id=config.tenant_id,
                now=workflow.now().isoformat(),
            ),
            start_to_close_timeout=timedelta(minutes=5),
        )


async def _execute_sync_activity(
    payload: JiraSyncInput
    | GitSyncInput
    | CalendarSyncInput
    | DirectorySyncInput
    | RuntimeSyncInput
    | RiskAssessmentInput
    | DriftScanInput
    | BriefGenerationInput
    | RollupInput
    | DeliverySnapshotInput
    | DayReportDispatchInput
    | GateScanInput,
) -> SyncWorkflowResult:
    if isinstance(payload, JiraSyncInput):
        return await workflow.execute_activity(
            sync_jira_project_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
    if isinstance(payload, GitSyncInput):
        return await workflow.execute_activity(
            sync_git_repo_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
    if isinstance(payload, CalendarSyncInput):
        return await workflow.execute_activity(
            sync_calendar_user_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
    if isinstance(payload, DirectorySyncInput):
        return await workflow.execute_activity(
            sync_directory_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
    if isinstance(payload, RuntimeSyncInput):
        return await workflow.execute_activity(
            run_runtime_config_sync_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
    return await _execute_derived_activity(payload)


async def _execute_derived_activity(payload: object) -> SyncWorkflowResult:
    """The activities that derive from stored state rather than read a provider."""
    if isinstance(payload, RollupInput):
        return await workflow.execute_activity(
            run_rollup_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
        )
    if isinstance(payload, RiskAssessmentInput):
        return await workflow.execute_activity(
            run_risk_assessment_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
    if isinstance(payload, DriftScanInput):
        return await workflow.execute_activity(
            run_drift_scan_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
    if isinstance(payload, BriefGenerationInput):
        return await workflow.execute_activity(
            run_brief_generation_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
        )
    if isinstance(payload, DeliverySnapshotInput):
        return await workflow.execute_activity(
            run_delivery_snapshot_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
        )
    if isinstance(payload, DayReportDispatchInput):
        return await workflow.execute_activity(
            run_day_report_dispatch_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
        )
    if isinstance(payload, GateScanInput):
        return await workflow.execute_activity(
            run_gate_scan_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=20),
        )
    raise ValueError("unsupported sync payload")


@dataclass(frozen=True)
class TemporalWorkflowScheduler:
    target: str
    task_queue: str
    schedule_id: str
    tenant_id: str
    interval_seconds: int
    reply_debounce_seconds: int = 30

    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult:
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleIntervalSpec,
            ScheduleOverlapPolicy,
            SchedulePolicy,
            ScheduleSpec,
        )

        client = await _connect_temporal(self.target)
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                HeartbeatWorkflow.run,
                HeartbeatInput(
                    tenant_id=self.tenant_id,
                    heartbeat_id=self.schedule_id,
                ),
                id=f"{self.schedule_id}-workflow",
                task_queue=self.task_queue,
            ),
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=timedelta(seconds=self.interval_seconds))]
            ),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        )
        status = await _ensure_temporal_schedule(client, self.schedule_id, schedule)
        return ScheduleBootstrapResult(schedule_id=self.schedule_id, status=status)

    async def ensure_checkin_fanout_schedule(
        self, config: CheckinScheduleConfig
    ) -> ScheduleBootstrapResult:
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleOverlapPolicy,
            SchedulePolicy,
            ScheduleSpec,
        )

        client = await _connect_temporal(self.target)
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                ScheduledCheckinFanoutWorkflow.run,
                config,
                id=f"{config.schedule_id}-workflow",
                task_queue=self.task_queue,
            ),
            spec=ScheduleSpec(cron_expressions=[config.cron]),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        )
        status = await _ensure_temporal_schedule(client, config.schedule_id, schedule)
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status=status)

    async def ensure_checkin_reconcile_schedule(
        self, config: CheckinReconcileScheduleConfig
    ) -> ScheduleBootstrapResult:
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleOverlapPolicy,
            SchedulePolicy,
            ScheduleSpec,
        )

        client = await _connect_temporal(self.target)
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                ScheduledCheckinReconcileWorkflow.run,
                config,
                id=f"{config.schedule_id}-workflow",
                task_queue=self.task_queue,
            ),
            spec=ScheduleSpec(cron_expressions=[config.cron]),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        )
        status = await _ensure_temporal_schedule(client, config.schedule_id, schedule)
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status=status)

    async def ensure_conversation_purge_schedule(
        self, config: ConversationPurgeScheduleConfig
    ) -> ScheduleBootstrapResult:
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleOverlapPolicy,
            SchedulePolicy,
            ScheduleSpec,
        )

        client = await _connect_temporal(self.target)
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                ScheduledConversationPurgeWorkflow.run,
                config,
                id=f"{config.schedule_id}-workflow",
                task_queue=self.task_queue,
            ),
            spec=ScheduleSpec(cron_expressions=[config.cron]),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        )
        status = await _ensure_temporal_schedule(client, config.schedule_id, schedule)
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status=status)

    async def ensure_inbound_sweeper_schedule(
        self, config: InboundSweeperScheduleConfig
    ) -> ScheduleBootstrapResult:
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleOverlapPolicy,
            SchedulePolicy,
            ScheduleSpec,
        )

        client = await _connect_temporal(self.target)
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                ScheduledInboundSweeperWorkflow.run,
                config,
                id=f"{config.schedule_id}-workflow",
                task_queue=self.task_queue,
            ),
            spec=ScheduleSpec(cron_expressions=[config.cron]),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        )
        status = await _ensure_temporal_schedule(client, config.schedule_id, schedule)
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status=status)

    async def ensure_cross_person_notify_retry_schedule(
        self, config: CrossPersonNotifyRetryScheduleConfig
    ) -> ScheduleBootstrapResult:
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleOverlapPolicy,
            SchedulePolicy,
            ScheduleSpec,
        )

        client = await _connect_temporal(self.target)
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                ScheduledCrossPersonNotifyRetryWorkflow.run,
                config,
                id=f"{config.schedule_id}-workflow",
                task_queue=self.task_queue,
            ),
            spec=ScheduleSpec(cron_expressions=[config.cron]),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        )
        status = await _ensure_temporal_schedule(client, config.schedule_id, schedule)
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status=status)

    async def ensure_sync_schedules(
        self, configs: Sequence[SyncScheduleConfig]
    ) -> list[ScheduleBootstrapResult]:
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleOverlapPolicy,
            SchedulePolicy,
            ScheduleSpec,
        )

        client = await _connect_temporal(self.target)
        results: list[ScheduleBootstrapResult] = []
        for config in configs:
            schedule = Schedule(
                action=ScheduleActionStartWorkflow(
                    ScheduledSyncWorkflow.run,
                    config,
                    id=f"{config.schedule_id}-workflow",
                    task_queue=self.task_queue,
                ),
                spec=ScheduleSpec(cron_expressions=[config.cron]),
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            )
            status = await _ensure_temporal_schedule(client, config.schedule_id, schedule)
            results.append(ScheduleBootstrapResult(schedule_id=config.schedule_id, status=status))
        return results

    async def remove_schedule(self, schedule_id: str) -> ScheduleBootstrapResult:
        from temporalio.service import RPCError, RPCStatusCode

        client = await _connect_temporal(self.target)
        try:
            await client.get_schedule_handle(schedule_id).delete()
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
            return ScheduleBootstrapResult(schedule_id=schedule_id, status="absent")
        return ScheduleBootstrapResult(schedule_id=schedule_id, status="removed")

    async def dispatch_developer_checkin(self, input: DeveloperCheckinDispatch) -> str:
        client = await _connect_temporal(self.target)
        workflow_id = safe_workflow_id(
            "checkin-"
            f"{input.tenant_id}-{input.developer_id}-"
            f"{input.checkin_date or datetime.now(tz=UTC).date().isoformat()}-{uuid4()}"
        )
        await client.start_workflow(
            DailyCheckinWorkflow.run,
            daily_checkin_input(input),
            id=workflow_id,
            task_queue=self.task_queue,
        )
        return workflow_id

    async def arm_reply_coalesce(
        self, conversation_key: str, tenant_id: str, *, burst_key: str
    ) -> None:
        client = await _connect_temporal(self.target)
        # The id stays per conversation here: Temporal's default reuse policy lets
        # signal_with_start open a fresh run once the previous one has closed, so
        # unlike DBOS a finished burst never swallows the next one.
        coalesce_id = safe_workflow_id(f"reply-coalesce-{tenant_id}-{conversation_key}")
        # signal_with_start: start the debounce workflow if idle, and in all cases
        # deliver the signal that resets its quiet-window timer.
        await client.start_workflow(
            ReplyCoalesceWorkflow.run,
            ReplyCoalesceInput(
                tenant_id=tenant_id,
                conversation_key=conversation_key,
                debounce_seconds=self.reply_debounce_seconds,
            ),
            id=coalesce_id,
            task_queue=self.task_queue,
            start_signal="new_event",
        )

    async def dispatch_sync(self, input: SyncDispatchInput) -> str:
        client = await _connect_temporal(self.target)
        workflow_input = sync_workflow_input(input)
        workflow_name = sync_workflow_name(input)
        workflow_id = safe_workflow_id(
            f"sync-{workflow_name}-{input.tenant_id}-{input.scope}-{uuid4()}"
        )
        if isinstance(workflow_input, JiraSyncInput):
            await client.start_workflow(
                JiraSyncWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        elif isinstance(workflow_input, GitSyncInput):
            await client.start_workflow(
                GitSyncWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        elif isinstance(workflow_input, CalendarSyncInput):
            await client.start_workflow(
                CalendarSyncWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        elif isinstance(workflow_input, DirectorySyncInput):
            await client.start_workflow(
                DirectorySyncWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        elif isinstance(workflow_input, RuntimeSyncInput):
            await client.start_workflow(
                RuntimeSyncWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        else:
            await self._start_derived_workflow(client, workflow_input, workflow_id, input.connector)
        return workflow_id

    async def _start_derived_workflow(
        self, client: Client, workflow_input: object, workflow_id: str, connector: str
    ) -> None:
        if isinstance(workflow_input, RollupInput):
            await client.start_workflow(
                RollupWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        elif isinstance(workflow_input, RiskAssessmentInput):
            await client.start_workflow(
                RiskAssessmentWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        elif isinstance(workflow_input, DriftScanInput):
            await client.start_workflow(
                DriftScanWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        elif isinstance(workflow_input, BriefGenerationInput):
            await client.start_workflow(
                BriefGenerationWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        elif isinstance(workflow_input, DeliverySnapshotInput):
            await client.start_workflow(
                DeliverySnapshotWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        elif isinstance(workflow_input, DayReportDispatchInput):
            await client.start_workflow(
                DayReportDispatchWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        elif isinstance(workflow_input, GateScanInput):
            await client.start_workflow(
                GateScanWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
            )
        else:
            raise ValueError(f"unsupported sync connector: {connector}")


@dataclass(frozen=True)
class TemporalRollupRefresher:
    """Starts the existing rollup workflow for one tenant and day, once per window (N27).

    Temporal has no debounce, so requests coalesce on a fixed window: one
    workflow id per tenant, day and ``debounce_seconds`` window, started
    ``debounce_seconds`` late. The run therefore starts after its window has
    closed, so it reads what every request in the window asked about; a request
    in the next window gets a run of its own.
    """

    target: str
    task_queue: str
    debounce_seconds: int = ROLLUP_REFRESH_DEBOUNCE_SECONDS

    async def refresh_rollup(self, tenant_id: str, as_of: date) -> None:
        from temporalio.exceptions import WorkflowAlreadyStartedError

        client = await _connect_temporal(self.target)
        window = int(datetime.now(tz=UTC).timestamp()) // max(1, self.debounce_seconds)
        try:
            await client.start_workflow(
                RollupWorkflow.run,
                rollup_refresh_input(tenant_id, as_of),
                id=safe_workflow_id(f"{rollup_refresh_key(tenant_id, as_of)}-{window}"),
                task_queue=self.task_queue,
                start_delay=timedelta(seconds=self.debounce_seconds),
            )
        except WorkflowAlreadyStartedError:
            # This window's refresh is already due; it will read this change too.
            return


@dataclass(frozen=True)
class TemporalWorkflowWorker:
    target: str
    task_queue: str

    async def run(self) -> None:
        from temporalio.worker import Worker

        client = await _connect_temporal(self.target)
        worker = Worker(
            client,
            task_queue=self.task_queue,
            workflows=[
                HeartbeatWorkflow,
                CheckinFanoutWorkflow,
                ScheduledCheckinFanoutWorkflow,
                ScheduledCheckinReconcileWorkflow,
                ConversationPurgeWorkflow,
                ScheduledConversationPurgeWorkflow,
                JiraSyncWorkflow,
                GitSyncWorkflow,
                CalendarSyncWorkflow,
                DirectorySyncWorkflow,
                RuntimeSyncWorkflow,
                RiskAssessmentWorkflow,
                RollupWorkflow,
                DriftScanWorkflow,
                BriefGenerationWorkflow,
                DeliverySnapshotWorkflow,
                DayReportDispatchWorkflow,
                GateScanWorkflow,
                ScheduledSyncWorkflow,
                DailyCheckinWorkflow,
                NudgeWorkflow,
                ReplyCoalesceWorkflow,
                ScheduledInboundSweeperWorkflow,
                ScheduledCrossPersonNotifyRetryWorkflow,
            ],
            activities=[
                record_heartbeat_activity,
                dispatch_checkins_for_tenant_activity,
                reconcile_checkins_for_tenant_activity,
                purge_conversation_turns_activity,
                sync_jira_project_activity,
                sync_git_repo_activity,
                sync_calendar_user_activity,
                sync_directory_activity,
                run_runtime_config_sync_activity,
                run_risk_assessment_activity,
                run_rollup_activity,
                run_drift_scan_activity,
                run_brief_generation_activity,
                run_delivery_snapshot_activity,
                run_day_report_dispatch_activity,
                run_gate_scan_activity,
                start_daily_checkin_activity,
                send_checkin_nudge_activity,
                send_escalation_step_activity,
                close_checkin_non_response_activity,
                drain_inbound_conversation_activity,
                sweep_inbound_events_activity,
                retry_cross_person_notifications_activity,
            ],
        )
        await worker.run()


@dataclass(frozen=True)
class TemporalWorkflowReadinessProbe:
    target: str

    async def check(self) -> bool:
        await _connect_temporal(self.target)
        return True


async def _connect_temporal(
    target: str,
    *,
    attempts: int = 30,
    delay_seconds: float = 1.0,
) -> Client:
    from temporalio.client import Client

    last_error: ConnectionError | OSError | RuntimeError | None = None
    for attempt in range(max(1, attempts)):
        try:
            return await Client.connect(target)
        except (ConnectionError, OSError, RuntimeError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Temporal connection failed without an exception")


async def _ensure_temporal_schedule(
    client: Client,
    schedule_id: str,
    schedule: TemporalSchedule,
) -> str:
    from temporalio.client import ScheduleAlreadyRunningError, ScheduleUpdate

    try:
        await client.create_schedule(schedule_id, schedule)
        return "created"
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(schedule_id)

        async def updater(_: object) -> ScheduleUpdate:
            return ScheduleUpdate(schedule=schedule)

        await handle.update(updater)
        return "updated"
