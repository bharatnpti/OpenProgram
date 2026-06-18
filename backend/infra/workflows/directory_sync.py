from __future__ import annotations

from typing import TYPE_CHECKING

from core.domain.workflows import DirectorySyncInput, DirectorySyncResult

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


async def sync_directory_activity(payload: DirectorySyncInput) -> DirectorySyncResult:
    registry = _service_registry()
    try:
        return await registry.directory_sync_service().sync(payload.tenant_id)
    finally:
        await registry.close()


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
