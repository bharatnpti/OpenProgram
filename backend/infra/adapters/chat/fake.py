from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage


@dataclass
class FakeChatProvider:
    tenant_id: str
    sent: list[OutboundMessage] = field(default_factory=list)
    replies: dict[str, InboundMessage] = field(default_factory=dict)

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        self.sent.append(message)
        return f"msg-{user.external_id}-{len(self.sent)}"

    async def open_thread(self, user: ChatUserRef) -> str:
        return f"thread-{user.external_id}"

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        return self.replies.get(thread_id)


@dataclass(frozen=True)
class FakeChatWebhookMapper:
    tenant_id: str

    def map_webhook(
        self, payload: Mapping[str, object], correlation_id: str
    ) -> InboundMessage | None:
        text = _string_field(payload, "text")
        user_id = _string_field(payload, "user_id", default="fake-user")
        thread_id = _string_field(payload, "thread_id", default=f"thread-{user_id}")
        return InboundMessage(
            tenant_id=self.tenant_id,
            user=ChatUserRef(tenant_id=self.tenant_id, external_id=user_id),
            text=text,
            thread_id=thread_id,
            message_id=_string_field(payload, "message_id", default=str(uuid4())),
            correlation_id=correlation_id,
            received_at=_datetime_field(payload, "received_at"),
            metadata={"source": "fake"},
        )


def _string_field(payload: Mapping[str, object], key: str, default: str | None = None) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    if default is not None:
        return default
    return ""


def _datetime_field(payload: Mapping[str, object], key: str) -> datetime:
    value = payload.get(key)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.now(tz=UTC)
