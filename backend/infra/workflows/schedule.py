from __future__ import annotations

import asyncio

from config.settings import get_settings
from infra.registry import ServiceRegistry


async def main() -> None:
    scheduler = ServiceRegistry(get_settings()).workflow_scheduler()
    result = await scheduler.ensure_heartbeat_schedule()
    print(f"workflow schedule {result.status}: {result.schedule_id}")


if __name__ == "__main__":
    asyncio.run(main())
