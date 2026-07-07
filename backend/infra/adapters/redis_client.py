from __future__ import annotations

from dataclasses import dataclass, field

from opentelemetry import trace
from redis.asyncio import ConnectionPool, Redis

_tracer = trace.get_tracer("openprogram.adapters.redis")


@dataclass
class RedisClientProvider:
    redis_url: str
    max_connections: int
    _pool: ConnectionPool | None = field(default=None, init=False)
    _client: Redis | None = field(default=None, init=False)

    def client(self) -> Redis:
        if self._client is None:
            with _tracer.start_as_current_span("redis.client.create"):
                self._pool = ConnectionPool.from_url(
                    self.redis_url,
                    max_connections=self.max_connections,
                    decode_responses=True,
                )
                self._client = Redis(connection_pool=self._pool)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            with _tracer.start_as_current_span("redis.client.close"):
                await self._client.aclose()
            self._client = None
            self._pool = None
