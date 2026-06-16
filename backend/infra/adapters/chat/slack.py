from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from core.domain.errors import ProviderUnavailable
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from infra.adapters.chat.rate_limit import RateLimiter


class SlackHttpClient(Protocol):
    async def open_conversation(self, user_id: str) -> str: ...

    async def post_message(self, channel_id: str, text: str) -> str: ...

    async def latest_reply(self, thread_id: str) -> Mapping[str, object] | None: ...


@dataclass
class SlackChatAdapter:
    tenant_id: str
    http_client: SlackHttpClient
    rate_limiter: RateLimiter
    _threads: dict[str, str] = field(default_factory=dict)

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        await self.rate_limiter.acquire(f"chat:{self.tenant_id}:{user.external_id}")
        channel_id = self._threads.get(user.external_id)
        if channel_id is None:
            channel_id = await self.open_thread(user)
        return await self.http_client.post_message(channel_id, message.text)

    async def open_thread(self, user: ChatUserRef) -> str:
        await self.rate_limiter.acquire(f"chat-open:{self.tenant_id}:{user.external_id}")
        channel_id = await self.http_client.open_conversation(user.external_id)
        self._threads[user.external_id] = channel_id
        return channel_id

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        payload = await self.http_client.latest_reply(thread_id)
        if payload is None:
            return None
        return self.map_reply_payload(payload, thread_id)

    def map_reply_payload(self, payload: Mapping[str, object], thread_id: str) -> InboundMessage:
        user_id = _string_field(payload, "user")
        text = _string_field(payload, "text")
        timestamp = _string_field(payload, "ts")
        return InboundMessage(
            tenant_id=self.tenant_id,
            user=ChatUserRef(tenant_id=self.tenant_id, external_id=user_id),
            text=text,
            thread_id=thread_id,
            message_id=timestamp,
            correlation_id=_string_field(payload, "client_msg_id", default=str(uuid4())),
            received_at=datetime.now(tz=UTC),
            metadata={"source": "slack"},
        )

    def map_webhook(self, payload: Mapping[str, object], correlation_id: str) -> InboundMessage:
        event = payload.get("event")
        if not isinstance(event, Mapping):
            raise ProviderUnavailable("chat webhook payload did not contain an event object")
        thread_id = _string_field(event, "thread_ts", default=_string_field(event, "channel"))
        return InboundMessage(
            tenant_id=self.tenant_id,
            user=ChatUserRef(tenant_id=self.tenant_id, external_id=_string_field(event, "user")),
            text=_string_field(event, "text"),
            thread_id=thread_id,
            message_id=_string_field(event, "ts"),
            correlation_id=correlation_id,
            received_at=datetime.now(tz=UTC),
            metadata={"source": "slack"},
        )


class DisabledSlackHttpClient:
    async def open_conversation(self, user_id: str) -> str:
        raise ProviderUnavailable("slack_bot_token is required to open a conversation")

    async def post_message(self, channel_id: str, text: str) -> str:
        raise ProviderUnavailable("slack_bot_token is required to post a message")

    async def latest_reply(self, thread_id: str) -> Mapping[str, object] | None:
        raise ProviderUnavailable("slack_bot_token is required to fetch replies")


def _string_field(payload: Mapping[str, object], key: str, default: str | None = None) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    if default is not None:
        return default
    message = f"payload missing required string field {key}"
    raise ProviderUnavailable(message)
