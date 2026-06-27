from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

from redis.asyncio import Redis

from core.domain.errors import ProviderUnavailable
from core.domain.graph import JsonScalar
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from infra.adapters.chat.rate_limit import RateLimiter

Direction = Literal["bot", "user"]


@dataclass(frozen=True, kw_only=True)
class MockSlackMessage:
    tenant_id: str
    message_id: str
    channel_id: str
    user_id: str
    direction: Direction
    text: str
    created_at: datetime
    correlation_id: str | None = None
    purpose: str | None = None
    reply_to_message_id: str | None = None
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "user_id": self.user_id,
            "direction": self.direction,
            "text": self.text,
            "created_at": self.created_at.isoformat(),
            "correlation_id": self.correlation_id,
            "purpose": self.purpose,
            "reply_to_message_id": self.reply_to_message_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> MockSlackMessage:
        created_at = _datetime_value(payload.get("created_at"))
        direction = payload.get("direction")
        if direction not in ("bot", "user"):
            raise ProviderUnavailable("stored simulator message has invalid direction")
        metadata = payload.get("metadata")
        return cls(
            tenant_id=_required_string(payload, "tenant_id"),
            message_id=_required_string(payload, "message_id"),
            channel_id=_required_string(payload, "channel_id"),
            user_id=_required_string(payload, "user_id"),
            direction=direction,
            text=_required_string(payload, "text"),
            created_at=created_at,
            correlation_id=_optional_string(payload.get("correlation_id")),
            purpose=_optional_string(payload.get("purpose")),
            reply_to_message_id=_optional_string(payload.get("reply_to_message_id")),
            metadata=_json_scalar_mapping(metadata),
        )


class MockSlackStore(Protocol):
    async def open_channel(self, tenant_id: str, user_id: str) -> str: ...

    async def record_bot_message(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        text: str,
        correlation_id: str,
        metadata: Mapping[str, JsonScalar],
        created_at: datetime | None = None,
    ) -> MockSlackMessage: ...

    async def record_user_reply(
        self,
        *,
        tenant_id: str,
        reply_to_message_id: str,
        text: str,
        created_at: datetime | None = None,
    ) -> MockSlackMessage: ...

    async def latest_user_reply(
        self,
        *,
        tenant_id: str,
        channel_id: str,
    ) -> MockSlackMessage | None: ...

    async def message_by_id(
        self,
        *,
        tenant_id: str,
        message_id: str,
    ) -> MockSlackMessage | None: ...

    async def list_messages(self, tenant_id: str) -> list[MockSlackMessage]: ...

    async def reset(self, tenant_id: str) -> None: ...


@dataclass
class InMemoryMockSlackStore:
    _channels: dict[tuple[str, str], str] = field(default_factory=dict)
    _channel_users: dict[tuple[str, str], str] = field(default_factory=dict)
    _messages: dict[str, list[MockSlackMessage]] = field(default_factory=dict)
    _counter: int = 0

    async def open_channel(self, tenant_id: str, user_id: str) -> str:
        channel_id = self._channels.get((tenant_id, user_id))
        if channel_id is None:
            channel_id = _channel_id(user_id)
            self._channels[(tenant_id, user_id)] = channel_id
            self._channel_users[(tenant_id, channel_id)] = user_id
        return channel_id

    async def record_bot_message(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        text: str,
        correlation_id: str,
        metadata: Mapping[str, JsonScalar],
        created_at: datetime | None = None,
    ) -> MockSlackMessage:
        user_id = self._channel_users.get((tenant_id, channel_id))
        if user_id is None:
            raise ProviderUnavailable("simulator channel is not open")
        observed_at = _observed_at(created_at)
        message = MockSlackMessage(
            tenant_id=tenant_id,
            message_id=self._next_message_id(observed_at),
            channel_id=channel_id,
            user_id=user_id,
            direction="bot",
            text=text,
            correlation_id=correlation_id,
            purpose=_optional_string(metadata.get("purpose")),
            metadata=dict(metadata),
            created_at=observed_at,
        )
        self._messages.setdefault(tenant_id, []).append(message)
        return message

    async def record_user_reply(
        self,
        *,
        tenant_id: str,
        reply_to_message_id: str,
        text: str,
        created_at: datetime | None = None,
    ) -> MockSlackMessage:
        parent = await self.message_by_id(tenant_id=tenant_id, message_id=reply_to_message_id)
        if parent is None or parent.direction != "bot":
            raise ProviderUnavailable("simulator outbound message was not found")
        observed_at = _observed_at(created_at)
        message = MockSlackMessage(
            tenant_id=tenant_id,
            message_id=self._next_message_id(observed_at),
            channel_id=parent.channel_id,
            user_id=parent.user_id,
            direction="user",
            text=text,
            correlation_id=parent.correlation_id,
            reply_to_message_id=parent.message_id,
            metadata={"source": "mock_slack"},
            created_at=observed_at,
        )
        self._messages.setdefault(tenant_id, []).append(message)
        return message

    async def latest_user_reply(
        self,
        *,
        tenant_id: str,
        channel_id: str,
    ) -> MockSlackMessage | None:
        for message in reversed(self._messages.get(tenant_id, [])):
            if message.channel_id == channel_id and message.direction == "user":
                return message
        return None

    async def message_by_id(
        self,
        *,
        tenant_id: str,
        message_id: str,
    ) -> MockSlackMessage | None:
        for message in self._messages.get(tenant_id, []):
            if message.message_id == message_id:
                return message
        return None

    async def list_messages(self, tenant_id: str) -> list[MockSlackMessage]:
        return list(self._messages.get(tenant_id, []))

    async def reset(self, tenant_id: str) -> None:
        self._messages.pop(tenant_id, None)
        for key in [key for key in self._channels if key[0] == tenant_id]:
            self._channels.pop(key, None)
        for key in [key for key in self._channel_users if key[0] == tenant_id]:
            self._channel_users.pop(key, None)

    def _next_message_id(self, observed_at: datetime) -> str:
        self._counter += 1
        return _message_id(observed_at, self._counter)


@dataclass(frozen=True)
class RedisMockSlackStore:
    client: Redis

    async def open_channel(self, tenant_id: str, user_id: str) -> str:
        channel_id = await self.client.hget(_channels_key(tenant_id), user_id)
        if isinstance(channel_id, str) and channel_id:
            return channel_id
        channel_id = _channel_id(user_id)
        await self.client.hset(_channels_key(tenant_id), user_id, channel_id)
        await self.client.hset(_channel_users_key(tenant_id), channel_id, user_id)
        return channel_id

    async def record_bot_message(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        text: str,
        correlation_id: str,
        metadata: Mapping[str, JsonScalar],
        created_at: datetime | None = None,
    ) -> MockSlackMessage:
        user_id = await self.client.hget(_channel_users_key(tenant_id), channel_id)
        if not isinstance(user_id, str) or not user_id:
            raise ProviderUnavailable("simulator channel is not open")
        observed_at = _observed_at(created_at)
        message = MockSlackMessage(
            tenant_id=tenant_id,
            message_id=await self._next_message_id(tenant_id, observed_at),
            channel_id=channel_id,
            user_id=user_id,
            direction="bot",
            text=text,
            correlation_id=correlation_id,
            purpose=_optional_string(metadata.get("purpose")),
            metadata=dict(metadata),
            created_at=observed_at,
        )
        await self._append_message(message)
        return message

    async def record_user_reply(
        self,
        *,
        tenant_id: str,
        reply_to_message_id: str,
        text: str,
        created_at: datetime | None = None,
    ) -> MockSlackMessage:
        parent = await self.message_by_id(tenant_id=tenant_id, message_id=reply_to_message_id)
        if parent is None or parent.direction != "bot":
            raise ProviderUnavailable("simulator outbound message was not found")
        observed_at = _observed_at(created_at)
        message = MockSlackMessage(
            tenant_id=tenant_id,
            message_id=await self._next_message_id(tenant_id, observed_at),
            channel_id=parent.channel_id,
            user_id=parent.user_id,
            direction="user",
            text=text,
            correlation_id=parent.correlation_id,
            reply_to_message_id=parent.message_id,
            metadata={"source": "mock_slack"},
            created_at=observed_at,
        )
        await self._append_message(message)
        return message

    async def latest_user_reply(
        self,
        *,
        tenant_id: str,
        channel_id: str,
    ) -> MockSlackMessage | None:
        for message in reversed(await self.list_messages(tenant_id)):
            if message.channel_id == channel_id and message.direction == "user":
                return message
        return None

    async def message_by_id(
        self,
        *,
        tenant_id: str,
        message_id: str,
    ) -> MockSlackMessage | None:
        value = await self.client.hget(_message_hash_key(tenant_id), message_id)
        if not isinstance(value, str) or not value:
            return None
        payload = json.loads(value)
        if not isinstance(payload, Mapping):
            raise ProviderUnavailable("stored simulator message was not an object")
        return MockSlackMessage.from_dict(payload)

    async def list_messages(self, tenant_id: str) -> list[MockSlackMessage]:
        ids = await self.client.lrange(_messages_key(tenant_id), 0, -1)
        if not ids:
            return []
        values = await self.client.hmget(_message_hash_key(tenant_id), ids)
        messages: list[MockSlackMessage] = []
        for value in values:
            if not isinstance(value, str) or not value:
                continue
            payload = json.loads(value)
            if isinstance(payload, Mapping):
                messages.append(MockSlackMessage.from_dict(payload))
        return messages

    async def reset(self, tenant_id: str) -> None:
        await self.client.delete(
            _channels_key(tenant_id),
            _channel_users_key(tenant_id),
            _messages_key(tenant_id),
            _message_hash_key(tenant_id),
            _counter_key(tenant_id),
        )

    async def _append_message(self, message: MockSlackMessage) -> None:
        serialized = json.dumps(message.to_dict(), sort_keys=True)
        await self.client.rpush(_messages_key(message.tenant_id), message.message_id)
        await self.client.hset(
            _message_hash_key(message.tenant_id),
            message.message_id,
            serialized,
        )

    async def _next_message_id(self, tenant_id: str, observed_at: datetime) -> str:
        counter = await self.client.incr(_counter_key(tenant_id))
        return _message_id(observed_at, int(counter))


@dataclass
class MockSlackChatAdapter:
    tenant_id: str
    store: MockSlackStore
    rate_limiter: RateLimiter

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        await self.rate_limiter.acquire(f"chat:{self.tenant_id}:{user.external_id}")
        channel_id = await self.open_thread(user)
        stored = await self.store.record_bot_message(
            tenant_id=self.tenant_id,
            channel_id=channel_id,
            text=message.text,
            correlation_id=message.correlation_id,
            metadata=message.metadata,
        )
        return stored.message_id

    async def open_thread(self, user: ChatUserRef) -> str:
        await self.rate_limiter.acquire(f"chat-open:{self.tenant_id}:{user.external_id}")
        return await self.store.open_channel(self.tenant_id, user.external_id)

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        payload = await self.latest_reply(thread_id)
        if payload is None:
            return None
        return InboundMessage(
            tenant_id=self.tenant_id,
            user=ChatUserRef(
                tenant_id=self.tenant_id, external_id=_required_string(payload, "user")
            ),
            text=_required_string(payload, "text"),
            thread_id=thread_id,
            message_id=_required_string(payload, "ts"),
            correlation_id=_required_string(payload, "client_msg_id"),
            received_at=datetime.now(tz=UTC),
            metadata={"source": "mock_slack"},
        )

    async def latest_reply(self, channel_id: str) -> Mapping[str, object] | None:
        message = await self.store.latest_user_reply(
            tenant_id=self.tenant_id,
            channel_id=channel_id,
        )
        if message is None:
            return None
        return _reply_payload(message)


@dataclass(frozen=True)
class MockSlackHttpClient:
    tenant_id: str
    store: MockSlackStore

    async def open_conversation(self, user_id: str) -> str:
        return await self.store.open_channel(self.tenant_id, user_id)

    async def post_message(self, channel_id: str, text: str) -> str:
        stored = await self.store.record_bot_message(
            tenant_id=self.tenant_id,
            channel_id=channel_id,
            text=text,
            correlation_id="manual-post",
            metadata={"purpose": "manual_post"},
        )
        return stored.message_id

    async def latest_reply(self, thread_id: str) -> Mapping[str, object] | None:
        message = await self.store.latest_user_reply(
            tenant_id=self.tenant_id,
            channel_id=thread_id,
        )
        if message is None:
            return None
        return _reply_payload(message)

    async def list_users(self, cursor: str | None = None) -> Mapping[str, object]:
        return {
            "ok": True,
            "members": [
                {
                    "id": "U1001",
                    "name": "asha",
                    "deleted": False,
                    "profile": {
                        "real_name": "Asha Rao",
                        "email": "asha@example.com",
                        "display_name": "Asha Rao",
                        "title": "Engineering Manager",
                    },
                },
                {
                    "id": "U1002",
                    "name": "liam",
                    "deleted": False,
                    "profile": {
                        "real_name": "Liam Chen",
                        "email": "liam@example.com",
                        "display_name": "Liam Chen",
                        "title": "Platform Engineer",
                    },
                },
                {
                    "id": "U1003",
                    "name": "mina",
                    "deleted": False,
                    "profile": {
                        "real_name": "Mina Patel",
                        "email": "mina@example.com",
                        "display_name": "Mina Patel",
                        "title": "Product Owner",
                    },
                },
            ],
            "response_metadata": {"next_cursor": ""},
        }


def slack_event_payload(message: MockSlackMessage) -> dict[str, object]:
    return {
        "event": {
            "user": message.user_id,
            "text": message.text,
            "ts": message.message_id,
            "channel": message.channel_id,
            "client_msg_id": message.message_id,
        }
    }


def _reply_payload(message: MockSlackMessage) -> dict[str, object]:
    return {
        "user": message.user_id,
        "text": message.text,
        "ts": message.message_id,
        "client_msg_id": message.message_id,
    }


def _message_id(observed_at: datetime, counter: int) -> str:
    return f"{int(observed_at.timestamp())}.{counter:06d}"


def _channel_id(user_id: str) -> str:
    suffix = user_id.removeprefix("U") or user_id
    return f"D{suffix}"


def _observed_at(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _channels_key(tenant_id: str) -> str:
    return f"pulseops:mock-slack:{tenant_id}:channels"


def _channel_users_key(tenant_id: str) -> str:
    return f"pulseops:mock-slack:{tenant_id}:channel-users"


def _messages_key(tenant_id: str) -> str:
    return f"pulseops:mock-slack:{tenant_id}:messages"


def _message_hash_key(tenant_id: str) -> str:
    return f"pulseops:mock-slack:{tenant_id}:message"


def _counter_key(tenant_id: str) -> str:
    return f"pulseops:mock-slack:{tenant_id}:counter"


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    raise ProviderUnavailable(f"payload missing required string field {key}")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _datetime_value(value: object) -> datetime:
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    raise ProviderUnavailable("stored simulator message missing created_at")


def _json_scalar_mapping(value: object) -> Mapping[str, JsonScalar]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, JsonScalar] = {}
    for key, item in value.items():
        if isinstance(key, str) and (isinstance(item, str | int | float | bool) or item is None):
            result[key] = item
    return result
