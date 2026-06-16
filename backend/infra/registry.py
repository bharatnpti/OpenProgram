from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from cryptography.fernet import Fernet
from redis.asyncio import Redis

from config.settings import Settings
from core.domain.messaging import InboundMessage
from core.ports.auth import AuthProvider, CurrentPrincipal
from core.ports.chat import ChatProvider, ChatWebhookMapper
from core.ports.llm import LlmProvider
from core.ports.repositories import GraphRepository, TimeSeriesRepository, VectorStore
from core.ports.secrets import SecretStore
from core.ports.workflows import WorkflowScheduler, WorkflowWorker
from infra.adapters import catalog
from infra.adapters.auth.dev import DevAuthProvider, DevCurrentPrincipal
from infra.adapters.redis_client import RedisClientProvider
from infra.adapters.secrets.encrypted import (
    FernetSecretStore,
    InMemoryEncryptedSecretRecordStore,
    PostgresEncryptedSecretRecordStore,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_graph import (
    PostgresGraphRepository,
    PostgresTimeSeriesRepository,
    PostgresVectorStore,
)
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor


@dataclass
class ServiceRegistry:
    settings: Settings
    graph_store: InMemoryGraphStore | None = None
    secret_records: InMemoryEncryptedSecretRecordStore | None = None
    _postgres_executor: PsycopgAsyncExecutor | None = field(default=None, init=False)
    _postgres_graph_repository: PostgresGraphRepository | None = field(default=None, init=False)
    _postgres_time_series_repository: PostgresTimeSeriesRepository | None = field(
        default=None,
        init=False,
    )
    _postgres_vector_store: PostgresVectorStore | None = field(default=None, init=False)
    _redis_provider: RedisClientProvider | None = field(default=None, init=False)

    def graph_repository(self) -> GraphRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_graph_repository is None:
            self._postgres_graph_repository = PostgresGraphRepository(self._executor())
        return self._postgres_graph_repository

    def time_series_repository(self) -> TimeSeriesRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_time_series_repository is None:
            self._postgres_time_series_repository = PostgresTimeSeriesRepository(self._executor())
        return self._postgres_time_series_repository

    def vector_store(self) -> VectorStore:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_vector_store is None:
            self._postgres_vector_store = PostgresVectorStore(self._executor())
        return self._postgres_vector_store

    def auth_provider(self) -> AuthProvider:
        return DevAuthProvider(
            tenant_id=self.settings.tenant_id,
            subject=self.settings.dev_principal_subject,
            roles=self.settings.dev_roles,
        )

    def current_principal(self, token: str | None) -> CurrentPrincipal:
        return DevCurrentPrincipal(self.auth_provider(), token)

    def chat_provider(self) -> ChatProvider:
        redis_client = None if self.settings.runtime_mode == "memory" else self._redis_client()
        return catalog.build_chat_provider(self.settings, redis_client)

    def chat_webhook_mapper(self, provider: str) -> ChatWebhookMapper | None:
        return catalog.build_chat_webhook_mapper(self.settings, provider)

    def map_chat_webhook(
        self, provider: str, payload: Mapping[str, object], correlation_id: str
    ) -> InboundMessage | None:
        mapper = self.chat_webhook_mapper(provider)
        return mapper.map_webhook(payload, correlation_id) if mapper else None

    def llm_provider(self) -> LlmProvider:
        return catalog.build_llm_provider(self.settings)

    def workflow_scheduler(self) -> WorkflowScheduler:
        return catalog.build_workflow_scheduler(self.settings)

    def workflow_worker(self) -> WorkflowWorker:
        return catalog.build_workflow_worker(self.settings)

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
        probes = catalog.build_readiness_probes(
            self.settings,
            self._executor,
            self._redis_client,
        )
        checks: dict[str, Callable[[], Awaitable[bool]]] = {
            name: probe.check for name, probe in probes.items()
        }
        results = await asyncio.gather(
            *(self._bounded_check(check) for check in checks.values()),
            return_exceptions=False,
        )
        return dict(zip(checks.keys(), results, strict=True))

    async def close(self) -> None:
        if self._postgres_executor is not None:
            await self._postgres_executor.close()
        if self._redis_provider is not None:
            await self._redis_provider.close()

    def _executor(self) -> PsycopgAsyncExecutor:
        if self._postgres_executor is None:
            self._postgres_executor = PsycopgAsyncExecutor(
                self.settings.database_url,
                min_size=self.settings.postgres_pool_min_size,
                max_size=self.settings.postgres_pool_max_size,
            )
        return self._postgres_executor

    def _redis_client(self) -> Redis:
        if self._redis_provider is None:
            self._redis_provider = RedisClientProvider(
                redis_url=self.settings.redis_url,
                max_connections=self.settings.redis_max_connections,
            )
        return self._redis_provider.client()

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


def _fernet_key(value: str) -> bytes:
    return value.encode("utf-8")
