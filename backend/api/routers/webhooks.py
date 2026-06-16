from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from api.dependencies import get_registry
from api.dtos import ChatWebhookResponse
from infra.observability.tracing import current_correlation_id
from infra.registry import ServiceRegistry

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/chat/slack", response_model=ChatWebhookResponse)
async def slack_webhook(
    request: Request,
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> ChatWebhookResponse:
    payload = await request.json()
    message = registry.map_chat_webhook(_as_mapping(payload), current_correlation_id())
    if message is None:
        return ChatWebhookResponse(status="ignored", message_id="unsupported-provider")
    return ChatWebhookResponse(status="accepted", message_id=message.message_id)


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}
