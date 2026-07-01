from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import psycopg
from dbos import DBOS, DBOSConfig, ScheduleInput, SetWorkflowID

from core.domain.workflows import (
    CheckinFanoutInput,
    CheckinFanoutResult,
    CheckinScheduleConfig,
    ConversationPurgeInput,
    ConversationPurgeResult,
    ConversationPurgeScheduleConfig,
    DeveloperCheckinDispatch,
    DirectorySyncInput,
    DirectorySyncResult,
    HeartbeatInput,
    HeartbeatResult,
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
    git_sync,
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
)


@dataclass(frozen=True)
class DbosRuntimeConfig:
    app_name: str
    system_database_url: str


_configured_runtime: DbosRuntimeConfig | None = None


@DBOS.step(name="pulseops_record_heartbeat", retries_allowed=True)
async def dbos_record_heartbeat_step(payload: HeartbeatInput) -> HeartbeatResult:
    return record_heartbeat(payload)


@DBOS.workflow(name="pulseops_heartbeat")
async def dbos_heartbeat_workflow(payload: HeartbeatInput) -> HeartbeatResult:
    return await dbos_record_heartbeat_step(payload)


@DBOS.workflow(name="pulseops_scheduled_heartbeat")
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


@DBOS.step(name="pulseops_prepare_checkin_fanout", retries_allowed=True)
async def dbos_prepare_checkin_fanout_step(
    payload: CheckinFanoutInput,
) -> list[DeveloperCheckinDispatch]:
    return await checkin_fanout.developer_checkin_dispatches_for_tenant_activity(payload)


@DBOS.workflow(name="pulseops_checkin_fanout")
async def dbos_checkin_fanout_workflow(payload: CheckinFanoutInput) -> CheckinFanoutResult:
    return await _run_dbos_checkin_fanout(payload)


@DBOS.workflow(name="pulseops_scheduled_checkin_fanout")
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


@DBOS.step(name="pulseops_purge_conversation_turns", retries_allowed=True)
async def dbos_purge_conversation_turns_step(
    payload: ConversationPurgeInput,
) -> ConversationPurgeResult:
    return await conversation_purge.purge_conversation_turns_activity(payload)


@DBOS.workflow(name="pulseops_conversation_purge")
async def dbos_conversation_purge_workflow(
    payload: ConversationPurgeInput,
) -> ConversationPurgeResult:
    return await dbos_purge_conversation_turns_step(payload)


@DBOS.workflow(name="pulseops_scheduled_conversation_purge")
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


@DBOS.step(name="pulseops_sync_jira_project", retries_allowed=True)
async def dbos_sync_jira_project_step(payload: JiraSyncInput) -> ReadSyncWorkflowResult:
    return await jira_sync.sync_jira_project_activity(payload)


@DBOS.workflow(name="pulseops_jira_sync")
async def dbos_jira_sync_workflow(payload: JiraSyncInput) -> ReadSyncWorkflowResult:
    return await dbos_sync_jira_project_step(payload)


@DBOS.step(name="pulseops_sync_git_repo", retries_allowed=True)
async def dbos_sync_git_repo_step(payload: GitSyncInput) -> GitSyncWorkflowResult:
    return await git_sync.sync_git_repo_activity(payload)


@DBOS.workflow(name="pulseops_git_sync")
async def dbos_git_sync_workflow(payload: GitSyncInput) -> GitSyncWorkflowResult:
    return await dbos_sync_git_repo_step(payload)


@DBOS.step(name="pulseops_sync_calendar_user", retries_allowed=True)
async def dbos_sync_calendar_user_step(
    payload: CalendarSyncInput,
) -> CalendarSyncWorkflowResult:
    return await calendar_sync.sync_calendar_user_activity(payload)


@DBOS.workflow(name="pulseops_calendar_sync")
async def dbos_calendar_sync_workflow(
    payload: CalendarSyncInput,
) -> CalendarSyncWorkflowResult:
    return await dbos_sync_calendar_user_step(payload)


@DBOS.step(name="pulseops_sync_directory", retries_allowed=True)
async def dbos_sync_directory_step(payload: DirectorySyncInput) -> DirectorySyncResult:
    return await directory_sync.sync_directory_activity(payload)


@DBOS.workflow(name="pulseops_directory_sync")
async def dbos_directory_sync_workflow(
    payload: DirectorySyncInput,
) -> DirectorySyncResult:
    return await dbos_sync_directory_step(payload)


@DBOS.step(name="pulseops_runtime_config_sync", retries_allowed=True)
async def dbos_runtime_config_sync_step(payload: RuntimeSyncInput) -> RuntimeSyncWorkflowResult:
    return await runtime_sync.run_runtime_config_sync_activity(payload)


@DBOS.workflow(name="pulseops_runtime_config_sync")
async def dbos_runtime_config_sync_workflow(
    payload: RuntimeSyncInput,
) -> RuntimeSyncWorkflowResult:
    return await dbos_runtime_config_sync_step(payload)


@DBOS.step(name="pulseops_run_risk_assessment", retries_allowed=True)
async def dbos_run_risk_assessment_step(
    payload: RiskAssessmentInput,
) -> RiskAssessmentWorkflowResult:
    return await risk_assessment.run_risk_assessment_activity(payload)


@DBOS.workflow(name="pulseops_risk_assessment")
async def dbos_risk_assessment_workflow(
    payload: RiskAssessmentInput,
) -> RiskAssessmentWorkflowResult:
    return await dbos_run_risk_assessment_step(payload)


@DBOS.workflow(name="pulseops_scheduled_sync")
async def dbos_scheduled_sync_workflow(
    scheduled_time: datetime,
    context: dict[str, Any],
) -> SyncWorkflowResult:
    return await _run_sync_dispatch(
        sync_dispatch_for_schedule(_sync_schedule_config_from_context(context), scheduled_time)
    )


@DBOS.step(name="pulseops_prepare_daily_checkin")
async def dbos_prepare_daily_checkin_step(payload: DailyCheckinInput) -> DailyCheckinInput:
    workflow_id = DBOS.workflow_id or "dbos-daily-checkin"
    return daily_checkin.prepare_daily_checkin_payload(
        payload,
        workflow_id=workflow_id,
        now=datetime.now(tz=UTC),
    )


@DBOS.step(name="pulseops_start_daily_checkin", retries_allowed=True)
async def dbos_start_daily_checkin_step(payload: DailyCheckinInput) -> DailyCheckinResult:
    return await daily_checkin.start_daily_checkin_activity(payload)


@DBOS.workflow(name="pulseops_daily_checkin")
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
    workflow_ids: list[str] = []
    for dispatch in dispatches:
        workflow_ids.append(await _start_daily_checkin_workflow(dispatch))
    return CheckinFanoutResult(
        tenant_id=payload.tenant_id,
        checkin_date=payload.checkin_date,
        dispatched=len(workflow_ids),
        workflow_ids=workflow_ids,
    )


async def _start_daily_checkin_workflow(input: DeveloperCheckinDispatch) -> str:
    workflow_id = safe_workflow_id(
        "checkin-"
        f"{input.tenant_id}-{input.developer_id}-"
        f"{input.checkin_date or datetime.now(tz=UTC).date().isoformat()}-{uuid4()}"
    )
    # Drive check-ins to completion so outbound chat messages exist before the
    # caller observes the workflow ID.
    with SetWorkflowID(workflow_id):
        handle = await DBOS.start_workflow_async(
            dbos_daily_checkin_workflow,
            daily_checkin_input(input),
        )
    await handle.get_result()
    return workflow_id


@DBOS.step(name="pulseops_send_checkin_nudge", retries_allowed=True)
async def dbos_send_checkin_nudge_step(payload: NudgeInput) -> NudgeResult:
    return await nudge.send_checkin_nudge_activity(payload)


@DBOS.step(name="pulseops_close_checkin_non_response", retries_allowed=True)
async def dbos_close_checkin_non_response_step(payload: NudgeInput) -> NudgeResult:
    return await nudge.close_checkin_non_response_activity(payload)


@DBOS.workflow(name="pulseops_nudge")
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
    raise ValueError(f"unsupported sync connector: {input.connector}")


@dataclass(frozen=True)
class DbosWorkflowScheduler:
    app_name: str
    system_database_url: str
    schedule_id: str
    tenant_id: str
    heartbeat_cron: str

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
        "automatic_backfill": False,
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
