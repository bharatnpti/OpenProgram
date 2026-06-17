from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_current_principal, get_registry
from api.dtos import (
    CalendarSyncDispatchRequest,
    CheckinDispatchRequest,
    GithubSyncDispatchRequest,
    JiraSyncDispatchRequest,
    WorkflowDispatchResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied
from core.domain.workflows import DeveloperCheckinDispatch, SyncDispatchInput
from infra.registry import ServiceRegistry

router = APIRouter(prefix="/admin/workflows", tags=["admin"])


@router.post("/checkin/dispatch", response_model=WorkflowDispatchResponse)
async def dispatch_checkin(
    request: CheckinDispatchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> WorkflowDispatchResponse:
    _ensure_admin_dispatch(principal)
    workflow_id = await registry.workflow_scheduler().dispatch_developer_checkin(
        DeveloperCheckinDispatch(
            tenant_id=request.tenant_id,
            developer_id=request.developer_id,
            developer_name=request.developer_name,
            chat_external_id=request.chat_external_id,
            checkin_date=request.checkin_date.isoformat()
            if request.checkin_date is not None
            else None,
        )
    )
    return WorkflowDispatchResponse(workflow_id=workflow_id)


@router.post("/sync/jira", response_model=WorkflowDispatchResponse)
async def dispatch_jira_sync(
    request: JiraSyncDispatchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> WorkflowDispatchResponse:
    _ensure_admin_dispatch(principal)
    payload: dict[str, str | int | float | bool | None] = {
        "project_key": request.project_key,
        "container_id": request.container_id,
        "observed_at": request.observed_at.isoformat() if request.observed_at else None,
    }
    workflow_id = await registry.workflow_scheduler().dispatch_sync(
        SyncDispatchInput(
            tenant_id=request.tenant_id,
            connector="issue",
            scope=f"project:{request.project_key}",
            payload=payload,
        )
    )
    return WorkflowDispatchResponse(workflow_id=workflow_id)


@router.post("/sync/github", response_model=WorkflowDispatchResponse)
async def dispatch_github_sync(
    request: GithubSyncDispatchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> WorkflowDispatchResponse:
    _ensure_admin_dispatch(principal)
    payload: dict[str, str | int | float | bool | None] = {
        "repo_name": request.repo_name,
        "observed_at": request.observed_at.isoformat() if request.observed_at else None,
    }
    workflow_id = await registry.workflow_scheduler().dispatch_sync(
        SyncDispatchInput(
            tenant_id=request.tenant_id,
            connector="vcs",
            scope=f"repo:{request.repo_name}",
            payload=payload,
        )
    )
    return WorkflowDispatchResponse(workflow_id=workflow_id)


@router.post("/sync/calendar", response_model=WorkflowDispatchResponse)
async def dispatch_calendar_sync(
    request: CalendarSyncDispatchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> WorkflowDispatchResponse:
    _ensure_admin_dispatch(principal)
    payload: dict[str, str | int | float | bool | None] = {
        "user_id": request.user_id,
        "start": request.start.isoformat(),
        "end": request.end.isoformat(),
        "display_name": request.display_name,
        "observed_at": request.observed_at.isoformat() if request.observed_at else None,
    }
    workflow_id = await registry.workflow_scheduler().dispatch_sync(
        SyncDispatchInput(
            tenant_id=request.tenant_id,
            connector="calendar",
            scope=f"user:{request.user_id}",
            payload=payload,
        )
    )
    return WorkflowDispatchResponse(workflow_id=workflow_id)


def _ensure_admin_dispatch(principal: Principal) -> None:
    try:
        AuthorizationPolicy().ensure(principal, Capability.DISPATCH_WORKFLOWS)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
