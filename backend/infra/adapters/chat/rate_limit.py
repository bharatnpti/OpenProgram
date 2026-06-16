from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from opentelemetry import trace
from redis.asyncio import Redis

_tracer = trace.get_tracer("pulseops.adapters.chat.rate_limit")


class RateLimiter(Protocol):
    async def acquire(self, key: str) -> None: ...


@dataclass
class InMemoryRateLimiter:
    min_interval_seconds: float = 0.0
    _last_seen: dict[str, datetime] = field(default_factory=dict)

    async def acquire(self, key: str) -> None:
        with _tracer.start_as_current_span("chat.rate_limit.memory.acquire"):
            if self.min_interval_seconds <= 0:
                self._last_seen[key] = datetime.now(tz=UTC)
                return
            now = datetime.now(tz=UTC)
            last_seen = self._last_seen.get(key)
            if last_seen is not None:
                elapsed = now - last_seen
                wait_for = timedelta(seconds=self.min_interval_seconds) - elapsed
                if wait_for.total_seconds() > 0:
                    await asyncio.sleep(wait_for.total_seconds())
            self._last_seen[key] = datetime.now(tz=UTC)


@dataclass(frozen=True)
class RedisRateLimiter:
    client: Redis
    window_seconds: int
    max_events: int

    async def acquire(self, key: str) -> None:
        with _tracer.start_as_current_span("chat.rate_limit.redis.acquire"):
            count = await self.client.incr(key)
            if count == 1:
                await self.client.expire(key, self.window_seconds)
            if int(count) > self.max_events:
                ttl = await self.client.ttl(key)
                await asyncio.sleep(float(ttl if ttl > 0 else self.window_seconds))
