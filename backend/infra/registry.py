from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

import httpx
from cryptography.fernet import Fernet
from redis.asyncio import Redis

from config.settings import Settings
from core.domain.messaging import InboundMessage
from core.ports.auth import AuthProvider
from core.ports.chat import ChatProvider
from core.ports.llm import LlmProvider
from core.ports.repositories import GraphRepository, TimeSeriesRepository, VectorStore
from core.ports.secrets import SecretStore
from infra.adapters.auth.dev import DevAuthProvider
from infra.adapters.chat.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from infra.adapters.chat.slack import DisabledSlackHttpClient, HttpSlackClient, SlackChatAdapter
from infra.adapters.llm.litellm_provider import LangfuseTraceSink, LiteLlmProvider, NoopTraceSink
from infra.adapters.secrets.encrypted import (
    FernetSecretStore,
    InMemoryEncryptedSecretRecordStore,
    PostgresEncryptedSecretRecordStore,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_graph import PostgresGraphRepository
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor


@dataclass
class ServiceRegistry:
    settings: Settings
    graph_store: InMemoryGraphStore | None = None
    secret_records: InMemoryEncryptedSecretRecordStore | None = None
    _postgres_executor: PsycopgAsyncExecutor | None = field(default=None, init=False)

    def graph_repository(self) -> GraphRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        return PostgresGraphRepository(self._executor())

    def time_series_repository(self) -> TimeSeriesRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        return PostgresGraphRepository(self._executor())

    def vector_store(self) -> VectorStore:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        return PostgresGraphRepository(self._executor())

    def auth_provider(self) -> AuthProvider:
        return DevAuthProvider(
            tenant_id=self.settings.tenant_id,
            subject=self.settings.dev_principal_subject,
            roles=self.settings.dev_roles,
        )

    def chat_provider(self) -> ChatProvider:
        http_client = (
            HttpSlackClient(
                bot_token=self.settings.slack_bot_token,
                base_url=self.settings.slack_api_base_url,
                retry_attempts=self.settings.slack_retry_attempts,
                retry_backoff_seconds=self.settings.slack_retry_backoff_seconds,
            )
            if self.settings.slack_bot_token
            else DisabledSlackHttpClient()
        )
        rate_limiter = (
            InMemoryRateLimiter()
            if self.settings.runtime_mode == "memory"
            else RedisRateLimiter(
                redis_url=self.settings.redis_url,
                window_seconds=self.settings.redis_rate_limit_window_seconds,
                max_events=self.settings.redis_rate_limit_max_events,
            )
        )
        return SlackChatAdapter(
            tenant_id=self.settings.tenant_id,
            http_client=http_client,
            rate_limiter=rate_limiter,
        )

    def map_chat_webhook(
        self, payload: Mapping[str, object], correlation_id: str
    ) -> InboundMessage | None:
        chat_provider = self.chat_provider()
        if isinstance(chat_provider, SlackChatAdapter):
            return chat_provider.map_webhook(payload, correlation_id)
        return None

    def llm_provider(self) -> LlmProvider:
        trace_sink = (
            NoopTraceSink()
            if self.settings.runtime_mode == "memory"
            else LangfuseTraceSink(
                host=self.settings.langfuse_host,
                public_key=_required(self.settings.langfuse_public_key, "langfuse_public_key"),
                secret_key=_required(self.settings.langfuse_secret_key, "langfuse_secret_key"),
            )
        )
        return LiteLlmProvider(
            base_url=self.settings.litellm_base_url,
            trace_sink=trace_sink,
            api_key=self.settings.litellm_api_key,
        )

    def secret_store(self) -> SecretStore:
        record_store = (
            self._memory_secret_records()
            if self.settings.runtime_mode == "memory"
            else PostgresEncryptedSecretRecordStore(self._executor())
        )
        return FernetSecretStore(
            fernet=Fernet(_fernet_key(self.settings.secret_key)),
            record_store=record_store,
        )

    async def readiness(self) -> dict[str, bool]:
        if self.settings.runtime_mode == "memory":
            return {
                "settings": True,
                "registry": True,
                "graph_repository": True,
                "redis": True,
                "temporal": True,
                "litellm": True,
                "langfuse": True,
            }
        checks: dict[str, Callable[[], Awaitable[bool]]] = {
            "database": self._check_database,
            "database_extensions": self._check_database_extensions,
            "redis": self._check_redis,
            "temporal": self._check_temporal,
            "litellm": self._check_litellm,
            "langfuse": self._check_langfuse,
        }
        results = await asyncio.gather(
            *(self._bounded_check(check) for check in checks.values()),
            return_exceptions=False,
        )
        return dict(zip(checks.keys(), results, strict=True))

    def _executor(self) -> PsycopgAsyncExecutor:
        if self._postgres_executor is None:
            self._postgres_executor = PsycopgAsyncExecutor(self.settings.database_url)
        return self._postgres_executor

    def _memory_graph_store(self) -> InMemoryGraphStore:
        if self.graph_store is None:
            self.graph_store = InMemoryGraphStore()
        return self.graph_store

    def _memory_secret_records(self) -> InMemoryEncryptedSecretRecordStore:
        if self.secret_records is None:
            self.secret_records = InMemoryEncryptedSecretRecordStore()
        return self.secret_records

    async def _bounded_check(self, check: Callable[[], Awaitable[bool]]) -> bool:
        try:
            return await asyncio.wait_for(check(), timeout=3.0)
        except Exception:
            return False

    async def _check_database(self) -> bool:
        rows = await self._executor().fetch("SELECT 1 AS ok")
        return bool(rows and rows[0].get("ok") == 1)

    async def _check_database_extensions(self) -> bool:
        rows = await self._executor().fetch(
            """
            SELECT extname
            FROM pg_extension
            WHERE extname IN ('age', 'timescaledb', 'vector')
            """
        )
        return {str(row["extname"]) for row in rows} == {"age", "timescaledb", "vector"}

    async def _check_redis(self) -> bool:
        client = Redis.from_url(self.settings.redis_url, decode_responses=True)
        try:
            return bool(await client.ping())
        finally:
            await client.aclose()

    async def _check_temporal(self) -> bool:
        from temporalio.client import Client

        await Client.connect(self.settings.temporal_target)
        return True

    async def _check_litellm(self) -> bool:
        async with httpx.AsyncClient(
            base_url=self.settings.litellm_base_url,
            timeout=3.0,
        ) as client:
            response = await client.get("/health/readiness")
            return response.is_success

    async def _check_langfuse(self) -> bool:
        async with httpx.AsyncClient(base_url=self.settings.langfuse_host, timeout=3.0) as client:
            response = await client.get("/api/public/health")
            return response.is_success


def _fernet_key(value: str) -> bytes:
    return value.encode("utf-8")


def _required(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required when runtime_mode=container")
    return value
