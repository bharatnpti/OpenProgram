"""A circuit breaker on how many direct messages one person gets in a day.

Send-once stops the same message going out twice and the rate limiter spaces
messages out, but neither stops a loop that keeps composing new ones. This
counts every post to a recipient per UTC day and refuses the post once the
day's limit is reached, raising :class:`OutboundLimitReached` (a
``ProviderUnavailable``) so the sending step fails visibly instead of the
person being flooded. The limit is sized far above a normal day: a check-in
with its follow-ups, acks, nudges and an escalation notice per team member.

A chat adapter takes one post from the cap after its send-once guard, so a
retry that returns an earlier message id is not counted again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Protocol

import structlog
from redis.asyncio import Redis

from core.domain.errors import OutboundLimitReached

_logger = structlog.get_logger(__name__)

# A day's count outlives its day a little, so a late post never resets it.
_COUNT_TTL_SECONDS = 2 * 86_400


class DailySendCap(Protocol):
    async def take(self, tenant_id: str, recipient: str) -> None:
        """Count one post to the recipient today; raise once today's limit is spent."""
        ...


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class InMemoryDailySendCap:
    #: Posts per recipient per UTC day; 0 is no limit.
    limit: int = 0
    clock: Callable[[], datetime] = field(default=_utc_now)
    _counts: dict[tuple[str, str, date], int] = field(default_factory=dict)

    async def take(self, tenant_id: str, recipient: str) -> None:
        if not self.limit:
            return
        key = (tenant_id, recipient, self.clock().astimezone(UTC).date())
        # Count and compare with no await in between: one coroutine wins.
        self._counts[key] = self._counts.get(key, 0) + 1
        _refuse_over_limit(self._counts[key], self.limit, tenant_id, recipient)


@dataclass(frozen=True)
class RedisDailySendCap:
    client: Redis
    limit: int
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def take(self, tenant_id: str, recipient: str) -> None:
        if not self.limit:
            return
        day = self.clock().astimezone(UTC).date().isoformat()
        key = f"openprogram:chat:daily-dms:{tenant_id}:{recipient}:{day}"
        # INCR first: of two posts racing for the last slot, one is refused.
        count = int(await self.client.incr(key))
        if count == 1:
            await self.client.expire(key, _COUNT_TTL_SECONDS)
        _refuse_over_limit(count, self.limit, tenant_id, recipient)


def _refuse_over_limit(count: int, limit: int, tenant_id: str, recipient: str) -> None:
    if count <= limit:
        return
    _logger.error(
        "chat_daily_dm_limit_reached",
        guardrail="chat_daily_dm_limit",
        tenant_id=tenant_id,
        recipient=recipient,
        limit=limit,
    )
    msg = f"recipient has had today's limit of {limit} direct messages"
    raise OutboundLimitReached(msg)
