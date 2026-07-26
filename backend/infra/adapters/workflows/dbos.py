from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import psycopg
from dbos import DBOS, DBOSConfig, ScheduleInput, SetWorkflowID

from core.application.reply_ingestion import run_reply_debounce
from core.domain.workflows import (
    CheckinFanoutInput,
    CheckinFanoutResult,
    CheckinReconcileDispatchPlan,
    CheckinReconcileInput,
    CheckinReconcileResult,
    CheckinReconcileScheduleConfig,
    CheckinScheduleConfig,
    ConversationPurgeInput,
    ConversationPurgeResult,
    ConversationPurgeScheduleConfig,
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
    calendar_sync,
    checkin_fanout,
    conversation_purge,
    daily_checkin,
    directory_sync,
    drift_scan,
    git_sync,
    inbound_events,
    jira_sync,
    nudge,
    risk_assessment,
    runtime_sync,
)
from infra.workflows.calendar_sync import CalendarSyncInput, CalendarSyncWorkflowResult
from infra.workflows.daily_checkin import DailyCheckinInput, DailyCheckinResult
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
from infra.workflows.nudge import NudgeInput, NudgeResult
from infra.workflows.risk_assessment import RiskAssessmentInput, RiskAssessmentWorkflowResult
from infra.workflows.runtime_sync import RuntimeSyncInput, RuntimeSyncWorkflowResult

SyncWorkflowResult = (
    ReadSyncWorkflowResult
    | GitSyncWorkflowResult
    | CalendarSyncWorkflowResult
    | DirectorySyncResult
    | RuntimeSyncWorkflowResult
    | RiskAssessmentWorkflowResult
    | DriftScanWorkflowResult
)


@dataclass(frozen=True)
class DbosRuntimeConfig:
    app_name: str
    system_database_url: str


_configured_runtime: DbosRuntimeConfig | None = None


@DBOS.step(name="openprogram_record_heartbeat", retries_allowed=True)
async def dbos_record_heartbeat_step(payload: HeartbeatInput) -> HeartbeatResult:
    return record_heartbeat(payload)


@DBOS.workflow(name="openprogram_heartbeat")
async def dbos_heartbeat_workflow(payload: HeartbeatInput) -> HeartbeatResult:
    return await dbos_record_heartbeat_step(payload)


@DBOS.workflow(name="openprogram_scheduled_heartbeat")
async def dbos_scheduled_heartbeat_workflow(
    scheduled_time: datetime,
    context: dict[str, str],
) -> HeartbeatResult:
    schedule_id = context["schedule_id"]
    return await dbos_record_heartbeat_step(
        HeartbeatInput(
            tenant_id=context["tenant_id"],
            heartbeat_id=f"{schedule_id}-{scheduled_time.isoformat()}",
        )
    )


@DBOS.step(name="openprogram_prepare_checkin_fanout", retries_allowed=True)
async def dbos_prepare_checkin_fanout_step(
    payload: CheckinFanoutInput,
) -> list[DeveloperCheckinDispatch]:
    return await checkin_fanout.developer_checkin_dispatches_for_tenant_activity(payload)


@DBOS.workflow(name="openprogram_checkin_fanout")
async def dbos_checkin_fanout_workflow(payload: CheckinFanoutInput) -> CheckinFanoutResult:
    return await _run_dbos_checkin_fanout(payload)


@DBOS.workflow(name="openprogram_scheduled_checkin_fanout")
async def dbos_scheduled_checkin_fanout_workflow(
    scheduled_time: datetime,
    context: dict[str, str],
) -> CheckinFanoutResult:
    return await _run_dbos_checkin_fanout(
        CheckinFanoutInput(
            tenant_id=context["tenant_id"],
            checkin_date=scheduled_time.date().isoformat(),
        )
    )


@DBOS.step(name="openprogram_prepare_checkin_reconcile", retries_allowed=True)
async def dbos_prepare_checkin_reconcile_step(
    payload: CheckinReconcileInput,
) -> CheckinReconcileDispatchPlan:
    return await checkin_fanout.prepare_checkin_reconcile_dispatches_for_tenant_activity(payload)


@DBOS.workflow(name="openprogram_checkin_reconcile")
async def dbos_checkin_reconcile_workflow(
    payload: CheckinReconcileInput,
) -> CheckinReconcileResult:
    return await _run_dbos_checkin_reconcile(payload)


@DBOS.workflow(name="openprogram_scheduled_checkin_reconcile")
async def dbos_scheduled_checkin_reconcile_workflow(
    scheduled_time: datetime,
    context: dict[str, str],
) -> CheckinReconcileResult:
    return await _run_dbos_checkin_reconcile(
        CheckinReconcileInput(
            tenant_id=context["tenant_id"],
            observed_at=scheduled_time.isoformat(),
            after_local_time=context["after_local_time"],
            timezone=context["timezone"],
        )
    )


@DBOS.step(name="openprogram_purge_conversation_turns", retries_allowed=True)
async def dbos_purge_conversation_turns_step(
    payload: ConversationPurgeInput,
) -> ConversationPurgeResult:
    return await conversation_purge.purge_conversation_turns_activity(payload)


@DBOS.workflow(name="openprogram_conversation_purge")
async def dbos_conversation_purge_workflow(
    payload: ConversationPurgeInput,
) -> ConversationPurgeResult:
    return await dbos_purge_conversation_turns_step(payload)


@DBOS.workflow(name="openprogram_scheduled_conversation_purge")
async def dbos_scheduled_conversation_purge_workflow(
    scheduled_time: datetime,
    context: dict[str, str],
) -> ConversationPurgeResult:
    return await dbos_purge_conversation_turns_step(
        ConversationPurgeInput(
            tenant_id=context["tenant_id"],
            retention_days=int(context["retention_days"]),
            now=scheduled_time.isoformat(),
        )
    )


@DBOS.step(name="openprogram_sync_jira_project", retries_allowed=True)
async def dbos_sync_jira_project_step(payload: JiraSyncInput) -> ReadSyncWorkflowResult:
    return await jira_sync.sync_jira_project_activity(payload)


@DBOS.workflow(name="openprogram_jira_sync")
async def dbos_jira_sync_workflow(payload: JiraSyncInput) -> ReadSyncWorkflowResult:
    return await dbos_sync_jira_project_step(payload)


@DBOS.step(name="openprogram_sync_git_repo", retries_allowed=True)
async def dbos_sync_git_repo_step(payload: GitSyncInput) -> GitSyncWorkflowResult:
    return await git_sync.sync_git_repo_activity(payload)


@DBOS.workflow(name="openprogram_git_sync")
async def dbos_git_sync_workflow(payload: GitSyncInput) -> GitSyncWorkflowResult:
    return await dbos_sync_git_repo_step(payload)


@DBOS.step(name="openprogram_sync_calendar_user", retries_allowed=True)
async def dbos_sync_calendar_user_step(
    payload: CalendarSyncInput,
) -> CalendarSyncWorkflowResult:
    return await calendar_sync.sync_calendar_user_activity(payload)


@DBOS.workflow(name="openprogram_calendar_sync")
async def dbos_calendar_sync_workflow(
    payload: CalendarSyncInput,
) -> CalendarSyncWorkflowResult:
    return await dbos_sync_calendar_user_step(payload)


@DBOS.step(name="openprogram_sync_directory", retries_allowed=True)
async def dbos_sync_directory_step(payload: DirectorySyncInput) -> DirectorySyncResult:
    return await directory_sync.sync_directory_activity(payload)


@DBOS.workflow(name="openprogram_directory_sync")
async def dbos_directory_sync_workflow(
    payload: DirectorySyncInput,
) -> DirectorySyncResult:
    return await dbos_sync_directory_step(payload)


@DBOS.step(name="openprogram_runtime_config_sync", retries_allowed=True)
async def dbos_runtime_config_sync_step(payload: RuntimeSyncInput) -> RuntimeSyncWorkflowResult:
    return await runtime_sync.run_runtime_config_sync_activity(payload)


@DBOS.workflow(name="openprogram_runtime_config_sync")
async def dbos_runtime_config_sync_workflow(
    payload: RuntimeSyncInput,
) -> RuntimeSyncWorkflowResult:
    return await dbos_runtime_config_sync_step(payload)


@DBOS.step(name="openprogram_run_risk_assessment", retries_allowed=True)
async def dbos_run_risk_assessment_step(
    payload: RiskAssessmentInput,
) -> RiskAssessmentWorkflowResult:
    return await risk_assessment.run_risk_assessment_activity(payload)


@DBOS.workflow(name="openprogram_risk_assessment")
async def dbos_risk_assessment_workflow(
    payload: RiskAssessmentInput,
) -> RiskAssessmentWorkflowResult:
    return await dbos_run_risk_assessment_step(payload)


@DBOS.step(name="openprogram_run_drift_scan", retries_allowed=True)
async def dbos_run_drift_scan_step(payload: DriftScanInput) -> DriftScanWorkflowResult:
    return await drift_scan.run_drift_scan_activity(payload)


@DBOS.workflow(name="openprogram_drift_scan")
async def dbos_drift_scan_workflow(payload: DriftScanInput) -> DriftScanWorkflowResult:
    return await dbos_run_drift_scan_step(payload)


@DBOS.workflow(name="openprogram_scheduled_sync")
async def dbos_scheduled_sync_workflow(
    scheduled_time: datetime,
    context: dict[str, Any],
) -> SyncWorkflowResult:
    return await _run_sync_dispatch(
        sync_dispatch_for_schedule(_sync_schedule_config_from_context(context), scheduled_time)
    )


@DBOS.step(name="openprogram_prepare_daily_checkin")
async def dbos_prepare_daily_checkin_step(payload: DailyCheckinInput) -> DailyCheckinInput:
    workflow_id = DBOS.workflow_id or "dbos-daily-checkin"
    return daily_checkin.prepare_daily_checkin_payload(
        payload,
        workflow_id=workflow_id,
        now=datetime.now(tz=UTC),
    )


@DBOS.step(name="openprogram_start_daily_checkin", retries_allowed=True)
async def dbos_start_daily_checkin_step(payload: DailyCheckinInput) -> DailyCheckinResult:
    return await daily_checkin.start_daily_checkin_activity(payload)


@DBOS.workflow(name="openprogram_daily_checkin")
async def dbos_daily_checkin_workflow(payload: DailyCheckinInput) -> DailyCheckinResult:
    scheduled = await dbos_prepare_daily_checkin_step(payload)
    result = await dbos_start_daily_checkin_step(scheduled)
    if result.status == "sent" and not result.already_recorded:
        nudge_workflow_id = f"nudge-{result.correlation_id}"
        with SetWorkflowID(nudge_workflow_id):
            await DBOS.start_workflow_async(
                dbos_nudge_workflow,
                daily_checkin.nudge_input_for_daily_checkin_result(
                    result,
                    scheduled,
                    now=datetime.now(tz=UTC),
                ),
            )
        return replace(result, nudge_workflow_id=nudge_workflow_id)
    return result


async def _run_dbos_checkin_fanout(payload: CheckinFanoutInput) -> CheckinFanoutResult:
    dispatches = await dbos_prepare_checkin_fanout_step(payload)
    workflow_ids = await _start_daily_checkins_concurrently(dispatches)
    return CheckinFanoutResult(
        tenant_id=payload.tenant_id,
        checkin_date=payload.checkin_date,
        dispatched=len(workflow_ids),
        workflow_ids=workflow_ids,
    )


async def _run_dbos_checkin_reconcile(
    payload: CheckinReconcileInput,
) -> CheckinReconcileResult:
    plan = await dbos_prepare_checkin_reconcile_step(payload)
    if plan.result.status != "dispatched":
        return plan.result
    workflow_ids = await _start_daily_checkins_concurrently(plan.dispatches)
    if not workflow_ids:
        return replace(plan.result, status="no_missing", dispatched=0, workflow_ids=[])
    return replace(plan.result, dispatched=len(workflow_ids), workflow_ids=workflow_ids)


async def _start_daily_checkins_concurrently(
    dispatches: Sequence[DeveloperCheckinDispatch],
) -> list[str]:
    """Fan out child check-in workflows concurrently, bounded to avoid Slack
    rate-limit spikes. Child workflows start from workflow context (never a
    retryable step). asyncio.gather preserves dispatch order in the result."""
    if not dispatches:
        return []
    semaphore = asyncio.Semaphore(max(1, _checkin_fanout_concurrency()))

    async def start_and_wait(dispatch: DeveloperCheckinDispatch) -> str:
        async with semaphore:
            return await _start_daily_checkin_workflow(dispatch)

    return list(await asyncio.gather(*(start_and_wait(dispatch) for dispatch in dispatches)))


async def _start_daily_checkin_workflow(input: DeveloperCheckinDispatch) -> str:
    workflow_id = safe_workflow_id(
        "checkin-"
        f"{input.tenant_id}-{input.developer_id}-"
        f"{input.checkin_date or datetime.now(tz=UTC).date().isoformat()}-{uuid4()}"
    )
    # Drive check-ins to completion so outbound chat messages exist before the
    # caller observes the workflow ID (also required by admin single-dispatch).
    with SetWorkflowID(workflow_id):
        handle = await DBOS.start_workflow_async(
            dbos_daily_checkin_workflow,
            daily_checkin_input(input),
        )
    await handle.get_result()
    return workflow_id


def _checkin_fanout_concurrency() -> int:
    from config.settings import get_settings

    return get_settings().checkin_fanout_concurrency


@DBOS.step(name="openprogram_send_checkin_nudge", retries_allowed=True)
async def dbos_send_checkin_nudge_step(payload: NudgeInput) -> NudgeResult:
    return await nudge.send_checkin_nudge_activity(payload)


@DBOS.step(name="openprogram_close_checkin_non_response", retries_allowed=True)
async def dbos_close_checkin_non_response_step(payload: NudgeInput) -> NudgeResult:
    return await nudge.close_checkin_non_response_activity(payload)


@DBOS.workflow(name="openprogram_nudge")
async def dbos_nudge_workflow(payload: NudgeInput) -> NudgeResult:
    if payload.reply_wait_seconds > 0:
        await DBOS.sleep_async(payload.reply_wait_seconds)
    nudge_result = await dbos_send_checkin_nudge_step(payload)
    if nudge_result.status == "already_replied":
        return nudge_result
    if payload.final_reply_wait_seconds > 0:
        await DBOS.sleep_async(payload.final_reply_wait_seconds)
    close_result = await dbos_close_checkin_non_response_step(payload)
    if close_result.nudge_message_id is None:
        return NudgeResult(
            tenant_id=close_result.tenant_id,
            developer_id=close_result.developer_id,
            correlation_id=close_result.correlation_id,
            status=close_result.status,
            nudge_message_id=nudge_result.nudge_message_id,
            terminal_source=close_result.terminal_source,
        )
    return close_result


DBOS_REPLY_TOPIC = "reply"
_MAX_COALESCE_PASSES = 5


@DBOS.step(name="openprogram_drain_inbound_conversation", retries_allowed=True)
async def dbos_drain_inbound_conversation_step(
    payload: ReplyCoalesceInput,
) -> ReplyCoalesceResult:
    return await inbound_events.drain_conversation_activity(payload)


@DBOS.workflow(name="openprogram_reply_coalesce")
async def dbos_reply_coalesce_workflow(payload: ReplyCoalesceInput) -> ReplyCoalesceResult:
    async def received_before_timeout() -> bool:
        message = await DBOS.recv_async(DBOS_REPLY_TOPIC, timeout_seconds=payload.debounce_seconds)
        return message is not None

    # Reset-on-message quiet window: each signal restarts the debounce timer.
    await run_reply_debounce(received_before_timeout)
    processed = 0
    passes = 0
    # Drain, then re-check for events that arrived during the drain. The sweeper
    # is the durable backstop for anything a dropped signal or crash leaves behind.
    while passes < _MAX_COALESCE_PASSES:
        result = await dbos_drain_inbound_conversation_step(payload)
        processed += result.processed
        passes += 1
        if result.processed == 0:
            break
    return ReplyCoalesceResult(
        tenant_id=payload.tenant_id,
        conversation_key=payload.conversation_key,
        processed=processed,
        passes=passes,
    )


@DBOS.step(name="openprogram_sweep_inbound_events", retries_allowed=True)
async def dbos_sweep_inbound_events_step(payload: InboundSweeperInput) -> InboundSweeperResult:
    return await inbound_events.sweep_inbound_events_activity(payload)


@DBOS.workflow(name="openprogram_inbound_events_sweeper")
async def dbos_inbound_events_sweeper_workflow(
    payload: InboundSweeperInput,
) -> InboundSweeperResult:
    return await dbos_sweep_inbound_events_step(payload)


@DBOS.workflow(name="openprogram_scheduled_inbound_events_sweeper")
async def dbos_scheduled_inbound_events_sweeper_workflow(
    scheduled_time: datetime,
    context: dict[str, str],
) -> InboundSweeperResult:
    return await dbos_sweep_inbound_events_step(
        InboundSweeperInput(
            tenant_id=context["tenant_id"],
            grace_seconds=int(context["grace_seconds"]),
            now=scheduled_time.isoformat(),
        )
    )


async def _run_sync_dispatch(
    input: SyncDispatchInput,
) -> SyncWorkflowResult:
    workflow_input = sync_workflow_input(input)
    if isinstance(workflow_input, JiraSyncInput):
        return await dbos_sync_jira_project_step(workflow_input)
    if isinstance(workflow_input, GitSyncInput):
        return await dbos_sync_git_repo_step(workflow_input)
    if isinstance(workflow_input, CalendarSyncInput):
        return await dbos_sync_calendar_user_step(workflow_input)
    if isinstance(workflow_input, DirectorySyncInput):
        return await dbos_sync_directory_step(workflow_input)
    if isinstance(workflow_input, RuntimeSyncInput):
        return await dbos_runtime_config_sync_step(workflow_input)
    if isinstance(workflow_input, RiskAssessmentInput):
        return await dbos_run_risk_assessment_step(workflow_input)
    if isinstance(workflow_input, DriftScanInput):
        return await dbos_run_drift_scan_step(workflow_input)
    raise ValueError(f"unsupported sync connector: {input.connector}")


@dataclass(frozen=True)
class DbosWorkflowScheduler:
    app_name: str
    system_database_url: str
    schedule_id: str
    tenant_id: str
    heartbeat_cron: str
    reply_debounce_seconds: int = 30

    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        try:
            DBOS.apply_schedules(
                [
                    _heartbeat_schedule_input(
                        schedule_id=self.schedule_id,
                        tenant_id=self.tenant_id,
                        cron=self.heartbeat_cron,
                    )
                ]
            )
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=self.schedule_id, status="configured")

    async def ensure_checkin_fanout_schedule(
        self, config: CheckinScheduleConfig
    ) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        try:
            DBOS.apply_schedules([_checkin_fanout_schedule_input(config)])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")

    async def ensure_checkin_reconcile_schedule(
        self, config: CheckinReconcileScheduleConfig
    ) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        try:
            DBOS.apply_schedules([_checkin_reconcile_schedule_input(config)])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")

    async def ensure_conversation_purge_schedule(
        self, config: ConversationPurgeScheduleConfig
    ) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        try:
            DBOS.apply_schedules([_conversation_purge_schedule_input(config)])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")

    async def ensure_inbound_sweeper_schedule(
        self, config: InboundSweeperScheduleConfig
    ) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        try:
            DBOS.apply_schedules([_inbound_events_sweeper_schedule_input(config)])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")

    async def ensure_sync_schedules(
        self, configs: Sequence[SyncScheduleConfig]
    ) -> list[ScheduleBootstrapResult]:
        if not configs:
            return []
        started_runtime = _ensure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        try:
            DBOS.apply_schedules([_sync_schedule_input(config) for config in configs])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return [
            ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")
            for config in configs
        ]

    async def arm_reply_coalesce(self, conversation_key: str, tenant_id: str) -> None:
        _ensure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        coalesce_id = safe_workflow_id(f"reply-coalesce-{tenant_id}-{conversation_key}")
        # Idempotent start (no-op if the window is already running) then signal,
        # which resets the debounce timer on the running coalesce workflow.
        with SetWorkflowID(coalesce_id):
            await DBOS.start_workflow_async(
                dbos_reply_coalesce_workflow,
                ReplyCoalesceInput(
                    tenant_id=tenant_id,
                    conversation_key=conversation_key,
                    debounce_seconds=self.reply_debounce_seconds,
                ),
            )
        await DBOS.send_async(coalesce_id, "ping", DBOS_REPLY_TOPIC)

    async def dispatch_developer_checkin(self, input: DeveloperCheckinDispatch) -> str:
        _ensure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        return await _start_daily_checkin_workflow(input)

    async def dispatch_sync(self, input: SyncDispatchInput) -> str:
        workflow_input = sync_workflow_input(input)
        workflow_name = sync_workflow_name(input)
        workflow_id = safe_workflow_id(
            f"sync-{workflow_name}-{input.tenant_id}-{input.scope}-{uuid4()}"
        )
        _ensure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        with SetWorkflowID(workflow_id):
            if isinstance(workflow_input, JiraSyncInput):
                await DBOS.start_workflow_async(dbos_jira_sync_workflow, workflow_input)
            elif isinstance(workflow_input, GitSyncInput):
                await DBOS.start_workflow_async(dbos_git_sync_workflow, workflow_input)
            elif isinstance(workflow_input, CalendarSyncInput):
                await DBOS.start_workflow_async(dbos_calendar_sync_workflow, workflow_input)
            elif isinstance(workflow_input, RuntimeSyncInput):
                await DBOS.start_workflow_async(dbos_runtime_config_sync_workflow, workflow_input)
            elif isinstance(workflow_input, RiskAssessmentInput):
                await DBOS.start_workflow_async(dbos_risk_assessment_workflow, workflow_input)
            elif isinstance(workflow_input, DriftScanInput):
                await DBOS.start_workflow_async(dbos_drift_scan_workflow, workflow_input)
            else:
                raise ValueError(f"unsupported sync connector: {input.connector}")
        return workflow_id


@dataclass(frozen=True)
class DbosWorkflowWorker:
    app_name: str
    system_database_url: str

    async def run(self) -> None:
        configure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        DBOS.launch()
        try:
            await asyncio.Event().wait()
        finally:
            destroy_dbos_runtime()


@dataclass(frozen=True)
class DbosWorkflowReadinessProbe:
    app_name: str
    system_database_url: str
    _launched: bool = field(default=False, init=False, compare=False)

    async def check(self) -> bool:
        if self._launched:
            return True
        try:
            connection = await psycopg.AsyncConnection.connect(self.system_database_url)
            try:
                await connection.execute("SELECT 1")
            finally:
                await connection.close()
            configure_dbos_runtime(
                DbosRuntimeConfig(
                    app_name=self.app_name,
                    system_database_url=self.system_database_url,
                )
            )
            DBOS.launch()
        except Exception:
            destroy_dbos_runtime()
            raise
        object.__setattr__(self, "_launched", True)
        return True


def configure_dbos_runtime(config: DbosRuntimeConfig) -> None:
    global _configured_runtime
    if _configured_runtime == config:
        return
    DBOS.destroy(destroy_registry=False)
    dbos_config: DBOSConfig = {
        "name": config.app_name,
        "system_database_url": config.system_database_url,
    }
    DBOS(config=dbos_config)
    _configured_runtime = config


def _ensure_dbos_runtime(config: DbosRuntimeConfig) -> bool:
    already_configured = _configured_runtime == config
    configure_dbos_runtime(config)
    if not already_configured:
        DBOS.launch()
    return not already_configured


def destroy_dbos_runtime() -> None:
    global _configured_runtime
    DBOS.destroy(destroy_registry=False)
    _configured_runtime = None


def _heartbeat_schedule_input(
    *,
    schedule_id: str,
    tenant_id: str,
    cron: str,
) -> ScheduleInput:
    return {
        "schedule_name": schedule_id,
        "workflow_fn": cast(Any, dbos_scheduled_heartbeat_workflow),
        "schedule": cron,
        "context": {"schedule_id": schedule_id, "tenant_id": tenant_id},
        "automatic_backfill": False,
    }


def _checkin_fanout_schedule_input(config: CheckinScheduleConfig) -> ScheduleInput:
    return {
        "schedule_name": config.schedule_id,
        "workflow_fn": cast(Any, dbos_scheduled_checkin_fanout_workflow),
        "schedule": config.cron,
        "context": {
            "schedule_id": config.schedule_id,
            "tenant_id": config.tenant_id,
        },
        "automatic_backfill": True,
    }


def _checkin_reconcile_schedule_input(config: CheckinReconcileScheduleConfig) -> ScheduleInput:
    return {
        "schedule_name": config.schedule_id,
        "workflow_fn": cast(Any, dbos_scheduled_checkin_reconcile_workflow),
        "schedule": config.cron,
        "context": {
            "schedule_id": config.schedule_id,
            "tenant_id": config.tenant_id,
            "after_local_time": config.after_local_time,
            "timezone": config.timezone,
        },
        "automatic_backfill": True,
    }


def _conversation_purge_schedule_input(config: ConversationPurgeScheduleConfig) -> ScheduleInput:
    return {
        "schedule_name": config.schedule_id,
        "workflow_fn": cast(Any, dbos_scheduled_conversation_purge_workflow),
        "schedule": config.cron,
        "context": {
            "schedule_id": config.schedule_id,
            "tenant_id": config.tenant_id,
            "retention_days": str(config.retention_days),
        },
        "automatic_backfill": False,
    }


def _inbound_events_sweeper_schedule_input(
    config: InboundSweeperScheduleConfig,
) -> ScheduleInput:
    return {
        "schedule_name": config.schedule_id,
        "workflow_fn": cast(Any, dbos_scheduled_inbound_events_sweeper_workflow),
        "schedule": config.cron,
        "context": {
            "schedule_id": config.schedule_id,
            "tenant_id": config.tenant_id,
            "grace_seconds": str(config.grace_seconds),
        },
        "automatic_backfill": False,
    }


def _sync_schedule_input(config: SyncScheduleConfig) -> ScheduleInput:
    return {
        "schedule_name": config.schedule_id,
        "workflow_fn": cast(Any, dbos_scheduled_sync_workflow),
        "schedule": config.cron,
        "context": {
            "schedule_id": config.schedule_id,
            "tenant_id": config.tenant_id,
            "connector": config.connector,
            "scope": config.scope,
            "payload": dict(config.payload),
            "cron": config.cron,
        },
        "automatic_backfill": False,
    }


def _sync_schedule_config_from_context(context: dict[str, Any]) -> SyncScheduleConfig:
    payload = context.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("sync schedule context payload must be an object")
    return SyncScheduleConfig(
        schedule_id=str(context["schedule_id"]),
        tenant_id=str(context["tenant_id"]),
        connector=str(context["connector"]),
        scope=str(context["scope"]),
        payload=cast(dict[str, str | int | float | bool | None], payload),
        cron=str(context["cron"]),
    )
