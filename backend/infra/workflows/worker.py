from __future__ import annotations

import asyncio

from config.settings import get_settings


async def main() -> None:
    settings = get_settings()
    from temporalio.client import Client
    from temporalio.worker import Worker

    from infra.workflows.heartbeat import HeartbeatWorkflow, record_heartbeat_activity

    client = await Client.connect(settings.temporal_target)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[HeartbeatWorkflow],
        activities=[record_heartbeat_activity],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
