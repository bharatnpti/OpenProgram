from __future__ import annotations

from collections.abc import Mapping
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

    outcome = await registry.process_chat_webhook(
        provider,
        mapped_payload,
        current_correlation_id(),
    )
    return ChatWebhookResponse(
        status=outcome.status,
        message_id=outcome.message_id,
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
