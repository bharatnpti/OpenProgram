from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import (
    get_current_principal,
    get_dead_letter_service,
    get_registry,
    get_sync_status_service,
    get_write_back_service,
)
from api.dtos import (
    DeadLetterResponse,
    DeadLettersResponse,
    SyncStatusResponse,
    WorkflowDispatchResponse,
    WriteBackRevertResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.dead_letter_service import DeadLetterService
from core.application.sync_status_service import SyncStatusService
from core.application.writeback_service import WriteBackOff, WriteBackService
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied
from core.domain.writeback import WriteBackStatus
from infra.registry import ServiceRegistry

router = APIRouter(prefix="/admin/ops", tags=["admin"])


@router.get("/dead-letters", response_model=DeadLettersResponse)
async def list_dead_letters(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DeadLetterService, Depends(get_dead_letter_service)],
) -> DeadLettersResponse:
    _ensure_admin_ops(principal)
    dead_letters = await service.list_open(principal.tenant_id)
    return DeadLettersResponse(
        dead_letters=[DeadLetterResponse.from_domain(item) for item in dead_letters],
    )


@router.get("/sync-status", response_model=SyncStatusResponse)
async def get_sync_status(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[SyncStatusService, Depends(get_sync_status_service)],
) -> SyncStatusResponse:
    """Per-source sync health: configured targets, last success, last failure.

    Read-only and built from recorded runs; it never calls a provider.
    """
    _ensure_admin_ops(principal)
    report = await service.status(principal.tenant_id)
    return SyncStatusResponse.from_domain(report)


@router.post("/dead-letters/{dead_letter_id}/rearm", response_model=WorkflowDispatchResponse)
async def rearm_dead_letter(
    dead_letter_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DeadLetterService, Depends(get_dead_letter_service)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> WorkflowDispatchResponse:
    _ensure_admin_ops(principal)
    rearmed = await service.rearm(principal.tenant_id, dead_letter_id, datetime.now(tz=UTC))
    if rearmed is None:
        raise HTTPException(status_code=404, detail="dead-letter not found")
    # Re-drive the exact conversation so a re-armed record actually makes progress.
    drained = await registry.drain_inbound_conversation(
        principal.tenant_id, rearmed.conversation_key
    )
    return WorkflowDispatchResponse(
        workflow_id=drained.message_id or f"rearm:{rearmed.id}",
    )


@router.post("/writeback/{audit_id}/revert", response_model=WriteBackRevertResponse)
async def revert_writeback(
    audit_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WriteBackService, Depends(get_write_back_service)],
) -> WriteBackRevertResponse:
    _ensure_admin_ops(principal)
    audit = await service.get_audit(principal.tenant_id, audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="write-back audit not found")
    if audit.status is not WriteBackStatus.APPLIED or audit.before_state is None:
        raise HTTPException(
            status_code=409,
            detail="write-back is not an applied write and cannot be reverted",
        )
    # The revert goes THROUGH WriteBackService (the only sanctioned write caller),
    # which is idempotent: a second revert of the same applied write returns None.
    # While the tenant's Jira writes switch is off nothing is written, a revert neither.
    try:
        reverted = await service.revert(audit)
    except WriteBackOff as exc:
        raise HTTPException(
            status_code=409, detail=f"{exc} Nothing was reverted in the issue tracker."
        ) from exc
    if reverted is None:
        raise HTTPException(
            status_code=409,
            detail="write-back has already been reverted",
        )
    if reverted.status is WriteBackStatus.FAILED:
        raise HTTPException(
            status_code=502,
            detail="issue tracker unavailable; revert was recorded as failed",
        )
    return WriteBackRevertResponse.from_domain(reverted)


def _ensure_admin_ops(principal: Principal) -> None:
    try:
        AuthorizationPolicy().ensure(principal, Capability.DISPATCH_WORKFLOWS)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
