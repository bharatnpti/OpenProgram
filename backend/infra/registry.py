from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime

from cryptography.fernet import Fernet
from redis.asyncio import Redis

from config.settings import Settings
from core.application.agents.tool_loop import ToolCallingAgent
from core.application.availability import AvailabilityService
from core.application.directory_sync_service import DirectorySyncService
from core.application.status_collector import StatusCollector
from core.application.sync_services import (
    CalendarReadSyncService,
    IssueReadSyncService,
    VcsReadSyncService,
)
from core.domain.messaging import InboundMessage
from core.ports.auth import AuthProvider, CurrentPrincipal
from core.ports.calendar import CalendarProvider
from core.ports.chat import ChatProvider, ChatWebhookMapper
from core.ports.directory import DirectoryProvider, DirectoryUserRepository
from core.ports.issue_tracker import IssueTracker
from core.ports.llm import LlmProvider
from core.ports.repositories import (
    ConversationRepository,
    GraphRepository,
    RollupRepository,
    StatusRepository,
    SyncCursorRepository,
    TimeSeriesRepository,
    VectorStore,
)
from core.ports.secrets import SecretStore
from core.ports.vcs import VcsProvider
from core.ports.workflows import WorkflowScheduler, WorkflowWorker
from infra.adapters import catalog
from infra.adapters.auth.dev import DevAuthProvider, DevCurrentPrincipal
from infra.adapters.chat.mock_slack import MockSlackStore, slack_event_payload
from infra.adapters.redis_client import RedisClientProvider
from infra.adapters.secrets.encrypted import (
    FernetSecretStore,
    InMemoryEncryptedSecretRecordStore,
    PostgresEncryptedSecretRecordStore,
)
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.persistence.postgres_directory import PostgresDirectoryUserRepository
from infra.persistence.postgres_graph import (
    PostgresGraphRepository,
    PostgresTimeSeriesRepository,
    PostgresVectorStore,
)
from infra.persistence.postgres_status import (
    PostgresConversationRepository,
    PostgresRollupRepository,
    PostgresStatusRepository,
    PostgresSyncCursorRepository,
)
from infra.persistence.psycopg_executor import PsycopgAsyncExecutor

_CHAT_SIMULATOR_PROVIDER = "mock_slack"


@dataclass(frozen=True)
class ChatWebhookProcessResult:
    status: str
    message_id: str


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
    _postgres_status_repository: PostgresStatusRepository | None = field(default=None, init=False)
    _postgres_conversation_repository: PostgresConversationRepository | None = field(
        default=None,
        init=False,
    )
    _postgres_rollup_repository: PostgresRollupRepository | None = field(default=None, init=False)
    _postgres_sync_cursor_repository: PostgresSyncCursorRepository | None = field(
        default=None,
        init=False,
    )
    _redis_provider: RedisClientProvider | None = field(default=None, init=False)
    _issue_tracker: IssueTracker | None = field(default=None, init=False)
    _vcs_provider: VcsProvider | None = field(default=None, init=False)
    _calendar_provider: CalendarProvider | None = field(default=None, init=False)
    _directory_provider: DirectoryProvider | None = field(default=None, init=False)
    _mock_slack_store: MockSlackStore | None = field(default=None, init=False)
    _postgres_directory_user_repository: PostgresDirectoryUserRepository | None = field(
        default=None,
        init=False,
    )

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

    def status_repository(self) -> StatusRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_status_repository is None:
            self._postgres_status_repository = PostgresStatusRepository(self._executor())
        return self._postgres_status_repository

    def conversation_repository(self) -> ConversationRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_conversation_repository is None:
            self._postgres_conversation_repository = PostgresConversationRepository(
                self._executor()
            )
        return self._postgres_conversation_repository

    def rollup_repository(self) -> RollupRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_rollup_repository is None:
            self._postgres_rollup_repository = PostgresRollupRepository(self._executor())
        return self._postgres_rollup_repository

    def sync_cursor_repository(self) -> SyncCursorRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_sync_cursor_repository is None:
            self._postgres_sync_cursor_repository = PostgresSyncCursorRepository(self._executor())
        return self._postgres_sync_cursor_repository

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
        mock_store = (
            self._chat_simulator_store()
            if self.settings.chat_provider == _CHAT_SIMULATOR_PROVIDER
            else None
        )
        return catalog.build_chat_provider(self.settings, redis_client, mock_store)

    def chat_webhook_mapper(self, provider: str) -> ChatWebhookMapper | None:
        return catalog.build_chat_webhook_mapper(self.settings, provider)

    def map_chat_webhook(
        self, provider: str, payload: Mapping[str, object], correlation_id: str
    ) -> InboundMessage | None:
        mapper = self.chat_webhook_mapper(provider)
        return mapper.map_webhook(payload, correlation_id) if mapper else None

    async def process_chat_webhook(
        self,
        provider: str,
        payload: Mapping[str, object],
        correlation_id: str,
        received_at: datetime | None = None,
    ) -> ChatWebhookProcessResult:
        message = self.map_chat_webhook(provider, payload, correlation_id)
        if message is None:
            return ChatWebhookProcessResult(status="ignored", message_id="unsupported-provider")
        if received_at is not None:
            message = replace(message, received_at=received_at)
        collector = self.status_collector()
        resolved_correlation_id = await collector.resolve_reply_correlation(message)
        if resolved_correlation_id is None:
            return ChatWebhookProcessResult(status="ignored", message_id=message.message_id)

        checkin = await self.status_repository().checkin_by_correlation(
            message.tenant_id,
            resolved_correlation_id,
        )
        if checkin is not None and checkin.replied_at is not None:
            return ChatWebhookProcessResult(status="duplicate", message_id=message.message_id)

        outcome = await collector.handle_reply(
            replace(message, correlation_id=resolved_correlation_id)
        )
        return ChatWebhookProcessResult(status=outcome.kind, message_id=message.message_id)

    def chat_simulator_available(self) -> bool:
        return self.settings.chat_provider == _CHAT_SIMULATOR_PROVIDER

    async def chat_simulator_status(self) -> Mapping[str, object]:
        messages = await self._chat_simulator_store().list_messages(self.settings.tenant_id)
        return {
            "enabled": self.settings.chat_simulator_enabled,
            "tenant_id": self.settings.tenant_id,
            "provider": self.settings.chat_provider,
            "message_count": len(messages),
        }

    async def chat_simulator_messages(self) -> list[Mapping[str, object]]:
        messages = await self._chat_simulator_store().list_messages(self.settings.tenant_id)
        return [message.to_dict() for message in messages]

    async def reset_chat_simulator(self) -> None:
        await self._chat_simulator_store().reset(self.settings.tenant_id)

    async def inject_chat_simulator_reply(
        self,
        *,
        message_id: str,
        text: str,
        received_at: datetime | None,
        correlation_id: str,
    ) -> Mapping[str, object]:
        reply = await self._chat_simulator_store().record_user_reply(
            tenant_id=self.settings.tenant_id,
            reply_to_message_id=message_id,
            text=text,
            created_at=received_at,
        )
        result = await self.process_chat_webhook(
            _CHAT_SIMULATOR_PROVIDER,
            slack_event_payload(reply),
            correlation_id,
            reply.created_at,
        )
        return {
            "message_id": reply.message_id,
            "status": result.status,
            "processed_message_id": result.message_id,
        }

    def llm_provider(self) -> LlmProvider:
        return catalog.build_llm_provider(self.settings)

    def issue_tracker(self) -> IssueTracker:
        if self._issue_tracker is None:
            self._issue_tracker = catalog.build_issue_tracker(
                self.settings,
                self.secret_store(),
            )
        return self._issue_tracker

    def vcs_provider(self) -> VcsProvider:
        if self._vcs_provider is None:
            self._vcs_provider = catalog.build_vcs_provider(
                self.settings,
                self.secret_store(),
            )
        return self._vcs_provider

    def calendar_provider(self) -> CalendarProvider:
        if self._calendar_provider is None:
            self._calendar_provider = catalog.build_calendar_provider(
                self.settings,
                self.secret_store(),
            )
        return self._calendar_provider

    def directory_provider(self) -> DirectoryProvider:
        if self._directory_provider is None:
            self._directory_provider = catalog.build_directory_provider(self.settings)
        return self._directory_provider

    def directory_user_repository(self) -> DirectoryUserRepository:
        if self.settings.runtime_mode == "memory":
            return InMemoryDirectoryUserRepository(self._memory_graph_store())
        if self._postgres_directory_user_repository is None:
            self._postgres_directory_user_repository = PostgresDirectoryUserRepository(
                self._executor()
            )
        return self._postgres_directory_user_repository

    def issue_read_sync_service(self) -> IssueReadSyncService:
        return IssueReadSyncService(
            issue_tracker=self.issue_tracker(),
            graph_repository=self.graph_repository(),
            time_series_repository=self.time_series_repository(),
            cursor_repository=self.sync_cursor_repository(),
        )

    def vcs_read_sync_service(self) -> VcsReadSyncService:
        return VcsReadSyncService(
            vcs_provider=self.vcs_provider(),
            graph_repository=self.graph_repository(),
            time_series_repository=self.time_series_repository(),
            cursor_repository=self.sync_cursor_repository(),
        )

    def calendar_read_sync_service(self) -> CalendarReadSyncService:
        return CalendarReadSyncService(
            calendar_provider=self.calendar_provider(),
            time_series_repository=self.time_series_repository(),
            cursor_repository=self.sync_cursor_repository(),
        )

    def directory_sync_service(self) -> DirectorySyncService:
        return DirectorySyncService(
            provider=self.directory_provider(),
            repository=self.directory_user_repository(),
        )

    def availability_service(self) -> AvailabilityService:
        return AvailabilityService(self.calendar_provider())

    def status_collector(self) -> StatusCollector:
        llm_provider = self.llm_provider()
        tool_agent = ToolCallingAgent(
            llm_provider=llm_provider,
            max_tool_iterations=self.settings.llm_max_tool_iterations,
        )
        return StatusCollector(
            issue_tracker=self.issue_tracker(),
            chat_provider=self.chat_provider(),
            llm_provider=llm_provider,
            status_repository=self.status_repository(),
            conversation_repository=self.conversation_repository(),
            time_series_repository=self.time_series_repository(),
            model=self.settings.litellm_model,
            tool_agent=tool_agent,
            conversation_retention_days=self.settings.conversation_retention_days,
            checkin_max_clarifications=self.settings.checkin_max_clarifications,
            tenant_default_timezone=self.settings.tenant_default_timezone,
        )

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

    def _chat_simulator_store(self) -> MockSlackStore:
        if self._mock_slack_store is None:
            redis_client = None if self.settings.runtime_mode == "memory" else self._redis_client()
            self._mock_slack_store = catalog.build_mock_slack_store(self.settings, redis_client)
        return self._mock_slack_store

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
