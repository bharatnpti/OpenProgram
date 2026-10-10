from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

from cryptography.fernet import Fernet
from redis.asyncio import Redis

from config.settings import Settings
from core.application.agents.tool_loop import ToolCallingAgent
from core.application.availability import AvailabilityService
from core.application.blocker_lifecycle import BlockerLifecycleService
from core.application.blocker_resolution import BlockerResolutionService
from core.application.blocker_settlement import BlockerSettlement
from core.application.connection_service import ConnectionService, StoredConnections
from core.application.cross_person_service import CrossPersonRequestService
from core.application.day_report_builder import DayReportBuilder
from core.application.day_report_service import DayReportService
from core.application.dead_letter_service import DeadLetterService
from core.application.delivery_service import DeliveryService
from core.application.directory_sync_service import DirectorySyncService
from core.application.escalation_matrix_service import EscalationMatrixService
from core.application.forecast_service import ForecastService
from core.application.gate_extraction import ModelItemFinder
from core.application.gate_service import GateService
from core.application.jira_writes_service import JiraWritesService
from core.application.persona_views import PersonaViewService, ProviderNames
from core.application.release_readiness_service import JiraData, ReleaseReadinessService
from core.application.reply_ingestion import ReplyDrainResult, ReplyIngestionService
from core.application.risk_service import RiskService
from core.application.rollup_service import PersonRollups, RollupService
from core.application.self_status_service import SelfStatusService
from core.application.status_collector import DEFAULT_ISSUE_TRACKER_NAME, StatusCollector
from core.application.sync_recording import provider_start_failure_message
from core.application.sync_services import (
    CalendarReadSyncService,
    IssueReadSyncService,
    VcsReadSyncService,
)
from core.application.sync_status_service import SyncStatusService
from core.application.task_update_service import TaskUpdateService
from core.application.writeback_service import WriteBackService
from core.domain.errors import GraphNotFound, ProviderUnavailable
from core.domain.graph import NodeKind
from core.domain.inbound import InboundChatEvent, conversation_key
from core.domain.messaging import InboundMessage
from core.domain.risk import RiskProviderConfig
from core.domain.status import resolve_timezone
from core.domain.sync_status import SyncHealth, SyncSource, SyncStatusConfig
from core.domain.workflows import InboundSweeperResult
from core.ports.auth import (
    AuthCallbackResult,
    AuthCredentials,
    AuthLogoutResult,
    AuthProvider,
    AuthSession,
    CurrentPrincipal,
)
from core.ports.branding import TenantLogoRepository
from core.ports.calendar import CalendarProvider
from core.ports.chat import ChatProvider, ChatWebhookMapper
from core.ports.connections import ConnectionRepository, ConnectorCatalog
from core.ports.delivery import DeliverySettingsRepository, RequirementsSnapshotRepository
from core.ports.directory import DirectoryProvider, DirectoryUserRepository
from core.ports.escalation_matrix import EscalationMatrixRepository
from core.ports.forecast import (
    CommitmentRepository,
    ForecastSettingsRepository,
    ReleaseRepository,
)
from core.ports.gates import (
    GateItemRepository,
    GateTemplateRepository,
    IssueScanRepository,
    QuestionRepository,
)
from core.ports.investigation import InvestigationEngine
from core.ports.issue_tracker import IssueTracker
from core.ports.llm import LlmProvider
from core.ports.readiness import ReadinessProbe, ReadinessReport, ReportingReadinessProbe
from core.ports.release_readiness import ReleaseReadinessRepository
from core.ports.reply_processing import ReplyProcessingOutcome
from core.ports.reports import DayReportRepository
from core.ports.repositories import (
    ConversationRepository,
    CrossPersonRequestRepository,
    DeadLetterRepository,
    GraphRepository,
    IdentityLinkRepository,
    InboundChatEventRepository,
    JiraWritesRepository,
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
from core.ports.workflows import RollupRefresher, WorkflowScheduler, WorkflowWorker
from infra.adapters import catalog
from infra.adapters.auth.dev import AuthProviderCurrentPrincipal, DevAuthProvider
from infra.adapters.auth.oidc import OidcBffAuthProvider, OidcBffService
from infra.adapters.auth.session import (
    AuthSessionStore,
    InMemoryAuthSessionStore,
    RedisAuthSessionStore,
)
from infra.adapters.chat.mock_slack import MockSlackStore, slack_event_payload
from infra.adapters.chat.slack_connection import SIGNING_SECRET
from infra.adapters.chat.slack_signing import verify_slack_signature
from infra.adapters.chat.slack_socket import SlackSocketModeListener
from infra.adapters.connections.resolver import CachedConnectionResolver
from infra.adapters.connections.routing import enabled_connector
from infra.adapters.connections.specs import SettingsConnectorCatalog
from infra.adapters.connections.testers import HttpConnectionTester
from infra.adapters.llm.readiness import LlmReadinessHistory
from infra.adapters.redis_client import RedisClientProvider
from infra.adapters.reports.sender import ConnectionReportSender
from infra.adapters.secrets.encrypted import (
    FernetSecretStore,
    InMemoryEncryptedSecretRecordStore,
    PostgresEncryptedSecretRecordStore,
)
from infra.persistence.in_memory_branding import InMemoryTenantLogoRepository
from infra.persistence.in_memory_connections import InMemoryConnectionRepository
from infra.persistence.in_memory_delivery import (
    InMemoryDeliverySettingsRepository,
    InMemoryRequirementsSnapshotRepository,
)
from infra.persistence.in_memory_escalation import InMemoryEscalationMatrixRepository
from infra.persistence.in_memory_forecast import (
    InMemoryCommitmentRepository,
    InMemoryForecastSettingsRepository,
    InMemoryReleaseRepository,
)
from infra.persistence.in_memory_gates import (
    InMemoryGateItemRepository,
    InMemoryGateTemplateRepository,
    InMemoryIssueScanRepository,
    InMemoryQuestionRepository,
)
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.persistence.in_memory_jira_writes import InMemoryJiraWritesRepository
from infra.persistence.in_memory_readiness import InMemoryReleaseReadinessRepository
from infra.persistence.in_memory_reports import InMemoryDayReportRepository
from infra.persistence.postgres_branding import PostgresTenantLogoRepository
from infra.persistence.postgres_connections import PostgresConnectionRepository
from infra.persistence.postgres_cross_person import PostgresCrossPersonRequestRepository
from infra.persistence.postgres_delivery import (
    PostgresDeliverySettingsRepository,
    PostgresForecastSettingsRepository,
    PostgresRequirementsSnapshotRepository,
)
from infra.persistence.postgres_directory import PostgresDirectoryUserRepository
from infra.persistence.postgres_escalation import PostgresEscalationMatrixRepository
from infra.persistence.postgres_forecast import (
    PostgresCommitmentRepository,
    PostgresReleaseRepository,
)
from infra.persistence.postgres_gates import (
    PostgresGateItemRepository,
    PostgresGateTemplateRepository,
    PostgresIssueScanRepository,
    PostgresQuestionRepository,
)
from infra.persistence.postgres_graph import (
    PostgresGraphRepository,
    PostgresTimeSeriesRepository,
    PostgresVectorStore,
)
from infra.persistence.postgres_inbound import PostgresInboundChatEventRepository
from infra.persistence.postgres_jira_writes import PostgresJiraWritesRepository
from infra.persistence.postgres_readiness import PostgresReleaseReadinessRepository
from infra.persistence.postgres_reports import PostgresDayReportRepository
from infra.persistence.postgres_status import (
    PostgresConversationRepository,
    PostgresDeadLetterRepository,
    PostgresNarrativeBriefRepository,
    PostgresRollupRepository,
    PostgresStatusRepository,
    PostgresSyncCursorRepository,
)
from infra.persistence.psycopg_executor import (
    PoolConfig,
    PsycopgAsyncExecutor,
    close_shared_executors,
    shared_executor,
)
from infra.workflows.runtime_sync import legacy_issue_dispatches, legacy_vcs_dispatches

_CHAT_SIMULATOR_PROVIDER = "mock_slack"
# Upper bound on any one readiness probe, so a hung dependency cannot stall /ready.
_READINESS_TIMEOUT_SECONDS = 3.0
# Outbound purposes that put a question to the developer and so leave the
# check-in open for a reply. Everything else the bot sends (acks, consent
# prompts, escalation notices) does not.
_CHAT_QUESTION_PURPOSES = frozenset(
    {
        "status_checkin",
        "status_clarification",
        "status_nudge",
    }
)


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
    _postgres_tenant_logo_repository: PostgresTenantLogoRepository | None = field(
        default=None,
        init=False,
    )
    _memory_tenant_logo_repository: InMemoryTenantLogoRepository | None = field(
        default=None,
        init=False,
    )
    _connection_repository: ConnectionRepository | None = field(default=None, init=False)
    _delivery_settings_repository: DeliverySettingsRepository | None = field(
        default=None, init=False
    )
    _requirements_snapshot_repository: RequirementsSnapshotRepository | None = field(
        default=None, init=False
    )
    _day_report_repository: DayReportRepository | None = field(default=None, init=False)
    _escalation_matrix_repository: EscalationMatrixRepository | None = field(
        default=None, init=False
    )
    _commitment_repository: CommitmentRepository | None = field(default=None, init=False)
    _release_repository: ReleaseRepository | None = field(default=None, init=False)
    _forecast_settings_repository: ForecastSettingsRepository | None = field(
        default=None, init=False
    )
    _gate_repositories: (
        tuple[GateTemplateRepository, GateItemRepository, QuestionRepository, IssueScanRepository]
        | None
    ) = field(default=None, init=False)
    _release_readiness_repository: ReleaseReadinessRepository | None = field(
        default=None, init=False
    )
    _jira_writes_repository: JiraWritesRepository | None = field(default=None, init=False)
    _connection_resolver: CachedConnectionResolver | None = field(default=None, init=False)
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
    # Outlives the probes built for each /ready call, so one missed LLM probe
    # right after a success is not reported as an outage.
    _llm_readiness_history: LlmReadinessHistory = field(
        default_factory=LlmReadinessHistory, init=False
    )

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

    def tenant_logo_repository(self) -> TenantLogoRepository:
        if self.settings.runtime_mode == "memory":
            if self._memory_tenant_logo_repository is None:
                self._memory_tenant_logo_repository = InMemoryTenantLogoRepository()
            return self._memory_tenant_logo_repository
        if self._postgres_tenant_logo_repository is None:
            self._postgres_tenant_logo_repository = PostgresTenantLogoRepository(self._executor())
        return self._postgres_tenant_logo_repository

    def connection_repository(self) -> ConnectionRepository:
        if self._connection_repository is None:
            self._connection_repository = (
                InMemoryConnectionRepository()
                if self.settings.runtime_mode == "memory"
                else PostgresConnectionRepository(self._executor())
            )
        return self._connection_repository

    def delivery_settings_repository(self) -> DeliverySettingsRepository:
        if self._delivery_settings_repository is None:
            self._delivery_settings_repository = (
                InMemoryDeliverySettingsRepository()
                if self.settings.runtime_mode == "memory"
                else PostgresDeliverySettingsRepository(self._executor())
            )
        return self._delivery_settings_repository

    def requirements_snapshot_repository(self) -> RequirementsSnapshotRepository:
        if self._requirements_snapshot_repository is None:
            self._requirements_snapshot_repository = (
                InMemoryRequirementsSnapshotRepository()
                if self.settings.runtime_mode == "memory"
                else PostgresRequirementsSnapshotRepository(self._executor())
            )
        return self._requirements_snapshot_repository

    def delivery_service(self) -> DeliveryService:
        return DeliveryService(
            graph_repository=self.graph_repository(),
            settings_repository=self.delivery_settings_repository(),
            snapshot_repository=self.requirements_snapshot_repository(),
            release_repository=self.release_repository(),
            today=self._tenant_today,
        )

    def commitment_repository(self) -> CommitmentRepository:
        if self._commitment_repository is None:
            self._commitment_repository = (
                InMemoryCommitmentRepository()
                if self.settings.runtime_mode == "memory"
                else PostgresCommitmentRepository(self._executor())
            )
        return self._commitment_repository

    def release_repository(self) -> ReleaseRepository:
        if self._release_repository is None:
            self._release_repository = (
                InMemoryReleaseRepository()
                if self.settings.runtime_mode == "memory"
                else PostgresReleaseRepository(self._executor())
            )
        return self._release_repository

    def forecast_settings_repository(self) -> ForecastSettingsRepository:
        if self._forecast_settings_repository is None:
            self._forecast_settings_repository = (
                InMemoryForecastSettingsRepository()
                if self.settings.runtime_mode == "memory"
                else PostgresForecastSettingsRepository(self._executor())
            )
        return self._forecast_settings_repository

    def forecast_service(self) -> ForecastService:
        return ForecastService(
            graph_repository=self.graph_repository(),
            delivery_service=self.delivery_service(),
            commitment_repository=self.commitment_repository(),
            release_repository=self.release_repository(),
            time_series_repository=self.time_series_repository(),
            settings_repository=self.forecast_settings_repository(),
            default_min_sample_days=self.settings.forecast_min_history_days,
            today=self._tenant_today,
        )

    def gate_repositories(
        self,
    ) -> tuple[GateTemplateRepository, GateItemRepository, QuestionRepository, IssueScanRepository]:
        if self._gate_repositories is None:
            if self.settings.runtime_mode == "memory":
                self._gate_repositories = (
                    InMemoryGateTemplateRepository(),
                    InMemoryGateItemRepository(),
                    InMemoryQuestionRepository(),
                    InMemoryIssueScanRepository(),
                )
            else:
                executor = self._executor()
                self._gate_repositories = (
                    PostgresGateTemplateRepository(executor),
                    PostgresGateItemRepository(executor),
                    PostgresQuestionRepository(executor),
                    PostgresIssueScanRepository(executor),
                )
        return self._gate_repositories

    def gate_service(self) -> GateService:
        templates, items, questions, scans = self.gate_repositories()
        finder = (
            ModelItemFinder(self.llm_provider(), model=self.settings.default_llm_model)
            if self.settings.gate_text_model_enabled and self.settings.llm_provider != "fake"
            else None
        )
        return GateService(
            template_repository=templates,
            item_repository=items,
            question_repository=questions,
            scan_repository=scans,
            delivery_service=self.delivery_service(),
            issue_tracker=self.issue_tracker(),
            model_finder=finder,
            graph_repository=self.graph_repository(),
        )

    def release_readiness_repository(self) -> ReleaseReadinessRepository:
        if self._release_readiness_repository is None:
            self._release_readiness_repository = (
                InMemoryReleaseReadinessRepository()
                if self.settings.runtime_mode == "memory"
                else PostgresReleaseReadinessRepository(self._executor())
            )
        return self._release_readiness_repository

    def jira_writes_repository(self) -> JiraWritesRepository:
        if self._jira_writes_repository is None:
            self._jira_writes_repository = (
                InMemoryJiraWritesRepository()
                if self.settings.runtime_mode == "memory"
                else PostgresJiraWritesRepository(self._executor())
            )
        return self._jira_writes_repository

    def jira_writes_service(self) -> JiraWritesService:
        """The tenant's Jira write switches: the master, one per kind, and the projects."""
        readiness = self.release_readiness_repository()

        async def legacy_readiness_create(tenant_id: str) -> bool | None:
            stored = await readiness.get_settings(tenant_id)
            return stored.create_in_jira if stored is not None else None

        return JiraWritesService(
            config_repository=self.writeback_config_repository(),
            repository=self.jira_writes_repository(),
            env_master=self.settings.jira_writeback_enabled,
            env_master_set="jira_writeback_enabled" in self.settings.model_fields_set,
            legacy_readiness_create=legacy_readiness_create,
        )

    def release_readiness_service(self) -> ReleaseReadinessService:
        writeback = self.write_back_service()
        return ReleaseReadinessService(
            repository=self.release_readiness_repository(),
            graph_repository=self.graph_repository(),
            delivery_service=self.delivery_service(),
            forecast_service=self.forecast_service(),
            issue_tracker=self.issue_tracker(),
            writeback_gate=writeback.system_gate_open,
            jira_health=self._readiness_jira_health,
            pod_task_ids=self._pod_task_ids,
            jira_writes=self.jira_writes_service(),
            identity_link_repository=self.identity_link_repository(),
            time_series_repository=self.time_series_repository(),
            console_base_url=self.settings.public_console_url,
            today=self._tenant_today,
        )

    async def _readiness_jira_health(self, tenant_id: str) -> JiraData:
        """How fresh the synced Jira data is: stale while the issue sync fails or lags."""
        try:
            report = await self.sync_status_service().status(tenant_id)
        except Exception:  # noqa: BLE001 - a status read must never fail a readiness run
            return JiraData()
        source = next(
            (item for item in report.sources if item.source is SyncSource.ISSUE_TRACKER), None
        )
        if source is None:
            return JiraData()
        return JiraData(
            stale=source.health in {SyncHealth.FAILING, SyncHealth.STALE},
            as_of=source.last_synced_at,
        )

    async def _pod_task_ids(self, tenant_id: str, pod_id: str, day: date) -> list[str]:
        """The tasks a pod owns within its remit, by the pod panel's own rule."""
        views = PersonaViewService(
            graph_repository=self.graph_repository(),
            status_repository=self.status_repository(),
            rollup_repository=self.rollup_repository(),
            time_series_repository=self.time_series_repository(),
        )
        try:
            return [task.id for task in (await views.pod_tasks(tenant_id, pod_id, day)).tasks]
        except GraphNotFound:
            return []

    def _tenant_today(self) -> date:
        zone = resolve_timezone(None, self.settings.tenant_default_timezone)
        return datetime.now(tz=zone).date()

    def day_report_repository(self) -> DayReportRepository:
        if self._day_report_repository is None:
            self._day_report_repository = (
                InMemoryDayReportRepository()
                if self.settings.runtime_mode == "memory"
                else PostgresDayReportRepository(self._executor())
            )
        return self._day_report_repository

    def risk_service(self) -> RiskService:
        settings = self.settings
        return RiskService(
            graph_repository=self.graph_repository(),
            time_series_repository=self.time_series_repository(),
            status_repository=self.status_repository(),
            blocker_resolution=BlockerResolutionService(
                self.graph_repository(), self.status_repository()
            ),
            rollup_repository=self.rollup_repository(),
            provider_config=RiskProviderConfig(
                jira_base_url=settings.jira_base_url,
                github_base_url=settings.github_base_url,
                default_no_pr_days=settings.risk_default_no_pr_days,
                default_pr_age_days=settings.risk_default_pr_age_days,
                default_stale_days=settings.risk_default_stale_days,
                default_no_activity_days=settings.drift_no_activity_days,
            ),
        )

    def escalation_matrix_repository(self) -> EscalationMatrixRepository:
        if self._escalation_matrix_repository is None:
            self._escalation_matrix_repository = (
                InMemoryEscalationMatrixRepository()
                if self.settings.runtime_mode == "memory"
                else PostgresEscalationMatrixRepository(self._executor())
            )
        return self._escalation_matrix_repository

    def escalation_matrix_service(self) -> EscalationMatrixService:
        return EscalationMatrixService(
            repository=self.escalation_matrix_repository(),
            graph_repository=self.graph_repository(),
        )

    def day_report_builder(self) -> DayReportBuilder:
        return DayReportBuilder(
            graph_repository=self.graph_repository(),
            delivery_service=self.delivery_service(),
            rollup_repository=self.rollup_repository(),
            blocker_resolution=BlockerResolutionService(
                self.graph_repository(), self.status_repository()
            ),
            risk_service=self.risk_service(),
            cross_person_repository=self.cross_person_request_repository(),
            forecast_service=self.forecast_service(),
            gate_service=self.gate_service(),
            escalation_service=self.escalation_matrix_service(),
            console_base_url=self.settings.public_console_url,
            readiness_service=self.release_readiness_service(),
        )

    def day_report_service(self) -> DayReportService:
        return DayReportService(
            repository=self.day_report_repository(),
            sender=self._day_report_sender(),
            builder=self.day_report_builder(),
            graph_repository=self.graph_repository(),
        )

    def _day_report_sender(self) -> ConnectionReportSender:
        connections = self._adapter_connections()
        channel_poster = None
        if self.settings.chat_provider == "slack" and connections is not None:
            channel_poster = catalog.slack_channel_poster(self.settings, connections)
        return ConnectionReportSender(
            connections=connections,
            chat_provider=self.chat_provider,
            channel_poster=channel_poster,
            member_chat_id=self._member_chat_id,
        )

    async def report_destination_options(self, tenant_id: str) -> list[tuple[str, bool, str]]:
        """Each destination kind, whether it can be used now, and why not."""
        connections = self._adapter_connections()
        slack_chat = self.settings.chat_provider == "slack"
        email = connections is not None and await connections.resolve(tenant_id, "email")
        teams = connections is not None and await connections.resolve(tenant_id, "teams")
        memory_note = "Not available while the server runs in memory mode."
        return [
            (
                "chat_channel",
                slack_chat and connections is not None,
                "" if slack_chat else "Needs Slack as the chat provider.",
            ),
            ("person", True, ""),
            (
                "email",
                bool(email),
                "" if email else (memory_note if connections is None else "Set up Email first."),
            ),
            (
                "teams",
                bool(teams),
                "" if teams else (memory_note if connections is None else "Set up Teams first."),
            ),
        ]

    async def _member_chat_id(self, tenant_id: str, member_id: str) -> str | None:
        """Where a member's direct messages go: their linked chat id, else their member id."""
        member = await self.graph_repository().get_node(tenant_id, member_id)
        if member is None or member.kind is not NodeKind.DEVELOPER:
            return None
        link = await self.identity_link_repository().get_identity_link(tenant_id, member_id)
        return link.chat_user_id if link is not None and link.chat_user_id else member_id

    def connector_catalog(self) -> ConnectorCatalog:
        return SettingsConnectorCatalog(self.settings)

    def connection_resolver(self) -> CachedConnectionResolver:
        """The tenant connections adapters read, cached briefly in this process."""
        if self._connection_resolver is None:
            self._connection_resolver = CachedConnectionResolver(
                StoredConnections(
                    repository=self.connection_repository(),
                    secret_store=self.secret_store(),
                    catalog=self.connector_catalog(),
                )
            )
        return self._connection_resolver

    def _adapter_connections(self) -> CachedConnectionResolver | None:
        """The connections adapters follow: none in memory mode, which stays credential-free."""
        if self.settings.runtime_mode != "container":
            return None
        return self.connection_resolver()

    def connection_service(self) -> ConnectionService:
        return ConnectionService(
            repository=self.connection_repository(),
            secret_store=self.secret_store(),
            catalog=self.connector_catalog(),
            tester=HttpConnectionTester(self.settings),
            on_change=self.connection_resolver().invalidate,
            graph_repository=self.graph_repository(),
        )

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
        return catalog.build_chat_provider(
            self.settings, redis_client, mock_store, self._adapter_connections()
        )

    def chat_webhook_mapper(self, provider: str) -> ChatWebhookMapper | None:
        return catalog.build_chat_webhook_mapper(self.settings, provider)

    def map_chat_webhook(
        self, provider: str, payload: Mapping[str, object], correlation_id: str
    ) -> InboundMessage | None:
        mapper = self.chat_webhook_mapper(provider)
        return mapper.map_webhook(payload, correlation_id) if mapper else None

    async def chat_webhook_signature_valid(
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
        # The tenant's signing secret set in the console wins over the server's.
        signing_secret = await catalog.slack_credentials(
            self.settings, self.connection_resolver()
        ).value(SIGNING_SECRET)
        return verify_slack_signature(
            signing_secret,
            headers.get("x-slack-request-timestamp"),
            headers.get("x-slack-signature"),
            body,
            self.settings.slack_signature_tolerance_seconds,
        )

    def fast_ack_enabled(self) -> bool:
        return self.settings.slack_fast_ack_enabled

    async def accept_chat_event(
        self,
        provider: str,
        payload: Mapping[str, object],
        correlation_id: str,
    ) -> ChatWebhookProcessResult:
        """Take one already-authenticated provider event into reply processing.

        The HTTP webhook and the Slack Socket Mode listener both enter here, so
        either transport dedups, coalesces and processes replies identically.
        """
        if self.fast_ack_enabled():
            # Fast intake: dedup + arm; no LLM in the delivery path.
            return await self.enqueue_inbound_chat_event(provider, payload, correlation_id)
        return await self.process_chat_webhook(provider, payload, correlation_id)

    def slack_socket_listener(self) -> SlackSocketModeListener | None:
        """The Socket Mode intake loop, or None when Slack events use another path."""
        redis_client = None if self.settings.runtime_mode == "memory" else self._redis_client()
        return catalog.build_slack_socket_listener(
            self.settings,
            redis_client,
            lambda payload, correlation_id: self.accept_chat_event(
                "slack", payload, correlation_id
            ),
            self._adapter_connections(),
        )

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
        await self.arm_reply_coalesce(message.tenant_id, event.conversation_key)
        return ChatWebhookProcessResult(status="accepted", message_id=message.message_id)

    async def arm_reply_coalesce(self, tenant_id: str, conversation_key: str) -> None:
        """Arm the coalesce workflow for the conversation's buffered burst.

        The burst is keyed by its earliest unprocessed event: a message landing
        while that event is buffered resets the same debounce window, and the
        first message after a drained burst starts a new workflow.
        """
        burst_key = await self.reply_ingestion_service().pending_burst_key(
            tenant_id=tenant_id, conversation_key=conversation_key
        )
        if burst_key is None:
            # A drain already in flight took the event; nothing is left to arm.
            return
        await self.workflow_scheduler().arm_reply_coalesce(
            conversation_key, tenant_id, burst_key=burst_key
        )

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
            # No check-in of the reply's local day: the person's newest check-in
            # takes it, as a late update once that one has closed (G9).
            resolved_correlation_id = await collector.late_reply_correlation(message)
            if resolved_correlation_id is None:
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
        # A request is recorded the turn it is stated, while the check-in may
        # still be asking something else, so every outcome can carry some.
        if outcome.cross_person_requests:
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

    async def chat_simulator_messages(
        self, user_id: str | None = None
    ) -> list[Mapping[str, object]]:
        messages = await self._chat_simulator_store().list_messages(self.settings.tenant_id)
        if user_id is not None:
            messages = [message for message in messages if message.user_id == user_id]
        return [message.to_dict() for message in messages]

    async def open_chat_simulator_question(self, user_id: str) -> str | None:
        """Message id of the bot question this person has not answered yet.

        The simulator keeps one channel per person, so "unanswered" is the last
        bot message that actually asks something with no user message after it.
        Acknowledgements and consent prompts are bot messages too, and treating
        one as an open question would attach the next reply to a check-in that
        is already closed.
        """
        store = self._chat_simulator_store()
        channel_id = await store.open_channel(self.settings.tenant_id, user_id)
        messages = [
            message
            for message in await store.list_messages(self.settings.tenant_id)
            if message.channel_id == channel_id
        ]
        messages.sort(key=lambda message: (message.created_at, message.message_id))
        for message in reversed(messages):
            if message.direction == "user":
                if message.thread_id is not None:
                    # A reply inside a request DM's thread answers that DM, not
                    # the check-in question above it, which stays open.
                    continue
                return None
            if (
                message.direction == "bot"
                and message.correlation_id
                and message.purpose in _CHAT_QUESTION_PURPOSES
            ):
                return message.message_id
        return None

    async def send_chat_simulator_user_message(
        self,
        *,
        user_id: str,
        developer_id: str,
        developer_name: str | None,
        text: str,
        received_at: datetime | None,
        correlation_id: str,
        thread_id: str | None = None,
    ) -> Mapping[str, object]:
        """Post a message as a person, asking them for status first if needed.

        A reply only carries meaning against an open check-in, so when the
        person speaks unprompted the bot's question is issued synchronously
        first -- the same ``start_checkin`` path the scheduled fan-out uses --
        and the text then lands as its reply.

        With ``thread_id`` the message is instead a reply in the thread of that
        bot message, and takes the thread path below.
        """
        if thread_id is not None:
            return await self._send_chat_simulator_thread_reply(
                user_id=user_id,
                thread_id=thread_id,
                text=text,
                received_at=received_at,
                correlation_id=correlation_id,
            )
        question_id = await self.open_chat_simulator_question(user_id)
        started_checkin = False
        if question_id is None:
            await self.status_collector().start_checkin(
                tenant_id=self.settings.tenant_id,
                developer_id=developer_id,
                developer_name=developer_name,
                chat_external_id=user_id,
            )
            question_id = await self.open_chat_simulator_question(user_id)
            started_checkin = True
        if question_id is None:
            raise ProviderUnavailable("simulator could not open a check-in for this person")
        result = await self.inject_chat_simulator_reply(
            message_id=question_id,
            text=text,
            received_at=received_at,
            correlation_id=correlation_id,
        )
        return {**result, "started_checkin": started_checkin}

    async def _send_chat_simulator_thread_reply(
        self,
        *,
        user_id: str,
        thread_id: str,
        text: str,
        received_at: datetime | None,
        correlation_id: str,
    ) -> Mapping[str, object]:
        """Reply in the thread of one of the bot's messages, as Slack would.

        The reply reaches inbound routing with the parent's id as its thread, so
        a cross-person request DM claims it through the threaded-reply path.
        A thread reply is never a status update: it does not open a check-in
        and is not parsed as one. Under any message that is not a request DM
        nothing claims it, so it stays in the transcript under that message and
        is reported ``ignored`` -- it is deliberately not handed to the
        check-in collector, which would file it as the person's status.
        """
        store = self._chat_simulator_store()
        tenant_id = self.settings.tenant_id
        parent = await store.message_by_id(tenant_id=tenant_id, message_id=thread_id)
        if parent is None or parent.direction != "bot" or parent.user_id != user_id:
            raise ProviderUnavailable("simulator message to reply to was not found")
        request = await self.cross_person_request_repository().get_by_notify_message_id(
            tenant_id,
            parent.message_id,
        )
        if request is not None:
            result = await self.inject_chat_simulator_reply(
                message_id=parent.message_id,
                text=text,
                received_at=received_at,
                correlation_id=correlation_id,
                in_thread=True,
            )
            return {**result, "started_checkin": False}
        reply = await store.record_user_reply(
            tenant_id=tenant_id,
            reply_to_message_id=parent.message_id,
            text=text,
            created_at=received_at,
            in_thread=True,
        )
        return {
            "message_id": reply.message_id,
            "status": "ignored",
            "processed_message_id": reply.message_id,
            "started_checkin": False,
        }

    async def reset_chat_simulator(self) -> None:
        await self._chat_simulator_store().reset(self.settings.tenant_id)

    async def inject_chat_simulator_reply(
        self,
        *,
        message_id: str,
        text: str,
        received_at: datetime | None,
        correlation_id: str,
        in_thread: bool = False,
    ) -> Mapping[str, object]:
        reply = await self._chat_simulator_store().record_user_reply(
            tenant_id=self.settings.tenant_id,
            reply_to_message_id=message_id,
            text=text,
            created_at=received_at,
            in_thread=in_thread,
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

    def investigation_engine(self) -> InvestigationEngine:
        # Imported here: deepagents loads LangChain and its integrations, which
        # only an investigation needs.
        from infra.adapters.agents.deep_investigation import DeepAgentInvestigationEngine

        return DeepAgentInvestigationEngine()

    def issue_tracker(self) -> IssueTracker:
        if self._issue_tracker is None:
            self._issue_tracker = catalog.build_issue_tracker(
                self.settings,
                self._adapter_connections(),
            )
        return self._issue_tracker

    def vcs_provider(self) -> VcsProvider:
        if self._vcs_provider is None:
            self._vcs_provider = catalog.build_vcs_provider(
                self.settings,
                self._adapter_connections(),
            )
        return self._vcs_provider

    def calendar_provider(self) -> CalendarProvider:
        if self._calendar_provider is None:
            self._calendar_provider = catalog.build_calendar_provider(
                self.settings,
                self._adapter_connections(),
            )
        return self._calendar_provider

    def directory_provider(self) -> DirectoryProvider:
        if self._directory_provider is None:
            self._directory_provider = catalog.build_directory_provider(
                self.settings, self._adapter_connections()
            )
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
            identity_link_repository=self.identity_link_repository(),
        )

    def vcs_read_sync_service(self) -> VcsReadSyncService:
        return VcsReadSyncService(
            vcs_provider=self.vcs_provider(),
            graph_repository=self.graph_repository(),
            time_series_repository=self.time_series_repository(),
            cursor_repository=self.sync_cursor_repository(),
            identity_link_repository=self.identity_link_repository(),
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
            cursor_repository=self.sync_cursor_repository(),
        )

    def sync_status_service(self) -> SyncStatusService:
        settings = self.settings
        return SyncStatusService(
            graph_repository=self.graph_repository(),
            cursor_repository=self.sync_cursor_repository(),
            time_series_repository=self.time_series_repository(),
            config=SyncStatusConfig(
                issue_tracker_provider=settings.issue_tracker_provider,
                vcs_provider=settings.vcs_provider,
                calendar_provider=settings.calendar_provider,
                directory_provider=catalog.effective_directory_provider(settings),
                issue_sync_cron=settings.jira_sync_cron,
                vcs_sync_cron=settings.github_sync_cron,
                directory_sync_cron=settings.directory_sync_cron,
                legacy_issue_targets=legacy_issue_dispatches(settings, settings.tenant_id),
                legacy_vcs_targets=legacy_vcs_dispatches(settings, settings.tenant_id),
                simulated_providers=frozenset({"fake", _CHAT_SIMULATOR_PROVIDER}),
                provider_start_errors=self.sync_provider_start_errors(),
            ),
            provider_overrides=(
                self._connected_providers if self._adapter_connections() is not None else None
            ),
        )

    async def _connected_providers(self, tenant_id: str) -> dict[SyncSource, str]:
        """The provider each synced source uses because the tenant switched it on."""
        connections = self.connection_resolver()
        choices = (
            (SyncSource.ISSUE_TRACKER, ("jira",)),
            (SyncSource.VCS, ("gitlab", "github")),
            (SyncSource.CALENDAR, ("google_calendar",)),
        )
        providers: dict[SyncSource, str] = {}
        for source, connectors in choices:
            connector = await enabled_connector(connections, tenant_id, connectors)
            if connector is not None:
                providers[source] = "google" if connector == "google_calendar" else connector
        return providers

    def sync_provider_start_errors(self) -> dict[SyncSource, str]:
        """Sanitised reasons for each synced source whose provider can't be built.

        A provider that fails while being built (a missing chat token, an
        unusable secret key) makes the sync fail before it reaches any cursor,
        so the sync status would otherwise read "never synced". Building only
        wires settings into a client object -- no connection is opened and no
        provider is called -- and a failed build is not cached, so the next
        read retries. Only fixed text and the error type are kept, never the
        exception message.
        """
        builders = (
            (SyncSource.ISSUE_TRACKER, self.issue_tracker),
            (SyncSource.VCS, self.vcs_provider),
            (SyncSource.DIRECTORY, self.directory_provider),
        )
        errors: dict[SyncSource, str] = {}
        for source, build in builders:
            try:
                build()
            except Exception as error:
                errors[source] = provider_start_failure_message(error)
        return errors

    def cross_person_request_service(self) -> CrossPersonRequestService:
        return CrossPersonRequestService(
            repository=self.cross_person_request_repository(),
            chat_provider=self.chat_provider(),
            directory_repository=self.directory_user_repository(),
            time_series_repository=self.time_series_repository(),
            llm_provider=self.llm_provider(),
            blocker_settlement=BlockerSettlement(
                self.status_repository(), rollup_refresher=self.rollup_refresher()
            ),
            model=self.settings.litellm_model,
            auto_notify=self.settings.cross_person_auto_notify,
            notify_max_attempts=self.settings.cross_person_notify_max_attempts,
            notify_retry_backoff_seconds=self.settings.cross_person_notify_retry_backoff_seconds,
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
            rollups=self.person_rollups(),
        )

    def task_update_service(self) -> TaskUpdateService:
        return TaskUpdateService(
            graph_repository=self.graph_repository(),
            status_repository=self.status_repository(),
            time_series_repository=self.time_series_repository(),
            blocker_lifecycle=BlockerLifecycleService(
                self.status_repository(), self.graph_repository()
            ),
            blocker_resolution=BlockerResolutionService(
                self.graph_repository(), self.status_repository()
            ),
            write_back=self.write_back_service(),
            rollups=self.person_rollups(),
            tracker_name=self.provider_names().tracker,
        )

    def person_rollups(self) -> PersonRollups:
        """Today's rollup of one person's trees, recorded right after their own update.

        Each call builds a fresh ``RollupService`` with the drift the hourly
        rollup reads (``RiskService.owner_drift``), since one keeps a day's drift.
        """
        blockers = BlockerResolutionService(self.graph_repository(), self.status_repository())
        return PersonRollups(
            graph_repository=self.graph_repository(),
            rollup_repository=self.rollup_repository(),
            service_factory=lambda: RollupService(
                self.status_repository(),
                self.rollup_repository(),
                blockers,
                drift_signals=self.risk_service(),
            ),
        )

    def write_back_service(self) -> WriteBackService:
        return WriteBackService(
            issue_tracker=self.issue_tracker(),
            audit_repository=self.writeback_audit_repository(),
            config_repository=self.writeback_config_repository(),
            status_repository=self.status_repository(),
            identity_link_repository=self.identity_link_repository(),
            time_series_repository=self.time_series_repository(),
            graph_repository=self.graph_repository(),
            writeback_enabled_default=self.settings.jira_writeback_enabled,
            kind_switch=self.jira_writes_service().kind_on,
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
            cross_person_repository=self.cross_person_request_repository(),
            model=self.settings.litellm_model,
            tool_agent=tool_agent,
            conversation_retention_days=self.settings.conversation_retention_days,
            checkin_max_clarifications=self.settings.checkin_max_clarifications,
            checkin_ack_enabled=self.settings.checkin_ack_enabled,
            tenant_default_timezone=self.settings.tenant_default_timezone,
            outbound_dm_max_chars=self.settings.outbound_dm_max_chars,
            recent_fact_lookback_days=self.settings.recent_fact_lookback_days,
            # Every tracker but the fake one is the Jira adapter (catalog).
            issue_tracker_name=(
                DEFAULT_ISSUE_TRACKER_NAME
                if self.settings.issue_tracker_provider == "fake"
                else "Jira"
            ),
        )

    def provider_names(self) -> ProviderNames:
        """How reasons and briefs name the configured tracker and code host."""
        return catalog.build_provider_names(self.settings)

    def workflow_scheduler(self) -> WorkflowScheduler:
        return catalog.build_workflow_scheduler(self.settings)

    def workflow_worker(self) -> WorkflowWorker:
        return catalog.build_workflow_worker(self.settings)

    def rollup_refresher(self) -> RollupRefresher:
        return catalog.build_rollup_refresher(self.settings)

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
        reports = await self.readiness_report()
        return {name: report.ready for name, report in reports.items()}

    async def readiness_report(self) -> dict[str, ReadinessReport]:
        """Every dependency's readiness, with a reason wherever a probe has one."""
        probes = catalog.build_readiness_probes(
            self.settings,
            self._executor,
            self._redis_client,
            workflow_backlog_count=self._open_dead_letter_count,
            llm_history=self._llm_readiness_history,
            connections=self._adapter_connections(),
        )
        results = await asyncio.gather(
            *(self._bounded_report(probe) for probe in probes.values()),
            return_exceptions=False,
        )
        return dict(zip(probes.keys(), results, strict=True))

    async def close(self) -> None:
        """Release what this registry owns.

        The Postgres pool is not among it: it is the process's, shared with
        every other registry here, and ``shutdown`` closes it.
        """
        if self._redis_provider is not None:
            await self._redis_provider.close()

    async def shutdown(self) -> None:
        """Close this registry and the process's Postgres pool.

        Called once, by whatever owns the process (the API's lifespan, the
        worker's main), as it stops. A workflow step closes its registry with
        ``close`` and leaves the pool to the others.
        """
        await self.close()
        await close_shared_executors()

    def _executor(self) -> PsycopgAsyncExecutor:
        if self._postgres_executor is None:
            self._postgres_executor = shared_executor(
                self.settings.database_url,
                PoolConfig(
                    min_size=self.settings.postgres_pool_min_size,
                    max_size=self.settings.postgres_pool_max_size,
                    timeout_seconds=self.settings.postgres_pool_timeout_seconds,
                ),
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

    async def _bounded_report(self, probe: ReadinessProbe) -> ReadinessReport:
        try:
            if isinstance(probe, ReportingReadinessProbe):
                return await asyncio.wait_for(probe.report(), timeout=_READINESS_TIMEOUT_SECONDS)
            ready = await asyncio.wait_for(probe.check(), timeout=_READINESS_TIMEOUT_SECONDS)
            return ReadinessReport(ready=ready)
        except TimeoutError:
            return ReadinessReport(
                ready=False,
                detail=f"timeout: the check did not finish within {_READINESS_TIMEOUT_SECONDS:g}s",
            )
        except Exception as exc:
            # The type only: a driver error message can carry a DSN or credential.
            return ReadinessReport(
                ready=False, detail=f"error: the check failed ({type(exc).__name__})"
            )


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
