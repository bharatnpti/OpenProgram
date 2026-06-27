from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from api.dependencies import get_current_principal, get_registry, get_settings_from_request
from api.dtos import (
    ChatSimulatorMessageResponse,
    ChatSimulatorMessagesResponse,
    ChatSimulatorReplyRequest,
    ChatSimulatorReplyResponse,
    ChatSimulatorStatusResponse,
)
from config.settings import Settings
from core.application.authorization import AuthorizationPolicy, Capability
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, ProviderUnavailable
from infra.observability.tracing import current_correlation_id
from infra.registry import ServiceRegistry

router = APIRouter(prefix="/test/chat-simulator", tags=["test-support"])


@router.get("/status", response_model=ChatSimulatorStatusResponse)
async def chat_simulator_status(
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> ChatSimulatorStatusResponse:
    _ensure_available(principal, registry, settings)
    return ChatSimulatorStatusResponse.model_validate(await registry.chat_simulator_status())


@router.get("/messages", response_model=ChatSimulatorMessagesResponse)
async def chat_simulator_messages(
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> ChatSimulatorMessagesResponse:
    _ensure_available(principal, registry, settings)
    return ChatSimulatorMessagesResponse(
        items=[
            ChatSimulatorMessageResponse.model_validate(message)
            for message in await registry.chat_simulator_messages()
        ]
    )


@router.post(
    "/messages/{message_id}/reply",
    response_model=ChatSimulatorReplyResponse,
)
async def chat_simulator_reply(
    message_id: str,
    request: ChatSimulatorReplyRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> ChatSimulatorReplyResponse:
    _ensure_available(principal, registry, settings)
    try:
        result = await registry.inject_chat_simulator_reply(
            message_id=message_id,
            text=request.text,
            received_at=request.received_at,
            correlation_id=current_correlation_id(),
        )
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatSimulatorReplyResponse.model_validate(result)


@router.delete("/state", status_code=status.HTTP_204_NO_CONTENT)
async def reset_chat_simulator(
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> Response:
    _ensure_available(principal, registry, settings)
    await registry.reset_chat_simulator()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _ensure_available(
    principal: Principal,
    registry: ServiceRegistry,
    settings: Settings,
) -> None:
    if not settings.chat_simulator_enabled or settings.environment != "local":
        raise HTTPException(status_code=404, detail="not found")
    if not registry.chat_simulator_available():
        raise HTTPException(status_code=404, detail="not found")
    try:
        AuthorizationPolicy().ensure(principal, Capability.MANAGE_CONFIG)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
