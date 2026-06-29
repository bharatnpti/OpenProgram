from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING

from core.application.sync_services import SyncRunResult
from core.domain.graph import JsonScalar, NodeKind

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


@dataclass(frozen=True, kw_only=True)
class JiraSyncInput:
    tenant_id: str
    project_key: str | None = None
    jql: str | None = None
    target_node_id: str | None = None
    target_node_kind: str | None = None
    cursor_scope: str | None = None
    container_id: str | None = None
    board_id: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReadSyncWorkflowResult:
    connector: str
    scope: str
    items_synced: int
    cursor_value: str | None
    cursor_updated_at: str | None
    cursor_metadata: dict[str, JsonScalar]


async def sync_jira_project_activity(payload: JiraSyncInput) -> ReadSyncWorkflowResult:
    registry = _service_registry()
    try:
        if payload.jql is not None:
            target_node_id = payload.target_node_id or payload.project_key
            if target_node_id is None:
                raise ValueError("jira query sync requires target_node_id or project_key")
            target_node_kind = NodeKind(payload.target_node_kind or NodeKind.PROJECT.value)
            result = await registry.issue_read_sync_service().sync_query(
                tenant_id=payload.tenant_id,
                jql=payload.jql,
                target_node_id=target_node_id,
                target_node_kind=target_node_kind,
                cursor_scope=payload.cursor_scope
                or _query_scope(target_node_kind, target_node_id, payload.jql),
                board_id=payload.board_id,
                observed_at=_optional_datetime(payload.observed_at),
            )
        else:
            if payload.project_key is None:
                raise ValueError("jira project sync requires project_key")
            result = await registry.issue_read_sync_service().sync_project(
                tenant_id=payload.tenant_id,
                project_key=payload.project_key,
                container_id=payload.container_id,
                board_id=payload.board_id,
                observed_at=_optional_datetime(payload.observed_at),
            )
        return _workflow_result(result)
    finally:
        await registry.close()


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


def _query_scope(kind: NodeKind, target_node_id: str, query: str) -> str:
    normalized = " ".join(query.split())
    query_hash = sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"query:{kind.value}:{target_node_id}:{query_hash}"


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
