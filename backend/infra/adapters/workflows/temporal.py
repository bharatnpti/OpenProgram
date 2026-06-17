from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

from temporalio import activity, workflow

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
            )
            return replace(result, nudge_workflow_id=nudge_workflow_id)
        return result


@activity.defn
async def send_checkin_nudge_activity(payload: NudgeInput) -> NudgeResult:
    return await nudge.send_checkin_nudge_activity(payload)


@activity.defn
async def close_checkin_non_response_activity(payload: NudgeInput) -> NudgeResult:
    return await nudge.close_checkin_non_response_activity(payload)


@workflow.defn
class NudgeWorkflow:
    @workflow.run
    async def run(self, payload: NudgeInput) -> NudgeResult:
        if payload.reply_wait_seconds > 0:
            await workflow.sleep(timedelta(seconds=payload.reply_wait_seconds))
        nudge_result = await workflow.execute_activity(
            send_checkin_nudge_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
        if nudge_result.status == "already_replied":
            return nudge_result
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
                nudge_message_id=nudge_result.nudge_message_id,
                terminal_source=close_result.terminal_source,
            )
        return close_result


@dataclass(frozen=True)
class TemporalWorkflowScheduler:
    target: str
    task_queue: str
    schedule_id: str
    tenant_id: str
    interval_seconds: int

    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult:
        from temporalio.client import (
            Client,
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleAlreadyRunningError,
            ScheduleIntervalSpec,
            ScheduleOverlapPolicy,
            SchedulePolicy,
            ScheduleSpec,
            ScheduleUpdate,
        )

        client = await Client.connect(self.target)
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
        try:
            await client.create_schedule(self.schedule_id, schedule)
            return ScheduleBootstrapResult(schedule_id=self.schedule_id, status="created")
        except ScheduleAlreadyRunningError:
            handle = client.get_schedule_handle(self.schedule_id)

            async def updater(_: object) -> ScheduleUpdate:
                return ScheduleUpdate(schedule=schedule)

            await handle.update(updater)
            return ScheduleBootstrapResult(schedule_id=self.schedule_id, status="updated")


@dataclass(frozen=True)
class TemporalWorkflowWorker:
    target: str
    task_queue: str

    async def run(self) -> None:
        from temporalio.client import Client
        from temporalio.worker import Worker

        client = await Client.connect(self.target)
        worker = Worker(
            client,
            task_queue=self.task_queue,
            workflows=[
                HeartbeatWorkflow,
                JiraSyncWorkflow,
                GitSyncWorkflow,
                CalendarSyncWorkflow,
                DailyCheckinWorkflow,
                NudgeWorkflow,
            ],
            activities=[
                record_heartbeat_activity,
                sync_jira_project_activity,
                sync_git_repo_activity,
                sync_calendar_user_activity,
                start_daily_checkin_activity,
                send_checkin_nudge_activity,
                close_checkin_non_response_activity,
            ],
        )
        await worker.run()


@dataclass(frozen=True)
class TemporalWorkflowReadinessProbe:
    target: str

    async def check(self) -> bool:
        from temporalio.client import Client

        await Client.connect(self.target)
        return True
