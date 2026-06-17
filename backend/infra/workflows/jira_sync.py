from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from temporalio import activity, workflow

from core.application.sync_services import SyncRunResult
from core.domain.graph import JsonScalar

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


@dataclass(frozen=True, kw_only=True)
class JiraSyncInput:
    tenant_id: str
    project_key: str
    container_id: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReadSyncWorkflowResult:
    connector: str
    scope: str
    items_synced: int
    cursor_value: str | None
    cursor_updated_at: str | None
    cursor_metadata: dict[str, JsonScalar]


@activity.defn
async def sync_jira_project_activity(payload: JiraSyncInput) -> ReadSyncWorkflowResult:
    registry = _service_registry()
    try:
        result = await registry.issue_read_sync_service().sync_project(
            tenant_id=payload.tenant_id,
            project_key=payload.project_key,
            container_id=payload.container_id,
            observed_at=_optional_datetime(payload.observed_at),
        )
        return _workflow_result(result)
    finally:
        await registry.close()


@workflow.defn
class JiraSyncWorkflow:
    @workflow.run
    async def run(self, payload: JiraSyncInput) -> ReadSyncWorkflowResult:
        return await workflow.execute_activity(
            sync_jira_project_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


def _workflow_result(result: SyncRunResult) -> ReadSyncWorkflowResult:
    return ReadSyncWorkflowResult(
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
