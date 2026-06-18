from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

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
) -> ChatWebhookResponse | JSONResponse:
    payload = await request.json()
    mapped_payload = _as_mapping(payload)
    challenge = _verification_challenge(mapped_payload)
    if challenge is not None:
        return JSONResponse({"challenge": challenge})

    message = registry.map_chat_webhook(provider, mapped_payload, current_correlation_id())
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


def _verification_challenge(payload: Mapping[str, object]) -> str | None:
    if payload.get("type") != "url_verification":
        return None
    challenge = payload.get("challenge")
    return challenge if isinstance(challenge, str) and challenge else None
