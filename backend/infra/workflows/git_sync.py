from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from core.application.sync_services import SyncRunResult
from core.domain.graph import JsonScalar

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


@dataclass(frozen=True, kw_only=True)
class GitSyncInput:
    tenant_id: str
    repo_name: str
    container_ids: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class GitSyncWorkflowResult:
    connector: str
    scope: str
    items_synced: int
    cursor_value: str | None
    cursor_updated_at: str | None
    cursor_metadata: dict[str, JsonScalar]


async def sync_git_repo_activity(payload: GitSyncInput) -> GitSyncWorkflowResult:
    registry = _service_registry()
    try:
        result = await registry.vcs_read_sync_service().sync_repo(
            tenant_id=payload.tenant_id,
            repo_name=payload.repo_name,
            container_ids=_csv_tuple(payload.container_ids),
            observed_at=_optional_datetime(payload.observed_at),
        )
        await _settle_merged_work(registry, payload.tenant_id)
        return _workflow_result(result)
    finally:
        await registry.close()


async def _settle_merged_work(registry: ServiceRegistry, tenant_id: str) -> None:
    """Close what merged merge requests have done, after each repository sync.

    A request for a review of a merge request that has since been merged is
    done whether or not anyone answered its DM. Never fails the sync: the
    facts are recorded, and the next sync settles anything this one missed.
    """
    try:
        await registry.cross_person_request_service().settle_merged_work(tenant_id)
    except Exception as error:
        import structlog

        structlog.get_logger(__name__).warning(
            "merged_work_settle_failed",
            tenant_id=tenant_id,
            error=type(error).__name__,
        )


def _workflow_result(result: SyncRunResult) -> GitSyncWorkflowResult:
    return GitSyncWorkflowResult(
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


def _csv_tuple(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
