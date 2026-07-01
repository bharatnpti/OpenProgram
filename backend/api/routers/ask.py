from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_ask_service, get_current_principal
from api.dtos import AskRequest, AskResponse
from core.application.ask_service import AskService
from core.application.authorization import AuthorizationPolicy, Capability
from core.domain.auth import Principal

router = APIRouter(tags=["ask"])


@router.post("/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AskService, Depends(get_ask_service)],
) -> AskResponse:
    _ensure_aggregate(principal)
    view = await service.ask(
        tenant_id=principal.tenant_id,
        question=request.question,
        correlation_id=f"ask:{principal.subject}:{uuid4().hex}",
        as_of=request.as_of,
    )
    return AskResponse.from_view(view)


def _ensure_aggregate(principal: Principal) -> None:
    policy = AuthorizationPolicy()
    if policy.can(principal, Capability.READ_TEAM_AGGREGATE):
        return
    if policy.can(principal, Capability.READ_EXEC_AGGREGATE):
        return
    raise HTTPException(
        status_code=403,
        detail=f"{principal.subject} is not authorized for aggregate read",
    )
