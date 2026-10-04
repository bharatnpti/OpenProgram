from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from opentelemetry import trace
from redis.asyncio import Redis

_tracer = trace.get_tracer("openprogram.adapters.chat.send_once")

# Default TTL for the container-mode send-once guard. Long enough to cover a
# workflow retry window, short enough not to accumulate keys indefinitely.
DEFAULT_SEND_ONCE_TTL_SECONDS = 86_400
# How long a claim taken before a send holds its key while the send is in
# flight. A send replaces the claim with its message id within seconds; a
# sender that died mid-send frees the key after this, so a retry can send.
DEFAULT_SEND_ONCE_CLAIM_SECONDS = 30
# What a claimed key holds until its message id is recorded. Never a message id.
_CLAIMED = "\x00claimed"


class SendOnceStore(Protocol):
    """Records which idempotency keys have already produced an outbound message.

    Provider-neutral so any chat adapter can dedupe sends: memory for local/tests,
    Redis in container mode. Keyed by ``(tenant_id, idempotency_key)``.

    A sender claims the key before it posts (one atomic set-if-absent), so of
    two senders with the same key -- a retry, or two passes racing -- only one
    posts. Before this, the guard was get, post, put: in N24 two merge passes
    both read nothing, and the requester got the same notice twice, a second
    apart (the per-user rate limit held the second one).
    """

    async def get(self, tenant_id: str, idempotency_key: str) -> str | None:
        """The message id recorded for the key; None while unsent or only claimed."""
        ...

    async def put(self, tenant_id: str, idempotency_key: str, message_id: str) -> None: ...

    async def claim(self, tenant_id: str, idempotency_key: str) -> bool:
        """Take the key for one send; False when it is sent or claimed already."""
        ...

    async def release(self, tenant_id: str, idempotency_key: str) -> None:
        """Give back a claim whose send failed, so a retry can send; a recorded id stays."""
        ...


@dataclass
class InMemorySendOnceStore:
    _entries: dict[tuple[str, str], str] = field(default_factory=dict)
    _claims: set[tuple[str, str]] = field(default_factory=set)

    async def get(self, tenant_id: str, idempotency_key: str) -> str | None:
        with _tracer.start_as_current_span("chat.send_once.memory.get"):
            return self._entries.get((tenant_id, idempotency_key))

    async def put(self, tenant_id: str, idempotency_key: str, message_id: str) -> None:
        with _tracer.start_as_current_span("chat.send_once.memory.put"):
            self._entries[(tenant_id, idempotency_key)] = message_id
            self._claims.discard((tenant_id, idempotency_key))

    async def claim(self, tenant_id: str, idempotency_key: str) -> bool:
        # Check and take with no await in between: one coroutine wins.
        with _tracer.start_as_current_span("chat.send_once.memory.claim"):
            key = (tenant_id, idempotency_key)
            if key in self._entries or key in self._claims:
                return False
            self._claims.add(key)
            return True

    async def release(self, tenant_id: str, idempotency_key: str) -> None:
        with _tracer.start_as_current_span("chat.send_once.memory.release"):
            self._claims.discard((tenant_id, idempotency_key))


@dataclass(frozen=True)
class RedisSendOnceStore:
    client: Redis
    ttl_seconds: int = DEFAULT_SEND_ONCE_TTL_SECONDS
    claim_seconds: int = DEFAULT_SEND_ONCE_CLAIM_SECONDS

    async def get(self, tenant_id: str, idempotency_key: str) -> str | None:
        with _tracer.start_as_current_span("chat.send_once.redis.get"):
            value = await self.client.get(self._key(tenant_id, idempotency_key))
            if isinstance(value, str) and value and value != _CLAIMED:
                return value
            return None

    async def put(self, tenant_id: str, idempotency_key: str, message_id: str) -> None:
        with _tracer.start_as_current_span("chat.send_once.redis.put"):
            await self.client.set(
                self._key(tenant_id, idempotency_key),
                message_id,
                ex=self.ttl_seconds,
            )

    async def claim(self, tenant_id: str, idempotency_key: str) -> bool:
        with _tracer.start_as_current_span("chat.send_once.redis.claim"):
            taken = await self.client.set(
                self._key(tenant_id, idempotency_key),
                _CLAIMED,
                nx=True,
                ex=self.claim_seconds,
            )
            return bool(taken)

    async def release(self, tenant_id: str, idempotency_key: str) -> None:
        with _tracer.start_as_current_span("chat.send_once.redis.release"):
            key = self._key(tenant_id, idempotency_key)
            # Only the claim goes; a recorded message id is never deleted.
            if await self.client.get(key) == _CLAIMED:
                await self.client.delete(key)

    def _key(self, tenant_id: str, idempotency_key: str) -> str:
        return f"openprogram:chat:send-once:{tenant_id}:{idempotency_key}"
