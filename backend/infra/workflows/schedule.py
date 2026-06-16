from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from config.settings import get_settings
from infra.workflows.heartbeat import HeartbeatInput, HeartbeatWorkflow


@dataclass(frozen=True)
class ScheduleBootstrapResult:
    schedule_id: str
    status: str


async def ensure_heartbeat_schedule() -> ScheduleBootstrapResult:
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

    settings = get_settings()
    client = await Client.connect(settings.temporal_target)
    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            HeartbeatWorkflow.run,
            HeartbeatInput(
                tenant_id=settings.tenant_id,
                heartbeat_id=settings.temporal_schedule_id,
            ),
            id=f"{settings.temporal_schedule_id}-workflow",
            task_queue=settings.temporal_task_queue,
        ),
        spec=ScheduleSpec(
            intervals=[
                ScheduleIntervalSpec(
                    every=timedelta(seconds=settings.temporal_heartbeat_interval_seconds)
                )
            ]
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )
    try:
        await client.create_schedule(settings.temporal_schedule_id, schedule)
        return ScheduleBootstrapResult(schedule_id=settings.temporal_schedule_id, status="created")
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(settings.temporal_schedule_id)

        async def updater(_: object) -> ScheduleUpdate:
            return ScheduleUpdate(schedule=schedule)

        await handle.update(updater)
        return ScheduleBootstrapResult(schedule_id=settings.temporal_schedule_id, status="updated")


async def main() -> None:
    result = await ensure_heartbeat_schedule()
    print(f"temporal schedule {result.status}: {result.schedule_id}")


if __name__ == "__main__":
    asyncio.run(main())
