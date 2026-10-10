from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import uuid4

import psycopg
import structlog
from dbos import DBOS, DBOSConfig, Debouncer, Queue, ScheduleInput, SetWorkflowID

# The scheduler's own cron parser, so a tick's successor is computed exactly as
# DBOS fires it (six-field crons put the seconds first).
from dbos._croniter import croniter  # type: ignore[attr-defined]

from config.settings import DEFAULT_DBOS_SYSTEM_POOL_SIZE, DEFAULT_SYNC_QUEUE_CONCURRENCY
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
    readiness,
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
from infra.workflows.readiness import ReadinessScanInput, ReadinessScanResult
from infra.workflows.risk_assessment import RiskAssessmentInput, RiskAssessmentWorkflowResult
from infra.workflows.rollup import (
    ROLLUP_REFRESH_DEBOUNCE_SECONDS,
    ROLLUP_REFRESH_MAX_WAIT_SECONDS,
    RollupInput,
    RollupWorkflowResult,
    rollup_refresh_input,
    rollup_refresh_key,
)
from infra.workflows.runtime_sync import (
    RuntimeSyncInput,
    RuntimeSyncPlan,
    RuntimeSyncWorkflowResult,
    superseded_runtime_sync_result,
)

_logger = structlog.get_logger(__name__)

SyncWorkflowResult = (
    ReadSyncWorkflowResult
    | GitSyncWorkflowResult
    | CalendarSyncWorkflowResult
    | DirectorySyncResult
    | RollupWorkflowResult
    | RuntimeSyncWorkflowResult
    | RiskAssessmentWorkflowResult
    | DriftScanWorkflowResult
    | BriefGenerationResult
    | DeliverySnapshotResult
    | DayReportDispatchResult
    | GateScanResult
    | ReadinessScanResult
)


@dataclass(frozen=True)
class DbosRuntimeConfig:
    """What one DBOS runtime is launched with.

    The pool size and the sync limit travel here, filled by whoever builds the
    config from its settings, so launching a runtime never reads the global
    settings: a caller holding only an application name and a database URL
    (an integration test, a one-off script) needs no secret key or provider.
    """

    app_name: str
    system_database_url: str
    system_pool_size: int = DEFAULT_DBOS_SYSTEM_POOL_SIZE
    sync_queue_concurrency: int = DEFAULT_SYNC_QUEUE_CONCURRENCY


_configured_runtime: DbosRuntimeConfig | None = None

# Jira and Git sync workflows wait here for a slot instead of all starting at
# once: a fan-out per repository or project after a long sleep started
# hundreds together and ran Postgres out of connections.
SYNC_QUEUE_NAME = "openprogram_sync"
_sync_queue: tuple[DbosRuntimeConfig, Queue] | None = None


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


@DBOS.step(name="openprogram_resolve_runtime_sync", retries_allowed=True)
async def dbos_resolve_runtime_sync_step(payload: RuntimeSyncInput) -> RuntimeSyncPlan:
    return await runtime_sync.resolve_runtime_sync_plan(payload)


@DBOS.workflow(name="openprogram_runtime_config_sync")
async def dbos_runtime_config_sync_workflow(
    payload: RuntimeSyncInput,
) -> RuntimeSyncWorkflowResult:
    return await _fan_out_runtime_sync(payload)


async def _fan_out_runtime_sync(payload: RuntimeSyncInput) -> RuntimeSyncWorkflowResult:
    """Resolve what runtime config asks to sync, then start one child per target.

    Resolving and dispatching are deliberately split: DBOS refuses to start a
    child workflow from inside a step, so the fan-out has to run here, in
    workflow context. Child ids are derived from this workflow's own id rather
    than a fresh uuid so a replay re-dispatches nothing.
    """
    plan = await dbos_resolve_runtime_sync_step(payload)
    parent_id = DBOS.workflow_id or f"runtime-sync-{payload.tenant_id}"
    workflow_ids = [
        await _start_sync_child_workflow(
            dispatch,
            workflow_id=safe_workflow_id(
                f"{parent_id}-{index}-{sync_workflow_name(dispatch)}-{dispatch.scope}"
            ),
        )
        for index, dispatch in enumerate(plan.dispatches)
    ]
    return RuntimeSyncWorkflowResult(
        tenant_id=payload.tenant_id,
        connector=plan.connector,
        dispatched=len(workflow_ids),
        workflow_ids=workflow_ids,
    )


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


@DBOS.step(name="openprogram_run_rollup", retries_allowed=True)
async def dbos_run_rollup_step(payload: RollupInput) -> RollupWorkflowResult:
    return await rollup.run_rollup_activity(payload)


@DBOS.workflow(name="openprogram_rollup")
async def dbos_rollup_workflow(payload: RollupInput) -> RollupWorkflowResult:
    return await dbos_run_rollup_step(payload)


@DBOS.step(name="openprogram_run_drift_scan", retries_allowed=True)
async def dbos_run_drift_scan_step(payload: DriftScanInput) -> DriftScanWorkflowResult:
    return await drift_scan.run_drift_scan_activity(payload)


@DBOS.workflow(name="openprogram_drift_scan")
async def dbos_drift_scan_workflow(payload: DriftScanInput) -> DriftScanWorkflowResult:
    return await dbos_run_drift_scan_step(payload)


@DBOS.step(name="openprogram_run_brief_generation", retries_allowed=True)
async def dbos_run_brief_generation_step(
    payload: BriefGenerationInput,
) -> BriefGenerationResult:
    return await brief_generation.run_brief_generation_activity(payload)


@DBOS.workflow(name="openprogram_brief_generation")
async def dbos_brief_generation_workflow(
    payload: BriefGenerationInput,
) -> BriefGenerationResult:
    return await dbos_run_brief_generation_step(payload)


@DBOS.step(name="openprogram_run_delivery_snapshot", retries_allowed=True)
async def dbos_run_delivery_snapshot_step(
    payload: DeliverySnapshotInput,
) -> DeliverySnapshotResult:
    return await delivery_reports.run_delivery_snapshot_activity(payload)


@DBOS.workflow(name="openprogram_delivery_snapshot")
async def dbos_delivery_snapshot_workflow(
    payload: DeliverySnapshotInput,
) -> DeliverySnapshotResult:
    return await dbos_run_delivery_snapshot_step(payload)


# Retries are safe: each report claims its local day before it is sent, so a
# retried step skips every report the failed attempt already claimed.
@DBOS.step(name="openprogram_run_day_report_dispatch", retries_allowed=True)
async def dbos_run_day_report_dispatch_step(
    payload: DayReportDispatchInput,
) -> DayReportDispatchResult:
    return await delivery_reports.run_day_report_dispatch_activity(payload)


@DBOS.workflow(name="openprogram_day_report_dispatch")
async def dbos_day_report_dispatch_workflow(
    payload: DayReportDispatchInput,
) -> DayReportDispatchResult:
    return await dbos_run_day_report_dispatch_step(payload)


@DBOS.step(name="openprogram_run_gate_scan", retries_allowed=True)
async def dbos_run_gate_scan_step(payload: GateScanInput) -> GateScanResult:
    return await delivery_reports.run_gate_scan_activity(payload)


@DBOS.workflow(name="openprogram_gate_scan")
async def dbos_gate_scan_workflow(payload: GateScanInput) -> GateScanResult:
    return await dbos_run_gate_scan_step(payload)


# Retries are safe: the run claims its slot first, so a retried step does nothing new.
@DBOS.step(name="openprogram_run_readiness_scan", retries_allowed=True)
async def dbos_run_readiness_scan_step(payload: ReadinessScanInput) -> ReadinessScanResult:
    return await readiness.run_readiness_scan_activity(payload)


@DBOS.workflow(name="openprogram_readiness_scan")
async def dbos_readiness_scan_workflow(payload: ReadinessScanInput) -> ReadinessScanResult:
    return await dbos_run_readiness_scan_step(payload)


# A step, so a recovered run keeps the answer its first attempt recorded.
@DBOS.step(name="openprogram_check_sync_tick_superseded")
async def dbos_check_sync_tick_superseded_step(scheduled_at: str, cron: str) -> bool:
    now = datetime.now(tz=UTC)
    return sync_tick_superseded(datetime.fromisoformat(scheduled_at), cron, now=now)


@DBOS.workflow(name="openprogram_scheduled_sync")
async def dbos_scheduled_sync_workflow(
    scheduled_time: datetime,
    context: dict[str, Any],
) -> SyncWorkflowResult:
    return await _run_scheduled_sync(scheduled_time, context)


async def _run_scheduled_sync(
    scheduled_time: datetime, context: dict[str, Any]
) -> SyncWorkflowResult:
    config = _sync_schedule_config_from_context(context)
    dispatch = sync_dispatch_for_schedule(config, scheduled_time)
    workflow_input = sync_workflow_input(dispatch)
    # A Jira or Git sync reads from its cursor, so only the latest due tick does
    # anything. After a sleep DBOS fires every missed tick back to back; each
    # one a newer tick has superseded returns at once, without dispatching.
    if isinstance(workflow_input, RuntimeSyncInput):
        scheduled_at = scheduled_time.isoformat()
        if await dbos_check_sync_tick_superseded_step(scheduled_at, config.cron):
            _logger.info(
                "scheduled_sync_tick_superseded",
                schedule_id=config.schedule_id,
                scheduled_at=scheduled_at,
            )
            return superseded_runtime_sync_result(workflow_input)
    if isinstance(workflow_input, ReadinessScanInput):
        return await _run_scheduled_readiness_scan(workflow_input, scheduled_time, config.cron)
    return await _run_sync_dispatch(dispatch)


async def _run_scheduled_readiness_scan(
    payload: ReadinessScanInput, scheduled_time: datetime, cron: str
) -> ReadinessScanResult:
    """The hourly readiness check, on the sync queue so it counts against the sync limit.

    A tick a newer one has superseded (a catch-up after sleep) does nothing:
    the latest tick reads everything as it stands. The child's id comes from
    the tick, so a doubled tick enqueues nothing new.
    """
    scheduled_at = scheduled_time.isoformat()
    if await dbos_check_sync_tick_superseded_step(scheduled_at, cron):
        _logger.info(
            "scheduled_readiness_tick_superseded",
            tenant_id=payload.tenant_id,
            scheduled_at=scheduled_at,
        )
        return ReadinessScanResult(
            tenant_id=payload.tenant_id, status="superseded", scopes=0, changed=0
        )
    queue = await _registered_sync_queue()
    workflow_id = safe_workflow_id(f"readiness-scan-{payload.tenant_id}-{scheduled_at}")
    with SetWorkflowID(workflow_id):
        handle = await queue.enqueue_async(dbos_readiness_scan_workflow, payload)
    return await handle.get_result()


def sync_tick_superseded(scheduled_at: datetime, cron: str, *, now: datetime) -> bool:
    """Whether the schedule's next tick after ``scheduled_at`` is already due.

    The latest due tick is never superseded, so one run always happens.
    """
    following = croniter(cron, scheduled_at, second_at_beginning=True).get_next(datetime)
    return bool(following <= now)


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
    return replace(plan.result, dispatched=len(workflow_ids), workflow_ids=workflow_ids)


async def _start_daily_checkins_concurrently(
    dispatches: Sequence[DeveloperCheckinDispatch],
) -> list[str]:
    """Fan out child check-in workflows concurrently, bounded to avoid Slack
    rate-limit spikes. Child workflows start from workflow context (never a
    retryable step). asyncio.gather preserves dispatch order in the result.

    One person's check-in failing (a chat id the provider does not know, say)
    is logged and left out of the result; it must not fail everyone else's run.
    """
    if not dispatches:
        return []
    semaphore = asyncio.Semaphore(max(1, _checkin_fanout_concurrency()))

    async def start_and_wait(dispatch: DeveloperCheckinDispatch) -> str | None:
        async with semaphore:
            try:
                return await _start_daily_checkin_workflow(dispatch)
            except Exception as exc:  # noqa: BLE001 - isolate each person's check-in
                _logger.warning(
                    "checkin_child_failed",
                    tenant_id=dispatch.tenant_id,
                    developer_id=dispatch.developer_id,
                    checkin_date=dispatch.checkin_date,
                    error_type=type(exc).__name__,
                )
                return None

    results = await asyncio.gather(*(start_and_wait(dispatch) for dispatch in dispatches))
    return [workflow_id for workflow_id in results if workflow_id is not None]


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


@DBOS.step(name="openprogram_send_escalation_step", retries_allowed=True)
async def dbos_send_escalation_step(payload: EscalationStepInput) -> NudgeResult:
    return await nudge.send_escalation_step_activity(payload)


@DBOS.step(name="openprogram_close_checkin_non_response", retries_allowed=True)
async def dbos_close_checkin_non_response_step(payload: NudgeInput) -> NudgeResult:
    return await nudge.close_checkin_non_response_activity(payload)


@DBOS.workflow(name="openprogram_nudge")
async def dbos_nudge_workflow(payload: NudgeInput) -> NudgeResult:
    last_nudge: NudgeResult | None = None
    for number, step in enumerate(payload.resolved_steps(), start=1):
        if step.wait_seconds > 0:
            await DBOS.sleep_async(step.wait_seconds)
        step_result = await dbos_send_escalation_step(payload.step_input(number, step.target))
        if step_result.status == "already_replied":
            return step_result
        if step_result.nudge_message_id is not None:
            last_nudge = step_result
    if payload.final_reply_wait_seconds > 0:
        await DBOS.sleep_async(payload.final_reply_wait_seconds)
    close_result = await dbos_close_checkin_non_response_step(payload)
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


DBOS_REPLY_TOPIC = "reply"
_MAX_COALESCE_PASSES = 5


def reply_coalesce_workflow_id(tenant_id: str, conversation_key: str, burst_key: str) -> str:
    """One DBOS coalesce workflow per buffered burst, not per conversation.

    DBOS dedupes a workflow id forever: starting a finished id returns the old
    result and runs nothing. A per-conversation id therefore left every message
    after the first burst to the sweeper. ``burst_key`` is the burst's earliest
    unprocessed event id, so messages within one burst share a workflow and the
    first message after a drain gets a new one.
    """
    return safe_workflow_id(f"reply-coalesce-{tenant_id}-{conversation_key}-{burst_key}")


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


@DBOS.step(name="openprogram_retry_cross_person_notifications", retries_allowed=True)
async def dbos_retry_cross_person_notifications_step(
    payload: CrossPersonNotifyRetryInput,
) -> CrossPersonNotifyRetryResult:
    # A retried step is safe: each DM is claimed before it is sent.
    return await cross_person_notify_retry.retry_cross_person_notifications_activity(payload)


@DBOS.workflow(name="openprogram_scheduled_cross_person_notify_retry")
async def dbos_scheduled_cross_person_notify_retry_workflow(
    scheduled_time: datetime,
    context: dict[str, str],
) -> CrossPersonNotifyRetryResult:
    return await dbos_retry_cross_person_notifications_step(
        CrossPersonNotifyRetryInput(
            tenant_id=context["tenant_id"],
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
        return await _fan_out_runtime_sync(workflow_input)
    return await _run_derived_step(workflow_input, input.connector)


async def _run_derived_step(workflow_input: object, connector: str) -> SyncWorkflowResult:
    """The steps that derive from stored state rather than read a provider."""
    if isinstance(workflow_input, RiskAssessmentInput):
        return await dbos_run_risk_assessment_step(workflow_input)
    if isinstance(workflow_input, RollupInput):
        return await dbos_run_rollup_step(workflow_input)
    if isinstance(workflow_input, DriftScanInput):
        return await dbos_run_drift_scan_step(workflow_input)
    if isinstance(workflow_input, BriefGenerationInput):
        return await dbos_run_brief_generation_step(workflow_input)
    if isinstance(workflow_input, DeliverySnapshotInput):
        return await dbos_run_delivery_snapshot_step(workflow_input)
    if isinstance(workflow_input, DayReportDispatchInput):
        return await dbos_run_day_report_dispatch_step(workflow_input)
    if isinstance(workflow_input, GateScanInput):
        return await dbos_run_gate_scan_step(workflow_input)
    if isinstance(workflow_input, ReadinessScanInput):
        return await dbos_run_readiness_scan_step(workflow_input)
    raise ValueError(f"unsupported sync connector: {connector}")


async def _start_sync_child_workflow(input: SyncDispatchInput, *, workflow_id: str) -> str:
    """Start the workflow that serves one sync dispatch, under a chosen id.

    Jira and Git syncs and the readiness check are enqueued on the sync queue
    and start when it has a slot; every other kind starts at once.
    """
    workflow_input = sync_workflow_input(input)
    if isinstance(workflow_input, JiraSyncInput | GitSyncInput | ReadinessScanInput):
        queue = await _registered_sync_queue()
    with SetWorkflowID(workflow_id):
        if isinstance(workflow_input, JiraSyncInput):
            await queue.enqueue_async(dbos_jira_sync_workflow, workflow_input)
        elif isinstance(workflow_input, GitSyncInput):
            await queue.enqueue_async(dbos_git_sync_workflow, workflow_input)
        elif isinstance(workflow_input, ReadinessScanInput):
            await queue.enqueue_async(dbos_readiness_scan_workflow, workflow_input)
        elif isinstance(workflow_input, CalendarSyncInput):
            await DBOS.start_workflow_async(dbos_calendar_sync_workflow, workflow_input)
        elif isinstance(workflow_input, DirectorySyncInput):
            await DBOS.start_workflow_async(dbos_directory_sync_workflow, workflow_input)
        elif isinstance(workflow_input, RuntimeSyncInput):
            await DBOS.start_workflow_async(dbos_runtime_config_sync_workflow, workflow_input)
        else:
            await _start_derived_workflow(workflow_input, input.connector)
    return workflow_id


async def _start_derived_workflow(workflow_input: object, connector: str) -> None:
    if isinstance(workflow_input, RiskAssessmentInput):
        await DBOS.start_workflow_async(dbos_risk_assessment_workflow, workflow_input)
    elif isinstance(workflow_input, RollupInput):
        await DBOS.start_workflow_async(dbos_rollup_workflow, workflow_input)
    elif isinstance(workflow_input, DriftScanInput):
        await DBOS.start_workflow_async(dbos_drift_scan_workflow, workflow_input)
    elif isinstance(workflow_input, BriefGenerationInput):
        await DBOS.start_workflow_async(dbos_brief_generation_workflow, workflow_input)
    elif isinstance(workflow_input, DeliverySnapshotInput):
        await DBOS.start_workflow_async(dbos_delivery_snapshot_workflow, workflow_input)
    elif isinstance(workflow_input, DayReportDispatchInput):
        await DBOS.start_workflow_async(dbos_day_report_dispatch_workflow, workflow_input)
    elif isinstance(workflow_input, GateScanInput):
        await DBOS.start_workflow_async(dbos_gate_scan_workflow, workflow_input)
    else:
        raise ValueError(f"unsupported sync connector: {connector}")


async def _registered_sync_queue() -> Queue:
    """The sync queue, registered once per launched runtime with the configured limit.

    The limit is global: every executor sharing the system database (the API
    and the worker both launch DBOS) counts against it. Configuration wins
    over whatever an earlier run stored.
    """
    global _sync_queue
    runtime = _configured_runtime
    if runtime is None:
        raise RuntimeError("DBOS runtime is not configured")
    if _sync_queue is None or _sync_queue[0] != runtime:
        queue = await DBOS.register_queue_async(
            SYNC_QUEUE_NAME,
            global_concurrency=runtime.sync_queue_concurrency,
            on_conflict="always_update",
        )
        _sync_queue = (runtime, queue)
    return _sync_queue[1]


def _apply_active_schedules(schedules: Sequence[ScheduleInput]) -> None:
    """Create or replace schedules, then resume them: configuration wins over a pause.

    DBOS 3.x upserts a schedule and keeps its status, so one paused by hand
    stayed paused through every worker restart even with its flag on (2.x
    replaced it). Switching a feature off is remove_schedule, not a pause.
    """
    DBOS.apply_schedules(list(schedules))
    for entry in schedules:
        DBOS.resume_schedule(entry["schedule_name"])


@dataclass(frozen=True)
class DbosWorkflowScheduler:
    app_name: str
    system_database_url: str
    schedule_id: str
    tenant_id: str
    heartbeat_cron: str
    reply_debounce_seconds: int = 30
    system_pool_size: int = DEFAULT_DBOS_SYSTEM_POOL_SIZE
    sync_queue_concurrency: int = DEFAULT_SYNC_QUEUE_CONCURRENCY

    def runtime_config(self) -> DbosRuntimeConfig:
        return DbosRuntimeConfig(
            app_name=self.app_name,
            system_database_url=self.system_database_url,
            system_pool_size=self.system_pool_size,
            sync_queue_concurrency=self.sync_queue_concurrency,
        )

    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(self.runtime_config())
        try:
            _apply_active_schedules(
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
        started_runtime = _ensure_dbos_runtime(self.runtime_config())
        try:
            _apply_active_schedules([_checkin_fanout_schedule_input(config)])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")

    async def ensure_checkin_reconcile_schedule(
        self, config: CheckinReconcileScheduleConfig
    ) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(self.runtime_config())
        try:
            _apply_active_schedules([_checkin_reconcile_schedule_input(config)])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")

    async def ensure_conversation_purge_schedule(
        self, config: ConversationPurgeScheduleConfig
    ) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(self.runtime_config())
        try:
            _apply_active_schedules([_conversation_purge_schedule_input(config)])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")

    async def ensure_inbound_sweeper_schedule(
        self, config: InboundSweeperScheduleConfig
    ) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(self.runtime_config())
        try:
            _apply_active_schedules([_inbound_events_sweeper_schedule_input(config)])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")

    async def ensure_cross_person_notify_retry_schedule(
        self, config: CrossPersonNotifyRetryScheduleConfig
    ) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(self.runtime_config())
        try:
            _apply_active_schedules([_cross_person_notify_retry_schedule_input(config)])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")

    async def ensure_sync_schedules(
        self, configs: Sequence[SyncScheduleConfig]
    ) -> list[ScheduleBootstrapResult]:
        if not configs:
            return []
        started_runtime = _ensure_dbos_runtime(self.runtime_config())
        try:
            _apply_active_schedules([_sync_schedule_input(config) for config in configs])
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return [
            ScheduleBootstrapResult(schedule_id=config.schedule_id, status="configured")
            for config in configs
        ]

    async def remove_schedule(self, schedule_id: str) -> ScheduleBootstrapResult:
        started_runtime = _ensure_dbos_runtime(self.runtime_config())
        try:
            existed = DBOS.get_schedule(schedule_id) is not None
            if existed:
                DBOS.delete_schedule(schedule_id)
        finally:
            if started_runtime:
                destroy_dbos_runtime()
        return ScheduleBootstrapResult(
            schedule_id=schedule_id, status="removed" if existed else "absent"
        )

    async def arm_reply_coalesce(
        self, conversation_key: str, tenant_id: str, *, burst_key: str
    ) -> None:
        _ensure_dbos_runtime(self.runtime_config())
        coalesce_id = reply_coalesce_workflow_id(tenant_id, conversation_key, burst_key)
        # Idempotent start (no-op while this burst's window is running) then
        # signal, which resets the debounce timer on that coalesce workflow.
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
        _ensure_dbos_runtime(self.runtime_config())
        return await _start_daily_checkin_workflow(input)

    async def dispatch_sync(self, input: SyncDispatchInput) -> str:
        workflow_name = sync_workflow_name(input)
        workflow_id = safe_workflow_id(
            f"sync-{workflow_name}-{input.tenant_id}-{input.scope}-{uuid4()}"
        )
        _ensure_dbos_runtime(self.runtime_config())
        return await _start_sync_child_workflow(input, workflow_id=workflow_id)


@dataclass(frozen=True)
class DbosRollupRefresher:
    """Debounces the existing rollup workflow for one tenant and day (N27).

    Each request extends one DELAYED ``openprogram_rollup`` run keyed on the
    tenant and the day, so the six per-repository merge passes of a sync run,
    and anything else in the same burst, record the day once:
    ``debounce_seconds`` after the last request, and at most
    ``ROLLUP_REFRESH_MAX_WAIT_SECONDS`` after the first. Once that run has
    started, DBOS frees the key, so a request made while it reads starts a
    new run and nothing is lost.
    """

    app_name: str
    system_database_url: str
    debounce_seconds: float = ROLLUP_REFRESH_DEBOUNCE_SECONDS
    system_pool_size: int = DEFAULT_DBOS_SYSTEM_POOL_SIZE
    sync_queue_concurrency: int = DEFAULT_SYNC_QUEUE_CONCURRENCY

    def runtime_config(self) -> DbosRuntimeConfig:
        return DbosRuntimeConfig(
            app_name=self.app_name,
            system_database_url=self.system_database_url,
            system_pool_size=self.system_pool_size,
            sync_queue_concurrency=self.sync_queue_concurrency,
        )

    async def refresh_rollup(self, tenant_id: str, as_of: date) -> None:
        _ensure_dbos_runtime(self.runtime_config())
        # The requests come from inside DBOS steps (the repository sync's merge
        # pass, a reply drain), and DBOS refuses to start a workflow in a step,
        # which a retry would start again. A repeated debounce is harmless (it
        # extends the same run, or records the same rows again), so it is made
        # outside the step's context, as a request from outside any workflow.
        await asyncio.to_thread(
            contextvars.Context().run,
            _debounce_rollup,
            tenant_id,
            as_of,
            self.debounce_seconds,
        )


def _debounce_rollup(tenant_id: str, as_of: date, debounce_seconds: float) -> None:
    debouncer = Debouncer.create_async(
        dbos_rollup_workflow, debounce_timeout_sec=ROLLUP_REFRESH_MAX_WAIT_SECONDS
    )
    debouncer.debounce(
        rollup_refresh_key(tenant_id, as_of),
        debounce_seconds,
        rollup_refresh_input(tenant_id, as_of),
    )


@dataclass(frozen=True)
class DbosWorkflowWorker:
    app_name: str
    system_database_url: str
    system_pool_size: int = DEFAULT_DBOS_SYSTEM_POOL_SIZE
    sync_queue_concurrency: int = DEFAULT_SYNC_QUEUE_CONCURRENCY

    def runtime_config(self) -> DbosRuntimeConfig:
        return DbosRuntimeConfig(
            app_name=self.app_name,
            system_database_url=self.system_database_url,
            system_pool_size=self.system_pool_size,
            sync_queue_concurrency=self.sync_queue_concurrency,
        )

    async def run(self) -> None:
        configure_dbos_runtime(self.runtime_config())
        DBOS.launch()
        try:
            # Stores the configured sync concurrency as the worker starts, so a
            # changed setting holds before the first sync is enqueued.
            await _registered_sync_queue()
            await asyncio.Event().wait()
        finally:
            destroy_dbos_runtime()


@dataclass(frozen=True)
class DbosWorkflowReadinessProbe:
    app_name: str
    system_database_url: str
    system_pool_size: int = DEFAULT_DBOS_SYSTEM_POOL_SIZE
    sync_queue_concurrency: int = DEFAULT_SYNC_QUEUE_CONCURRENCY
    _launched: bool = field(default=False, init=False, compare=False)

    def runtime_config(self) -> DbosRuntimeConfig:
        return DbosRuntimeConfig(
            app_name=self.app_name,
            system_database_url=self.system_database_url,
            system_pool_size=self.system_pool_size,
            sync_queue_concurrency=self.sync_queue_concurrency,
        )

    async def check(self) -> bool:
        if self._launched:
            return True
        try:
            connection = await psycopg.AsyncConnection.connect(self.system_database_url)
            try:
                await connection.execute("SELECT 1")
            finally:
                await connection.close()
            configure_dbos_runtime(self.runtime_config())
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
        # Bounded explicitly: with max_overflow 0 (DBOS's default) this is the
        # most the pool opens, plus one connection for the LISTEN/NOTIFY thread.
        "sys_db_pool_size": config.system_pool_size,
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
    global _configured_runtime, _sync_queue
    DBOS.destroy(destroy_registry=False)
    _configured_runtime = None
    _sync_queue = None


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


def _cross_person_notify_retry_schedule_input(
    config: CrossPersonNotifyRetryScheduleConfig,
) -> ScheduleInput:
    # No backfill: a missed pass is caught up by the next one, which picks up
    # every attempt that has come due in the meantime.
    return {
        "schedule_name": config.schedule_id,
        "workflow_fn": cast(Any, dbos_scheduled_cross_person_notify_retry_workflow),
        "schedule": config.cron,
        "context": {
            "schedule_id": config.schedule_id,
            "tenant_id": config.tenant_id,
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
