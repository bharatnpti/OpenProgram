from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from redis.asyncio import Redis


class RateLimiter(Protocol):
    async def acquire(self, key: str) -> None: ...


@dataclass
class InMemoryRateLimiter:
    min_interval_seconds: float = 0.0
    _last_seen: dict[str, datetime] = field(default_factory=dict)

    async def acquire(self, key: str) -> None:
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
    redis_url: str
    window_seconds: int
    max_events: int

    async def acquire(self, key: str) -> None:
        client = Redis.from_url(self.redis_url, decode_responses=True)
        try:
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, self.window_seconds)
            if int(count) > self.max_events:
                ttl = await client.ttl(key)
                await asyncio.sleep(float(ttl if ttl > 0 else self.window_seconds))
        finally:
            await client.aclose()
