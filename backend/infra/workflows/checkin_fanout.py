from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from core.domain.workflows import (
    CheckinFanoutInput,
    CheckinFanoutResult,
    DeveloperCheckinDispatch,
)

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


async def dispatch_checkins_for_tenant_activity(
    payload: CheckinFanoutInput,
) -> CheckinFanoutResult:
    registry = _service_registry()
    try:
        dispatches = await _developer_checkin_dispatches(registry, payload)
        workflow_ids: list[str] = []
        scheduler = registry.workflow_scheduler()
        for dispatch in dispatches:
            workflow_ids.append(await scheduler.dispatch_developer_checkin(dispatch))
        return CheckinFanoutResult(
            tenant_id=payload.tenant_id,
            checkin_date=payload.checkin_date,
            dispatched=len(workflow_ids),
            workflow_ids=workflow_ids,
        )
    finally:
        await registry.close()


async def developer_checkin_dispatches_for_tenant_activity(
    payload: CheckinFanoutInput,
) -> list[DeveloperCheckinDispatch]:
    registry = _service_registry()
    try:
        return await _developer_checkin_dispatches(registry, payload)
    finally:
        await registry.close()


async def _developer_checkin_dispatches(
    registry: ServiceRegistry,
    payload: CheckinFanoutInput,
) -> list[DeveloperCheckinDispatch]:
    checkin_date = date.fromisoformat(payload.checkin_date)
    developers = await registry.status_repository().developers_without_checkin(
        payload.tenant_id,
        checkin_date,
    )
    return [
        DeveloperCheckinDispatch(
            tenant_id=payload.tenant_id,
            developer_id=developer_id,
            checkin_date=payload.checkin_date,
        )
        for developer_id in developers
    ]


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
