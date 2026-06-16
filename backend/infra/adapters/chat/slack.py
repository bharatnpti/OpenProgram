from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import httpx

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
        return SlackChatWebhookMapper(self.tenant_id).map_webhook(payload, correlation_id)


@dataclass(frozen=True)
class SlackChatWebhookMapper:
    tenant_id: str

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


@dataclass(frozen=True)
class HttpSlackClient:
    bot_token: str
    base_url: str = "https://slack.com/api"
    retry_attempts: int = 3
    retry_backoff_seconds: float = 0.25

    async def open_conversation(self, user_id: str) -> str:
        payload = await self._post("/conversations.open", json={"users": user_id})
        channel = payload.get("channel")
        if not isinstance(channel, Mapping):
            raise ProviderUnavailable("slack conversations.open response missing channel")
        return _string_field(channel, "id")

    async def post_message(self, channel_id: str, text: str) -> str:
        payload = await self._post("/chat.postMessage", json={"channel": channel_id, "text": text})
        return _string_field(payload, "ts")

    async def latest_reply(self, thread_id: str) -> Mapping[str, object] | None:
        payload = await self._get(
            "/conversations.history",
            params={"channel": thread_id, "limit": "1"},
        )
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        first = messages[0]
        return first if isinstance(first, Mapping) else None

    async def _post(self, path: str, json: Mapping[str, object]) -> Mapping[str, object]:
        return await self._request("POST", path, json=json)

    async def _get(self, path: str, params: Mapping[str, str]) -> Mapping[str, object]:
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, object] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        headers = {"Authorization": f"Bearer {self.bot_token}"}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            for attempt in range(1, self.retry_attempts + 1):
                response = await client.request(
                    method,
                    path,
                    headers=headers,
                    json=json,
                    params=params,
                )
                if response.status_code == 429 and attempt < self.retry_attempts:
                    retry_after = response.headers.get("retry-after")
                    await asyncio.sleep(
                        _retry_delay(retry_after, self.retry_backoff_seconds, attempt)
                    )
                    continue
                if response.status_code >= 500 and attempt < self.retry_attempts:
                    await asyncio.sleep(self.retry_backoff_seconds * attempt)
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise ProviderUnavailable("slack response was not an object")
                if payload.get("ok") is not True:
                    error = payload.get("error")
                    raise ProviderUnavailable(f"slack request failed: {error}")
                return payload
        raise ProviderUnavailable("slack request exhausted retry attempts")


def _retry_delay(value: str | None, fallback: float, attempt: int) -> float:
    if value is not None:
        try:
            parsed = float(value)
        except ValueError:
            parsed = 0.0
        if parsed > 0:
            return parsed
    return fallback * attempt


def _string_field(payload: Mapping[str, object], key: str, default: str | None = None) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    if default is not None:
        return default
    message = f"payload missing required string field {key}"
    raise ProviderUnavailable(message)
