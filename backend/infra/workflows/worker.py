from __future__ import annotations

import asyncio

from config.settings import get_settings
from infra.registry import ServiceRegistry
from infra.workflows.schedule import ensure_workflow_schedules


async def main() -> None:
    registry = ServiceRegistry(get_settings())
    try:
        results = await ensure_workflow_schedules(registry)
        for result in results:
            print(f"workflow schedule {result.status}: {result.schedule_id}")
        await registry.workflow_worker().run()
    finally:
        await registry.close()


if __name__ == "__main__":
    asyncio.run(main())
