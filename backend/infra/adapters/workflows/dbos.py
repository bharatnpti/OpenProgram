from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import psycopg
from dbos import DBOS, DBOSConfig, ScheduleInput, SetWorkflowID

from core.domain.workflows import (
    HeartbeatInput,
    HeartbeatResult,
    ScheduleBootstrapResult,
    record_heartbeat,
)
from infra.workflows import calendar_sync, daily_checkin, git_sync, jira_sync, nudge
from infra.workflows.calendar_sync import CalendarSyncInput, CalendarSyncWorkflowResult
from infra.workflows.daily_checkin import DailyCheckinInput, DailyCheckinResult
from infra.workflows.git_sync import GitSyncInput, GitSyncWorkflowResult
from infra.workflows.jira_sync import JiraSyncInput, ReadSyncWorkflowResult
from infra.workflows.nudge import NudgeInput, NudgeResult


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
) -> None:
    schedule_id = context["schedule_id"]
    await dbos_record_heartbeat_step(
        HeartbeatInput(
            tenant_id=context["tenant_id"],
            heartbeat_id=f"{schedule_id}-{scheduled_time.isoformat()}",
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


@dataclass(frozen=True)
class DbosWorkflowScheduler:
    app_name: str
    system_database_url: str
    schedule_id: str
    tenant_id: str
    heartbeat_cron: str

    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult:
        configure_dbos_runtime(
            DbosRuntimeConfig(
                app_name=self.app_name,
                system_database_url=self.system_database_url,
            )
        )
        DBOS.launch()
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
            destroy_dbos_runtime()
        return ScheduleBootstrapResult(schedule_id=self.schedule_id, status="configured")


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
    system_database_url: str

    async def check(self) -> bool:
        connection = await psycopg.AsyncConnection.connect(self.system_database_url)
        try:
            await connection.execute("SELECT 1")
        finally:
            await connection.close()
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
        "workflow_fn": dbos_scheduled_heartbeat_workflow,
        "schedule": cron,
        "context": {"schedule_id": schedule_id, "tenant_id": tenant_id},
        "automatic_backfill": False,
    }
