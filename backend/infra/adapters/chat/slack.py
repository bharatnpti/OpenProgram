from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import httpx
from opentelemetry import trace
from redis.asyncio import Redis

from core.domain.errors import ProviderConfigurationError, ProviderUnavailable
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from infra.adapters.chat.rate_limit import RateLimiter

_tracer = trace.get_tracer("openprogram.adapters.chat.slack")

_SLACK_CONFIGURATION_ERRORS = frozenset(
    {"invalid_auth", "missing_scope", "not_authed", "token_revoked"}
)
_SLACK_SCOPE_HINTS = {
    "/users.list": "users:read",
}


class SlackHttpClient(Protocol):
    async def open_conversation(self, user_id: str) -> str: ...

    async def post_message(self, channel_id: str, text: str) -> str: ...

    async def latest_reply(self, thread_id: str) -> Mapping[str, object] | None: ...

    async def list_users(self, cursor: str | None = None) -> Mapping[str, object]: ...


class ConversationCache(Protocol):
    async def get(self, user_id: str) -> str | None: ...

    async def put(self, user_id: str, channel_id: str) -> None: ...


@dataclass
class InMemoryConversationCache:
    _threads: dict[str, str] = field(default_factory=dict)

    async def get(self, user_id: str) -> str | None:
        with _tracer.start_as_current_span("slack.conversation_cache.memory.get"):
            return self._threads.get(user_id)

    async def put(self, user_id: str, channel_id: str) -> None:
        with _tracer.start_as_current_span("slack.conversation_cache.memory.put"):
            self._threads[user_id] = channel_id


@dataclass(frozen=True)
class RedisConversationCache:
    tenant_id: str
    client: Redis

    async def get(self, user_id: str) -> str | None:
        with _tracer.start_as_current_span("slack.conversation_cache.redis.get"):
            value = await self.client.get(self._key(user_id))
            return value if isinstance(value, str) and value else None

    async def put(self, user_id: str, channel_id: str) -> None:
        with _tracer.start_as_current_span("slack.conversation_cache.redis.put"):
            await self.client.set(self._key(user_id), channel_id)

    def _key(self, user_id: str) -> str:
        return f"openprogram:slack:conversation:{self.tenant_id}:{user_id}"


@dataclass
class SlackChatAdapter:
    tenant_id: str
    http_client: SlackHttpClient
    rate_limiter: RateLimiter
    conversation_cache: ConversationCache = field(default_factory=InMemoryConversationCache)

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        with _tracer.start_as_current_span("slack.send_dm"):
            await self.rate_limiter.acquire(f"chat:{self.tenant_id}:{user.external_id}")
            channel_id = await self.conversation_cache.get(user.external_id)
            if channel_id is None:
                channel_id = await self.open_thread(user)
            return await self.http_client.post_message(channel_id, message.text)

    async def open_thread(self, user: ChatUserRef) -> str:
        with _tracer.start_as_current_span("slack.open_thread"):
            await self.rate_limiter.acquire(f"chat-open:{self.tenant_id}:{user.external_id}")
            channel_id = await self.http_client.open_conversation(user.external_id)
            await self.conversation_cache.put(user.external_id, channel_id)
            return channel_id

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        with _tracer.start_as_current_span("slack.fetch_reply"):
            payload = await self.http_client.latest_reply(thread_id)
            if payload is None:
                return None
            return self.map_reply_payload(payload, thread_id)

    def map_reply_payload(self, payload: Mapping[str, object], thread_id: str) -> InboundMessage:
        with _tracer.start_as_current_span("slack.map_reply_payload"):
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

    def map_webhook(
        self, payload: Mapping[str, object], correlation_id: str
    ) -> InboundMessage | None:
        with _tracer.start_as_current_span("slack.map_webhook"):
            return SlackChatWebhookMapper(self.tenant_id).map_webhook(payload, correlation_id)


@dataclass(frozen=True)
class SlackChatWebhookMapper:
    tenant_id: str

    def map_webhook(
        self, payload: Mapping[str, object], correlation_id: str
    ) -> InboundMessage | None:
        with _tracer.start_as_current_span("slack.webhook.map"):
            event = payload.get("event")
            if not isinstance(event, Mapping):
                return None
            if "type" in event and event.get("type") != "message":
                return None
            if any(key in event for key in ("subtype", "bot_id", "app_id")):
                return None
            user_id = _optional_string(event, "user")
            text = _optional_string(event, "text")
            timestamp = _optional_string(event, "ts")
            channel = _optional_string(event, "channel")
            if user_id is None or text is None or timestamp is None or channel is None:
                return None
            thread_id = _optional_string(event, "thread_ts") or channel
            return InboundMessage(
                tenant_id=self.tenant_id,
                user=ChatUserRef(
                    tenant_id=self.tenant_id,
                    external_id=user_id,
                ),
                text=text,
                thread_id=thread_id,
                message_id=timestamp,
                correlation_id=_string_field(event, "correlation_id", default=correlation_id),
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

    async def list_users(self, cursor: str | None = None) -> Mapping[str, object]:
        raise ProviderUnavailable("slack_bot_token is required to list users")


@dataclass(frozen=True)
class HttpSlackClient:
    bot_token: str
    base_url: str = "https://slack.com/api"
    retry_attempts: int = 3
    retry_backoff_seconds: float = 0.25

    async def open_conversation(self, user_id: str) -> str:
        with _tracer.start_as_current_span("slack.http.open_conversation"):
            payload = await self._post("/conversations.open", json={"users": user_id})
            channel = payload.get("channel")
            if not isinstance(channel, Mapping):
                raise ProviderUnavailable("slack conversations.open response missing channel")
            return _string_field(channel, "id")

    async def post_message(self, channel_id: str, text: str) -> str:
        with _tracer.start_as_current_span("slack.http.post_message"):
            payload = await self._post(
                "/chat.postMessage",
                json={"channel": channel_id, "text": text},
            )
            return _string_field(payload, "ts")

    async def latest_reply(self, thread_id: str) -> Mapping[str, object] | None:
        with _tracer.start_as_current_span("slack.http.latest_reply"):
            payload = await self._get(
                "/conversations.history",
                params={"channel": thread_id, "limit": "1"},
            )
            messages = payload.get("messages")
            if not isinstance(messages, list) or not messages:
                return None
            first = messages[0]
            return first if isinstance(first, Mapping) else None

    async def list_users(self, cursor: str | None = None) -> Mapping[str, object]:
        with _tracer.start_as_current_span("slack.http.list_users"):
            params: dict[str, str] = {"limit": "200"}
            if cursor:
                params["cursor"] = cursor
            return await self._get("/users.list", params=params)

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
                    if isinstance(error, str) and error in _SLACK_CONFIGURATION_ERRORS:
                        raise ProviderConfigurationError(
                            _slack_configuration_error(path, error, payload)
                        )
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


def _slack_configuration_error(path: str, error: str, payload: Mapping[str, object]) -> str:
    method = path.removeprefix("/")
    if error == "missing_scope":
        needed = _optional_string(payload, "needed") or _SLACK_SCOPE_HINTS.get(path)
        if needed:
            return (
                f"slack bot token is missing required OAuth scope(s) for {method}: {needed}. "
                "Reinstall the Slack app after adding the scope and update slack_bot_token."
            )
        return (
            f"slack bot token is missing required OAuth scope(s) for {method}. "
            "Reinstall the Slack app after adding the scope and update slack_bot_token."
        )
    return f"slack bot token is not authorized for {method}: {error}"


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None
