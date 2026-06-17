from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow

from core.domain.workflows import (
    HeartbeatInput,
    HeartbeatResult,
    ScheduleBootstrapResult,
    record_heartbeat,
)


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

        from infra.workflows.calendar_sync import CalendarSyncWorkflow, sync_calendar_user_activity
        from infra.workflows.daily_checkin import DailyCheckinWorkflow, start_daily_checkin_activity
        from infra.workflows.git_sync import GitSyncWorkflow, sync_git_repo_activity
        from infra.workflows.jira_sync import JiraSyncWorkflow, sync_jira_project_activity
        from infra.workflows.nudge import (
            NudgeWorkflow,
            close_checkin_non_response_activity,
            send_checkin_nudge_activity,
        )

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
