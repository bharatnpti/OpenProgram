from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from redis.asyncio import Redis

from config.settings import Settings
from core.application.agents.tool_loop import ToolCallingAgent
from core.application.availability import AvailabilityService
from core.application.blocker_lifecycle import BlockerLifecycleService
from core.application.blocker_resolution import BlockerResolutionService
from core.application.cross_person_service import CrossPersonRequestService
from core.application.dead_letter_service import DeadLetterService
from core.application.directory_sync_service import DirectorySyncService
from core.application.reply_ingestion import ReplyDrainResult, ReplyIngestionService
from core.application.self_status_service import SelfStatusService
from core.application.status_collector import StatusCollector
from core.application.sync_services import (
    CalendarReadSyncService,
    IssueReadSyncService,
    VcsReadSyncService,
)
from core.application.writeback_service import WriteBackService
from core.domain.inbound import InboundChatEvent, conversation_key
from core.domain.messaging import InboundMessage
from core.domain.workflows import InboundSweeperResult
from core.ports.auth import (
    AuthCallbackResult,
    AuthCredentials,
    AuthLogoutResult,
    AuthProvider,
    AuthSession,
    CurrentPrincipal,
)
from core.ports.calendar import CalendarProvider
from core.ports.chat import ChatProvider, ChatWebhookMapper
from core.ports.directory import DirectoryProvider, DirectoryUserRepository
from core.ports.issue_tracker import IssueTracker
from core.ports.llm import LlmProvider
from core.ports.reply_processing import ReplyProcessingOutcome
from core.ports.repositories import (
    ConversationRepository,
    CrossPersonRequestRepository,
    DeadLetterRepository,
    GraphRepository,
    IdentityLinkRepository,
    InboundChatEventRepository,
    NarrativeBriefRepository,
    RollupRepository,
    StatusRepository,
    SyncCursorRepository,
    TimeSeriesRepository,
    VectorStore,
    WriteBackAuditRepository,
    WriteBackConfigRepository,
)
from core.ports.secrets import SecretStore
from core.ports.vcs import VcsProvider
from core.ports.workflows import WorkflowScheduler, WorkflowWorker
from infra.adapters import catalog
from infra.adapters.auth.dev import AuthProviderCurrentPrincipal, DevAuthProvider
from infra.adapters.auth.oidc import OidcBffAuthProvider, OidcBffService
from infra.adapters.auth.session import (
    AuthSessionStore,
    InMemoryAuthSessionStore,
    RedisAuthSessionStore,
)
from infra.adapters.chat.mock_slack import MockSlackStore, slack_event_payload
from infra.adapters.chat.slack_signing import verify_slack_signature
from infra.adapters.redis_client import RedisClientProvider
from infra.adapters.secrets.encrypted import (
    FernetSecretStore,
    InMemoryEncryptedSecretRecordStore,
    PostgresEncryptedSecretRecordStore,
)
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.persistence.postgres_cross_person import PostgresCrossPersonRequestRepository
from infra.persistence.postgres_directory import PostgresDirectoryUserRepository
from infra.persistence.postgres_graph import (
    PostgresGraphRepository,
    PostgresTimeSeriesRepository,
    PostgresVectorStore,
)
from infra.persistence.postgres_inbound import PostgresInboundChatEventRepository
from infra.persistence.postgres_status import (
    PostgresConversationRepository,
    PostgresDeadLetterRepository,
    PostgresNarrativeBriefRepository,
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
    _postgres_narrative_brief_repository: PostgresNarrativeBriefRepository | None = field(
        default=None,
        init=False,
    )
    _postgres_dead_letter_repository: PostgresDeadLetterRepository | None = field(
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
    _postgres_cross_person_request_repository: PostgresCrossPersonRequestRepository | None = field(
        default=None,
        init=False,
    )
    _postgres_inbound_chat_event_repository: PostgresInboundChatEventRepository | None = field(
        default=None,
        init=False,
    )
    _auth_provider: AuthProvider | None = field(default=None, init=False)
    _auth_session_store: AuthSessionStore | None = field(default=None, init=False)
    _oidc_bff_service: OidcBffService | None = field(default=None, init=False)

    def graph_repository(self) -> GraphRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_graph_repository is None:
            self._postgres_graph_repository = PostgresGraphRepository(self._executor())
        return self._postgres_graph_repository

    def identity_link_repository(self) -> IdentityLinkRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_graph_repository is None:
            self._postgres_graph_repository = PostgresGraphRepository(self._executor())
        return self._postgres_graph_repository

    def writeback_config_repository(self) -> WriteBackConfigRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_graph_repository is None:
            self._postgres_graph_repository = PostgresGraphRepository(self._executor())
        return self._postgres_graph_repository

    def writeback_audit_repository(self) -> WriteBackAuditRepository:
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

    def cross_person_request_repository(self) -> CrossPersonRequestRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_cross_person_request_repository is None:
            self._postgres_cross_person_request_repository = PostgresCrossPersonRequestRepository(
                self._executor()
            )
        return self._postgres_cross_person_request_repository

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

    def inbound_chat_event_repository(self) -> InboundChatEventRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_inbound_chat_event_repository is None:
            self._postgres_inbound_chat_event_repository = PostgresInboundChatEventRepository(
                self._executor()
            )
        return self._postgres_inbound_chat_event_repository

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

    def narrative_brief_repository(self) -> NarrativeBriefRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_narrative_brief_repository is None:
            self._postgres_narrative_brief_repository = PostgresNarrativeBriefRepository(
                self._executor()
            )
        return self._postgres_narrative_brief_repository

    def dead_letter_repository(self) -> DeadLetterRepository:
        if self.settings.runtime_mode == "memory":
            return self._memory_graph_store()
        if self._postgres_dead_letter_repository is None:
            self._postgres_dead_letter_repository = PostgresDeadLetterRepository(self._executor())
        return self._postgres_dead_letter_repository

    def dead_letter_service(self) -> DeadLetterService:
        return DeadLetterService(self.dead_letter_repository())

    def auth_provider(self) -> AuthProvider:
        if self._auth_provider is None:
            if self.settings.auth_provider == "dev":
                self._auth_provider = DevAuthProvider(
                    tenant_id=self.settings.tenant_id,
                    subject=self.settings.dev_principal_subject,
                    roles=self.settings.dev_roles,
                )
            else:
                self._auth_provider = OidcBffAuthProvider(self.auth_session_store())
        return self._auth_provider

    def current_principal(self, credentials: AuthCredentials) -> CurrentPrincipal:
        return AuthProviderCurrentPrincipal(self.auth_provider(), credentials)

    def auth_session_store(self) -> AuthSessionStore:
        if self._auth_session_store is None:
            if self.settings.runtime_mode == "memory":
                self._auth_session_store = InMemoryAuthSessionStore()
            else:
                self._auth_session_store = RedisAuthSessionStore(
                    self._redis_client(),
                    Fernet(_fernet_key(self.settings.secret_key)),
                )
        return self._auth_session_store

    def oidc_bff_service(self) -> OidcBffService:
        if self._oidc_bff_service is None:
            self._oidc_bff_service = OidcBffService(
                settings=self.settings,
                store=self.auth_session_store(),
            )
        return self._oidc_bff_service

    async def auth_session(self, session_id: str | None) -> AuthSession | None:
        if session_id is None:
            return None
        record = await self.auth_session_store().get_session(session_id)
        return record.session if record is not None else None

    async def begin_auth_login(self, return_url: str | None, prompt: str | None) -> str:
        return await self.oidc_bff_service().authorization_redirect_url(
            return_url=return_url,
            prompt=prompt,
        )

    async def complete_auth_callback(self, params: Mapping[str, str]) -> AuthCallbackResult:
        return await self.oidc_bff_service().handle_callback(params)

    async def logout_auth_session(self, session_id: str | None) -> AuthLogoutResult:
        if self.settings.auth_provider == "dev":
            return AuthLogoutResult(
                success=True,
                message="signed out",
                redirect_url=self.settings.frontend_logged_out_url(),
            )
        return await self.oidc_bff_service().logout(session_id)

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

    def chat_webhook_signature_valid(
        self,
        provider: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> bool:
        # Verify based on the *configured* chat provider, not the URL segment:
        # the mapper treats mock_slack as Slack, and unknown URL paths must not
        # bypass verification. Memory/fake/mock_slack flows stay credential-free.
        del provider
        if self.settings.runtime_mode != "container":
            return True
        if self.settings.chat_provider != "slack":
            return True
        return verify_slack_signature(
            self.settings.slack_signing_secret,
            headers.get("x-slack-request-timestamp"),
            headers.get("x-slack-signature"),
            body,
            self.settings.slack_signature_tolerance_seconds,
        )

    def fast_ack_enabled(self) -> bool:
        return self.settings.slack_fast_ack_enabled

    async def enqueue_inbound_chat_event(
        self,
        provider: str,
        payload: Mapping[str, object],
        correlation_id: str,
        received_at: datetime | None = None,
    ) -> ChatWebhookProcessResult:
        """Fast-ack intake: verify-mapped, dedup by event_id, then arm/drain.

        No LLM work happens here. In durable runtimes the coalesce workflow is
        armed and we return ``accepted``; in memory/fake runtimes there is no
        worker so we drain inline and return the concrete outcome.
        """
        message = self.map_chat_webhook(provider, payload, correlation_id)
        if message is None:
            return ChatWebhookProcessResult(status="ignored", message_id="unsupported-provider")
        if received_at is not None:
            message = replace(message, received_at=received_at)
        event = self._build_inbound_chat_event(provider, payload, message)
        inserted = await self.inbound_chat_event_repository().append(event)
        if not inserted:
            return ChatWebhookProcessResult(status="duplicate", message_id=message.message_id)
        if self._inbound_processing_inline():
            drained = await self.drain_inbound_conversation(
                message.tenant_id, event.conversation_key
            )
            return ChatWebhookProcessResult(
                status=drained.status,
                message_id=drained.message_id or message.message_id,
            )
        await self.workflow_scheduler().arm_reply_coalesce(
            event.conversation_key, message.tenant_id
        )
        return ChatWebhookProcessResult(status="accepted", message_id=message.message_id)

    def _build_inbound_chat_event(
        self,
        provider: str,
        payload: Mapping[str, object],
        message: InboundMessage,
    ) -> InboundChatEvent:
        return InboundChatEvent(
            tenant_id=message.tenant_id,
            provider=provider,
            event_id=_extract_event_id(payload, message),
            conversation_key=conversation_key(message.tenant_id, message.thread_id),
            chat_user_ref=message.user.external_id,
            chat_thread_ref=message.thread_id,
            message_ref=message.message_id,
            correlation_id=message.correlation_id,
            text=message.text,
            raw_payload=json.dumps(dict(payload), default=str),
            received_at=message.received_at,
        )

    def _inbound_processing_inline(self) -> bool:
        return self.settings.runtime_mode == "memory" or self.settings.workflow_provider == "fake"

    def reply_ingestion_service(self) -> ReplyIngestionService:
        return ReplyIngestionService(
            repository=self.inbound_chat_event_repository(),
            processor=_RegistryReplyProcessor(self),
        )

    async def drain_inbound_conversation(
        self, tenant_id: str, conversation_key: str
    ) -> ReplyDrainResult:
        return await self.reply_ingestion_service().drain_conversation(
            tenant_id=tenant_id,
            conversation_key=conversation_key,
        )

    async def sweep_inbound_events(
        self,
        tenant_id: str,
        grace_seconds: int,
        now: datetime | None = None,
    ) -> InboundSweeperResult:
        reference = now or datetime.now(tz=UTC)
        cutoff = reference - timedelta(seconds=grace_seconds)
        stuck = await self.inbound_chat_event_repository().list_stuck(tenant_id, cutoff)
        keys = sorted({event.conversation_key for event in stuck})
        # Record dead-letters for bursts whose durable retries are exhausted --
        # either they exceeded the hard time threshold or the max reply-processing
        # attempt count -- BEFORE draining, so operators still see them even if a
        # later drain wins. Re-sweeps upsert the same deterministic row.
        dead_lettered = await self._record_inbound_dead_letters(tenant_id, stuck, reference)
        for key in keys:
            # Drain directly for guaranteed progress rather than only re-arming a
            # possibly-completed coalesce workflow (the durable R3 backstop).
            await self.drain_inbound_conversation(tenant_id, key)
        return InboundSweeperResult(
            tenant_id=tenant_id,
            rearmed=len(keys),
            conversation_keys=keys,
            dead_lettered=dead_lettered,
        )

    async def _record_inbound_dead_letters(
        self,
        tenant_id: str,
        stuck: list[InboundChatEvent],
        reference: datetime,
    ) -> int:
        dead_letter_cutoff = reference - timedelta(
            seconds=self.settings.inbound_events_dead_letter_seconds
        )
        max_retries = self.settings.reply_processing_max_retries
        exhausted = [
            event
            for event in stuck
            if event.received_at < dead_letter_cutoff or event.attempts >= max_retries
        ]
        if not exhausted:
            return 0
        service = self.dead_letter_service()
        by_conversation: dict[str, list[InboundChatEvent]] = {}
        for event in exhausted:
            by_conversation.setdefault(event.conversation_key, []).append(event)
        for conversation, events in by_conversation.items():
            event_ids = tuple(event.id for event in events if event.id is not None)
            first_seen_at = min(event.received_at for event in events)
            attempts = max(event.attempts for event in events)
            await service.record_inbound(
                tenant_id=tenant_id,
                conversation_key=conversation,
                event_ids=event_ids,
                reason="inbound reply retries exhausted",
                attempts=attempts,
                first_seen_at=first_seen_at,
                now=reference,
            )
        return len(by_conversation)

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
        return await self.process_inbound_message(message, allow_reprocess=False)

    async def process_inbound_message(
        self,
        message: InboundMessage,
        *,
        allow_reprocess: bool,
    ) -> ChatWebhookProcessResult:
        cross_person_service = self.cross_person_request_service()
        cross_person_repository = self.cross_person_request_repository()
        notified_request = await cross_person_repository.get_by_notify_correlation(
            message.tenant_id,
            message.correlation_id,
        )
        if notified_request is not None:
            updated = await cross_person_service.handle_counterpart_reply(
                message,
                notified_request,
            )
            return ChatWebhookProcessResult(
                status=updated.status.value,
                message_id=message.message_id,
            )

        if message.thread_id:
            threaded_request = await cross_person_repository.get_by_notify_message_id(
                message.tenant_id,
                message.thread_id,
            )
            if threaded_request is not None:
                updated = await cross_person_service.handle_counterpart_reply(
                    message,
                    threaded_request,
                )
                return ChatWebhookProcessResult(
                    status=updated.status.value,
                    message_id=message.message_id,
                )

        collector = self.status_collector()
        resolved_correlation_id = await collector.resolve_reply_correlation(message)
        if resolved_correlation_id is None:
            open_request = await cross_person_service.open_request_for_counterpart_reply(message)
            if open_request is not None:
                updated = await cross_person_service.handle_counterpart_reply(message, open_request)
                return ChatWebhookProcessResult(
                    status=updated.status.value,
                    message_id=message.message_id,
                )
            return ChatWebhookProcessResult(status="ignored", message_id=message.message_id)

        checkin = await self.status_repository().checkin_by_correlation(
            message.tenant_id,
            resolved_correlation_id,
        )
        if checkin is not None and checkin.replied_at is not None:
            return ChatWebhookProcessResult(status="duplicate", message_id=message.message_id)

        outcome = await collector.handle_reply(
            replace(message, correlation_id=resolved_correlation_id),
            allow_reprocess=allow_reprocess,
        )
        if outcome.kind == "processed" and outcome.cross_person_requests:
            assert checkin is not None
            correlation = await self.status_repository().checkin_correlation_by_id(
                message.tenant_id,
                resolved_correlation_id,
            )
            await cross_person_service.record_from_checkin(
                tenant_id=message.tenant_id,
                requester_id=checkin.developer_id,
                requester_chat_ref=correlation.chat_user_ref if correlation is not None else None,
                source_correlation_id=resolved_correlation_id,
                resolutions=outcome.cross_person_requests,
                observed_at=message.received_at,
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

    def cross_person_request_service(self) -> CrossPersonRequestService:
        return CrossPersonRequestService(
            repository=self.cross_person_request_repository(),
            chat_provider=self.chat_provider(),
            directory_repository=self.directory_user_repository(),
            time_series_repository=self.time_series_repository(),
            llm_provider=self.llm_provider(),
            model=self.settings.litellm_model,
            auto_notify=self.settings.cross_person_auto_notify,
        )

    def availability_service(self) -> AvailabilityService:
        return AvailabilityService(self.calendar_provider())

    def self_status_service(self) -> SelfStatusService:
        return SelfStatusService(
            self.status_repository(),
            blocker_lifecycle=BlockerLifecycleService(
                self.status_repository(), self.graph_repository()
            ),
            blocker_resolution=BlockerResolutionService(
                self.graph_repository(), self.status_repository()
            ),
        )

    def write_back_service(self) -> WriteBackService:
        return WriteBackService(
            issue_tracker=self.issue_tracker(),
            audit_repository=self.writeback_audit_repository(),
            config_repository=self.writeback_config_repository(),
            status_repository=self.status_repository(),
            writeback_enabled_default=self.settings.jira_writeback_enabled,
        )

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
            directory_repository=self.directory_user_repository(),
            identity_link_repository=self.identity_link_repository(),
            write_back_service=self.write_back_service(),
            graph_repository=self.graph_repository(),
            model=self.settings.litellm_model,
            tool_agent=tool_agent,
            conversation_retention_days=self.settings.conversation_retention_days,
            checkin_max_clarifications=self.settings.checkin_max_clarifications,
            checkin_ack_enabled=self.settings.checkin_ack_enabled,
            tenant_default_timezone=self.settings.tenant_default_timezone,
            outbound_dm_max_chars=self.settings.outbound_dm_max_chars,
            recent_fact_lookback_days=self.settings.recent_fact_lookback_days,
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
            workflow_backlog_count=self._open_dead_letter_count,
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

    async def _open_dead_letter_count(self) -> int:
        return await self.dead_letter_repository().count_open_dead_letters(self.settings.tenant_id)

    async def _bounded_check(self, check: Callable[[], Awaitable[bool]]) -> bool:
        try:
            return await asyncio.wait_for(check(), timeout=3.0)
        except Exception:
            return False


def _fernet_key(value: str) -> bytes:
    return value.encode("utf-8")


def _extract_event_id(payload: Mapping[str, object], message: InboundMessage) -> str:
    """Prefer Slack's envelope-level event_id (stable across retries).

    Providers without one (fake/mock_slack) fall back to the message ts, which is
    stable per message and keeps redelivery dedup working credential-free.
    """
    top_level = payload.get("event_id")
    if isinstance(top_level, str) and top_level:
        return top_level
    return message.message_id


@dataclass(frozen=True)
class _RegistryReplyProcessor:
    """Adapts the registry's reply routing to the ReplyProcessor port."""

    registry: ServiceRegistry

    async def process_reply(self, message: InboundMessage) -> ReplyProcessingOutcome:
        result = await self.registry.process_inbound_message(message, allow_reprocess=True)
        return ReplyProcessingOutcome(status=result.status, message_id=result.message_id)
