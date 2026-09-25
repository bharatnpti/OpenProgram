from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.dependencies import get_current_principal, get_registry, get_settings_from_request
from api.dtos import (
    ChatSimulatorMessageResponse,
    ChatSimulatorMessagesResponse,
    ChatSimulatorReplyRequest,
    ChatSimulatorReplyResponse,
    ChatSimulatorStatusResponse,
    ChatSimulatorUserMessageRequest,
    ChatSimulatorUserMessageResponse,
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
    _ensure_enabled(registry, settings)
    return ChatSimulatorStatusResponse.model_validate(await registry.chat_simulator_status())


@router.get("/messages", response_model=ChatSimulatorMessagesResponse)
async def chat_simulator_messages(
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
    user_id: Annotated[str | None, Query()] = None,
) -> ChatSimulatorMessagesResponse:
    """Simulator transcript, optionally narrowed to one person's own thread."""
    _ensure_enabled(registry, settings)
    _ensure_thread_access(principal, user_id)
    return ChatSimulatorMessagesResponse(
        items=[
            ChatSimulatorMessageResponse.model_validate(message)
            for message in await registry.chat_simulator_messages(user_id)
        ]
    )


@router.post(
    "/users/{user_id}/messages",
    response_model=ChatSimulatorUserMessageResponse,
)
async def chat_simulator_user_message(
    user_id: str,
    request: ChatSimulatorUserMessageRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> ChatSimulatorUserMessageResponse:
    """Send a message as one person and let it land in the rollup.

    Unlike the reply endpoint this does not need an outstanding bot question:
    if the person speaks first, the check-in is opened for them and the text
    becomes its reply.
    """
    _ensure_enabled(registry, settings)
    _ensure_thread_access(principal, user_id)
    try:
        result = await registry.send_chat_simulator_user_message(
            user_id=user_id,
            developer_id=request.developer_id or user_id,
            developer_name=request.developer_name,
            text=request.text,
            received_at=request.received_at,
            correlation_id=current_correlation_id(),
        )
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatSimulatorUserMessageResponse.model_validate(result)


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


def _ensure_enabled(registry: ServiceRegistry, settings: Settings) -> None:
    """404 unless this tenant really is running the local chat simulator."""
    if not settings.chat_simulator_enabled or settings.environment != "local":
        raise HTTPException(status_code=404, detail="not found")
    if not registry.chat_simulator_available():
        raise HTTPException(status_code=404, detail="not found")


def _ensure_thread_access(principal: Principal, user_id: str | None) -> None:
    """Own conversation needs only READ_OWN_WORK; anyone else's is a config act.

    The simulator stands in for the chat workspace, so a developer must be able
    to read and answer their own check-in thread. Reading the whole tenant
    transcript, or speaking as another person, stays behind MANAGE_CONFIG.
    """
    own_thread = user_id is not None and user_id == principal.subject
    capability = Capability.READ_OWN_WORK if own_thread else Capability.MANAGE_CONFIG
    _ensure_capability(principal, capability)


def _ensure_available(
    principal: Principal,
    registry: ServiceRegistry,
    settings: Settings,
) -> None:
    _ensure_enabled(registry, settings)
    _ensure_capability(principal, Capability.MANAGE_CONFIG)


def _ensure_capability(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
