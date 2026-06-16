from __future__ import annotations

import asyncio

from config.settings import get_settings
from infra.registry import ServiceRegistry


async def main() -> None:
    await ServiceRegistry(get_settings()).workflow_worker().run()


if __name__ == "__main__":
    asyncio.run(main())
