from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def conversation_key(tenant_id: str, thread_ref: str) -> str:
    """Derive the per-conversation coalescing key for an inbound chat event.

    The key groups every inbound message for the same DM/thread so the debounce
    workflow can wait for quiet and process the burst as one reply.
    """
    return f"{tenant_id}:{thread_ref}"


@dataclass(frozen=True, kw_only=True)
class InboundChatEvent:
    """A durably-buffered inbound chat message awaiting coalesced processing.

    Privacy: ``text`` and ``raw_payload`` carry raw DM content. They may be
    persisted internally but must never be exposed through logs, traces,
    persona views, or public APIs (same rule as ``conversation_turns``).
    """

    tenant_id: str
    provider: str
    event_id: str
    conversation_key: str
    chat_user_ref: str
    message_ref: str
    text: str
    correlation_id: str
    chat_thread_ref: str | None = None
    outbound_message_id: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    raw_payload: str = "{}"
    id: str | None = None
    processed_at: datetime | None = None
    attempts: int = 0
