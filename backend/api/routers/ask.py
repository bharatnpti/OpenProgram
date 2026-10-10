from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from api.dependencies import get_ask_service, get_current_principal, get_investigation_service
from api.dtos import AskRequest, AskResponse, InvestigateEvent
from core.application.ask_investigation import InvestigationEvent, InvestigationService
from core.application.ask_service import AskService
from core.application.authorization import AuthorizationPolicy, Capability
from core.domain.auth import Principal

router = APIRouter(tags=["ask"])


class NdjsonResponse(StreamingResponse):
    """Newline-delimited JSON, one object per line, sent as each is ready."""

    media_type = "application/x-ndjson"


@router.post("/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[AskService, Depends(get_ask_service)],
) -> AskResponse:
    _ensure_aggregate(principal)
    view = await service.ask(
        principal=principal,
        question=request.question,
        correlation_id=f"ask:{principal.subject}:{uuid4().hex}",
        as_of=request.as_of,
    )
    return AskResponse.from_view(view)


@router.post(
    "/ask/investigate",
    response_class=NdjsonResponse,
    responses={
        200: {
            "model": InvestigateEvent,
            "description": (
                "One InvestigateEvent per line: the plan first, then each step as it "
                "finishes, then the answer or the reason there is none."
            ),
        }
    },
)
async def investigate(
    request: AskRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[InvestigationService, Depends(get_investigation_service)],
) -> NdjsonResponse:
    """Ask, investigated: the question split into steps, each looked up with Ask's tools.

    The same read as /ask, with the same tools, so it never reads more than the
    asker could. Refused before anything is streamed when the asker may not ask.
    """
    _ensure_aggregate(principal)
    events = service.investigate(
        principal=principal,
        question=request.question,
        correlation_id=f"ask-investigate:{principal.subject}:{uuid4().hex}",
        as_of=request.as_of,
    )
    # No proxy may hold the lines back until the end: each says what is happening.
    return NdjsonResponse(
        _lines(events), headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )


async def _lines(events: AsyncGenerator[InvestigationEvent]) -> AsyncIterator[str]:
    async with aclosing(events):
        async for event in events:
            yield InvestigateEvent.from_event(event).model_dump_json() + "\n"


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
