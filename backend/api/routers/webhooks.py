from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from api.dependencies import get_registry
from api.dtos import ChatWebhookResponse
from infra.observability.tracing import current_correlation_id
from infra.registry import ServiceRegistry

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/chat/{provider}", response_model=ChatWebhookResponse)
async def chat_webhook(
    provider: str,
    request: Request,
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> ChatWebhookResponse:
    payload = await request.json()
    message = registry.map_chat_webhook(provider, _as_mapping(payload), current_correlation_id())
    if message is None:
        return ChatWebhookResponse(status="ignored", message_id="unsupported-provider")
    collector = registry.status_collector()
    resolved_correlation_id = await collector.resolve_reply_correlation(message)
    if resolved_correlation_id is None:
        return ChatWebhookResponse(status="ignored", message_id=message.message_id)

    checkin = await registry.status_repository().checkin_by_correlation(
        message.tenant_id,
        resolved_correlation_id,
    )
    if checkin is not None and checkin.replied_at is not None:
        return ChatWebhookResponse(status="duplicate", message_id=message.message_id)

    outcome = await collector.handle_reply(replace(message, correlation_id=resolved_correlation_id))
    return ChatWebhookResponse(
        status=outcome.kind,
        message_id=message.message_id,
    )


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}
