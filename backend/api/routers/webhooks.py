from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.dependencies import get_registry
from api.dtos import ChatWebhookResponse
from core.domain.errors import ProviderUnavailable
from infra.observability.tracing import current_correlation_id
from infra.registry import ServiceRegistry

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/chat/{provider}", response_model=ChatWebhookResponse)
async def chat_webhook(
    provider: str,
    request: Request,
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
) -> ChatWebhookResponse | JSONResponse:
    raw_body = await request.body()
    if not registry.chat_webhook_signature_valid(provider, request.headers, raw_body):
        return JSONResponse(
            status_code=401,
            content={"detail": "invalid webhook signature"},
        )

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _ignored_response("invalid-payload")

    mapped_payload = _as_mapping(payload)
    if not mapped_payload and not isinstance(payload, Mapping):
        return _ignored_response("invalid-payload")

    challenge = _verification_challenge(mapped_payload)
    if challenge is not None:
        return JSONResponse({"challenge": challenge})

    try:
        outcome = await registry.process_chat_webhook(
            provider,
            mapped_payload,
            current_correlation_id(),
        )
    except ProviderUnavailable:
        return _ignored_response("provider-unavailable")

    return ChatWebhookResponse(
        status=outcome.status,
        message_id=outcome.message_id,
    )


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}


def _ignored_response(message_id: str) -> ChatWebhookResponse:
    return ChatWebhookResponse(status="ignored", message_id=message_id)


def _verification_challenge(payload: Mapping[str, object]) -> str | None:
    if payload.get("type") != "url_verification":
        return None
    challenge = payload.get("challenge")
    return challenge if isinstance(challenge, str) and challenge else None
