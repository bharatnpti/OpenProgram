from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from temporalio import activity, workflow

from core.application.sync_services import SyncRunResult
from core.domain.graph import JsonScalar
from core.domain.integrations import UserRef

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


@dataclass(frozen=True, kw_only=True)
class CalendarSyncInput:
    tenant_id: str
    user_id: str
    start: str
    end: str
    display_name: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class CalendarSyncWorkflowResult:
    connector: str
    scope: str
    items_synced: int
    cursor_value: str | None
    cursor_updated_at: str | None
    cursor_metadata: dict[str, JsonScalar]


@activity.defn
async def sync_calendar_user_activity(payload: CalendarSyncInput) -> CalendarSyncWorkflowResult:
    registry = _service_registry()
    try:
        result = await registry.calendar_read_sync_service().sync_user(
            user=UserRef(
                tenant_id=payload.tenant_id,
                external_id=payload.user_id,
                display_name=payload.display_name,
            ),
            start=date.fromisoformat(payload.start),
            end=date.fromisoformat(payload.end),
            observed_at=_optional_datetime(payload.observed_at),
        )
        return _workflow_result(result)
    finally:
        await registry.close()


@workflow.defn
class CalendarSyncWorkflow:
    @workflow.run
    async def run(self, payload: CalendarSyncInput) -> CalendarSyncWorkflowResult:
        return await workflow.execute_activity(
            sync_calendar_user_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


def _workflow_result(result: SyncRunResult) -> CalendarSyncWorkflowResult:
    return CalendarSyncWorkflowResult(
        connector=result.connector,
        scope=result.scope,
        items_synced=result.items_synced,
        cursor_value=result.cursor.value,
        cursor_updated_at=result.cursor.updated_at.isoformat()
        if result.cursor.updated_at is not None
        else None,
        cursor_metadata=dict(result.cursor.metadata),
    )


def _optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
