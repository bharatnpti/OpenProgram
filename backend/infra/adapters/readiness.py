from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx
from opentelemetry import trace
from redis.asyncio import Redis

_tracer = trace.get_tracer("openprogram.adapters.readiness")


@dataclass(frozen=True)
class StaticReadinessProbe:
    healthy: bool = True

    async def check(self) -> bool:
        return self.healthy


class AsyncReadinessExecutor(Protocol):
    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


@dataclass(frozen=True)
class DatabaseReadinessProbe:
    executor: AsyncReadinessExecutor

    async def check(self) -> bool:
        with _tracer.start_as_current_span("readiness.database"):
            rows = await self.executor.fetch("SELECT 1 AS ok")
            return bool(rows and rows[0].get("ok") == 1)


@dataclass(frozen=True)
class DatabaseExtensionsReadinessProbe:
    executor: AsyncReadinessExecutor

    async def check(self) -> bool:
        with _tracer.start_as_current_span("readiness.database_extensions"):
            rows = await self.executor.fetch(
                """
                SELECT extname
                FROM pg_extension
                WHERE extname IN ('age', 'timescaledb', 'vector')
                """
            )
            return {str(row["extname"]) for row in rows} == {"age", "timescaledb", "vector"}


@dataclass(frozen=True)
class WorkflowBacklogReadinessProbe:
    """Healthy when the open dead-letter backlog stays at or below ``threshold``.

    Surfaces the "recorded but never finalized" failure mode: a growing backlog
    of dead-lettered inbound events makes ``/ready`` report degraded so an
    operator investigates instead of silently losing replies.
    """

    count_open: Callable[[], Awaitable[int]]
    threshold: int = 0

    async def check(self) -> bool:
        with _tracer.start_as_current_span("readiness.workflow_backlog"):
            return await self.count_open() <= self.threshold


@dataclass(frozen=True)
class RedisReadinessProbe:
    client: Redis

    async def check(self) -> bool:
        with _tracer.start_as_current_span("readiness.redis"):
            return bool(await self.client.ping())


@dataclass(frozen=True)
class HttpReadinessProbe:
    base_url: str
    path: str

    async def check(self) -> bool:
        with _tracer.start_as_current_span("readiness.http"):
            async with httpx.AsyncClient(base_url=self.base_url, timeout=3.0) as client:
                response = await client.get(self.path)
                return response.is_success
