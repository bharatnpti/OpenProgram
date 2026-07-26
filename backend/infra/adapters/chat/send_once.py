from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from opentelemetry import trace
from redis.asyncio import Redis

_tracer = trace.get_tracer("openprogram.adapters.chat.send_once")

# Default TTL for the container-mode send-once guard. Long enough to cover a
# workflow retry window, short enough not to accumulate keys indefinitely.
DEFAULT_SEND_ONCE_TTL_SECONDS = 86_400


class SendOnceStore(Protocol):
    """Records which idempotency keys have already produced an outbound message.

    Provider-neutral so any chat adapter can dedupe sends: memory for local/tests,
    Redis in container mode. Keyed by ``(tenant_id, idempotency_key)``.
    """

    async def get(self, tenant_id: str, idempotency_key: str) -> str | None: ...

    async def put(self, tenant_id: str, idempotency_key: str, message_id: str) -> None: ...


@dataclass
class InMemorySendOnceStore:
    _entries: dict[tuple[str, str], str] = field(default_factory=dict)

    async def get(self, tenant_id: str, idempotency_key: str) -> str | None:
        with _tracer.start_as_current_span("chat.send_once.memory.get"):
            return self._entries.get((tenant_id, idempotency_key))

    async def put(self, tenant_id: str, idempotency_key: str, message_id: str) -> None:
        with _tracer.start_as_current_span("chat.send_once.memory.put"):
            self._entries[(tenant_id, idempotency_key)] = message_id


@dataclass(frozen=True)
class RedisSendOnceStore:
    client: Redis
    ttl_seconds: int = DEFAULT_SEND_ONCE_TTL_SECONDS

    async def get(self, tenant_id: str, idempotency_key: str) -> str | None:
        with _tracer.start_as_current_span("chat.send_once.redis.get"):
            value = await self.client.get(self._key(tenant_id, idempotency_key))
            return value if isinstance(value, str) and value else None

    async def put(self, tenant_id: str, idempotency_key: str, message_id: str) -> None:
        with _tracer.start_as_current_span("chat.send_once.redis.put"):
            await self.client.set(
                self._key(tenant_id, idempotency_key),
                message_id,
                ex=self.ttl_seconds,
            )

    def _key(self, tenant_id: str, idempotency_key: str) -> str:
        return f"openprogram:chat:send-once:{tenant_id}:{idempotency_key}"
