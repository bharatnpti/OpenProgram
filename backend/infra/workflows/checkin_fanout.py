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
        checkin_date = date.fromisoformat(payload.checkin_date)
        developers = await registry.status_repository().developers_without_checkin(
            payload.tenant_id,
            checkin_date,
        )
        workflow_ids: list[str] = []
        scheduler = registry.workflow_scheduler()
        for developer_id in developers:
            workflow_ids.append(
                await scheduler.dispatch_developer_checkin(
                    DeveloperCheckinDispatch(
                        tenant_id=payload.tenant_id,
                        developer_id=developer_id,
                        checkin_date=payload.checkin_date,
                    )
                )
            )
        return CheckinFanoutResult(
            tenant_id=payload.tenant_id,
            checkin_date=payload.checkin_date,
            dispatched=len(workflow_ids),
            workflow_ids=workflow_ids,
        )
    finally:
        await registry.close()


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
