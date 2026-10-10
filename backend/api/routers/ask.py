from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_ask_service, get_current_principal
from api.dtos import AskRequest, AskResponse
from core.application.ask_service import AskService, may_ask
from core.domain.auth import Principal

router = APIRouter(tags=["ask"])


@router.post("/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AskService, Depends(get_ask_service)],
) -> AskResponse:
    _ensure_may_ask(principal)
    view = await service.ask(
        principal=principal,
        question=request.question,
        correlation_id=f"ask:{principal.subject}:{uuid4().hex}",
        as_of=request.as_of,
    )
    return AskResponse.from_view(view)


def _ensure_may_ask(principal: Principal) -> None:
    if may_ask(principal):
        return
    raise HTTPException(
        status_code=403,
        detail=f"{principal.subject} is not authorized for aggregate read",
    )
