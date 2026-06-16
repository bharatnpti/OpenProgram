from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from cryptography.fernet import Fernet

from config.settings import Settings
from core.domain.messaging import InboundMessage
from core.ports.auth import AuthProvider
from core.ports.chat import ChatProvider
from core.ports.llm import LlmProvider
from core.ports.repositories import GraphRepository, TimeSeriesRepository, VectorStore
from core.ports.secrets import SecretStore
from infra.adapters.auth.dev import DevAuthProvider
from infra.adapters.chat.rate_limit import InMemoryRateLimiter
from infra.adapters.chat.slack import DisabledSlackHttpClient, SlackChatAdapter
from infra.adapters.llm.litellm_provider import LiteLlmProvider, NoopTraceSink
from infra.adapters.secrets.encrypted import (
    FernetSecretStore,
    InMemoryEncryptedSecretRecordStore,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore


@dataclass
class ServiceRegistry:
    settings: Settings
    graph_store: InMemoryGraphStore = field(default_factory=InMemoryGraphStore)
    secret_records: InMemoryEncryptedSecretRecordStore = field(
        default_factory=InMemoryEncryptedSecretRecordStore
    )

    def graph_repository(self) -> GraphRepository:
        return self.graph_store

    def time_series_repository(self) -> TimeSeriesRepository:
        return self.graph_store

    def vector_store(self) -> VectorStore:
        return self.graph_store

    def auth_provider(self) -> AuthProvider:
        return DevAuthProvider(
            tenant_id=self.settings.tenant_id,
            subject=self.settings.dev_principal_subject,
            roles=self.settings.dev_roles,
        )

    def chat_provider(self) -> ChatProvider:
        return SlackChatAdapter(
            tenant_id=self.settings.tenant_id,
            http_client=DisabledSlackHttpClient(),
            rate_limiter=InMemoryRateLimiter(),
        )

    def map_chat_webhook(
        self, payload: Mapping[str, object], correlation_id: str
    ) -> InboundMessage | None:
        chat_provider = self.chat_provider()
        if isinstance(chat_provider, SlackChatAdapter):
            return chat_provider.map_webhook(payload, correlation_id)
        return None

    def llm_provider(self) -> LlmProvider:
        return LiteLlmProvider(
            base_url=self.settings.litellm_base_url,
            trace_sink=NoopTraceSink(),
        )

    def secret_store(self) -> SecretStore:
        key = _fernet_key(self.settings.secret_key)
        return FernetSecretStore(
            fernet=Fernet(key),
            record_store=self.secret_records,
        )


def _fernet_key(value: str) -> bytes:
    return value.encode("utf-8")
