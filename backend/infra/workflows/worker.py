from __future__ import annotations

import asyncio

import structlog

from config.settings import get_settings
from core.domain.errors import ProviderConfigurationError
from infra.adapters.chat.slack_socket import SlackSocketModeListener
from infra.registry import ServiceRegistry
from infra.workflows.schedule import ensure_workflow_schedules

_logger = structlog.get_logger(__name__)


async def main() -> None:
    registry = ServiceRegistry(get_settings())
    try:
        results = await ensure_workflow_schedules(registry)
        for result in results:
            print(f"workflow schedule {result.status}: {result.schedule_id}")
        listener = _slack_socket_listener(registry)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(registry.workflow_worker().run())
            if listener is not None:
                tasks.create_task(listener.run())
    finally:
        await registry.close()


def _slack_socket_listener(registry: ServiceRegistry) -> SlackSocketModeListener | None:
    # A missing app token leaves Slack replies undelivered but must not stop
    # schedules, syncs and check-ins: log it and let /ready report it, the same
    # way a missing bot token surfaces today.
    try:
        return registry.slack_socket_listener()
    except ProviderConfigurationError as exc:
        _logger.error("slack.socket.misconfigured", error=str(exc))
        return None


if __name__ == "__main__":
    asyncio.run(main())
