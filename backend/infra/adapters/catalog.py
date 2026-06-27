from __future__ import annotations

from collections.abc import Callable

from redis.asyncio import Redis

from config.settings import Settings
from core.ports.calendar import CalendarProvider
from core.ports.chat import ChatProvider, ChatWebhookMapper
from core.ports.directory import DirectoryProvider
from core.ports.issue_tracker import IssueTracker
from core.ports.llm import LlmProvider
from core.ports.readiness import ReadinessProbe
from core.ports.secrets import SecretStore
from core.ports.vcs import VcsProvider
from core.ports.workflows import WorkflowScheduler, WorkflowWorker
from infra.adapters.calendar.google_adapter import GoogleCalendarAdapter
from infra.adapters.chat.fake import FakeChatProvider, FakeChatWebhookMapper
from infra.adapters.chat.mock_slack import (
    InMemoryMockSlackStore,
    MockSlackChatAdapter,
    MockSlackStore,
    RedisMockSlackStore,
)
from infra.adapters.chat.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from infra.adapters.chat.slack import (
    DisabledSlackHttpClient,
    HttpSlackClient,
    InMemoryConversationCache,
    RedisConversationCache,
    SlackChatAdapter,
)
from infra.adapters.directory.fake import FakeDirectoryProvider
from infra.adapters.directory.mock_slack import MockSlackDirectoryProvider
from infra.adapters.directory.slack import SlackDirectoryProvider
from infra.adapters.github.github_adapter import GitHubVcsAdapter
from infra.adapters.integrations.fake import (
    FakeCalendarProvider,
    FakeIssueTracker,
    FakeVcsProvider,
)
from infra.adapters.jira.jira_adapter import JiraIssueTrackerAdapter
from infra.adapters.llm.fake import FakeLlmProvider
from infra.adapters.llm.litellm_provider import LangfuseTraceSink, LiteLlmProvider, NoopTraceSink
from infra.adapters.readiness import (
    AsyncReadinessExecutor,
    DatabaseExtensionsReadinessProbe,
    DatabaseReadinessProbe,
    HttpReadinessProbe,
    RedisReadinessProbe,
    StaticReadinessProbe,
)
from infra.adapters.workflows.dbos import (
    DbosWorkflowReadinessProbe,
    DbosWorkflowScheduler,
    DbosWorkflowWorker,
)
from infra.adapters.workflows.fake import (
    FakeWorkflowReadinessProbe,
    FakeWorkflowScheduler,
    FakeWorkflowWorker,
)
from infra.adapters.workflows.temporal import (
    TemporalWorkflowReadinessProbe,
    TemporalWorkflowScheduler,
    TemporalWorkflowWorker,
)


def build_chat_provider(
    settings: Settings,
    redis_client: Redis | None = None,
    mock_slack_store: MockSlackStore | None = None,
) -> ChatProvider:
    if settings.chat_provider == "fake":
        return FakeChatProvider(tenant_id=settings.tenant_id)
    if settings.chat_provider == "mock_slack":
        return MockSlackChatAdapter(
            tenant_id=settings.tenant_id,
            store=mock_slack_store or build_mock_slack_store(settings, redis_client),
            rate_limiter=_chat_rate_limiter(settings, redis_client),
        )
    http_client = _slack_http_client(settings)
    conversation_cache = (
        InMemoryConversationCache()
        if settings.runtime_mode == "memory"
        else RedisConversationCache(
            tenant_id=settings.tenant_id,
            client=_required_redis(redis_client),
        )
    )
    return SlackChatAdapter(
        tenant_id=settings.tenant_id,
        http_client=http_client,
        rate_limiter=_chat_rate_limiter(settings, redis_client),
        conversation_cache=conversation_cache,
    )


def build_directory_provider(settings: Settings) -> DirectoryProvider:
    if settings.directory_provider == "mock_slack":
        return MockSlackDirectoryProvider()
    if settings.runtime_mode == "memory" or settings.directory_provider == "fake":
        return FakeDirectoryProvider()
    return SlackDirectoryProvider(http_client=_slack_http_client(settings))


def build_chat_webhook_mapper(settings: Settings, provider: str) -> ChatWebhookMapper | None:
    if provider == "fake":
        return FakeChatWebhookMapper(tenant_id=settings.tenant_id)
    if provider in {"slack", "mock_slack"}:
        from infra.adapters.chat.slack import SlackChatWebhookMapper

        return SlackChatWebhookMapper(tenant_id=settings.tenant_id)
    return None


def build_mock_slack_store(
    settings: Settings,
    redis_client: Redis | None = None,
) -> MockSlackStore:
    if settings.runtime_mode == "memory":
        return InMemoryMockSlackStore()
    return RedisMockSlackStore(client=_required_redis(redis_client))


def build_issue_tracker(
    settings: Settings,
    secret_store: SecretStore | None = None,
) -> IssueTracker:
    if settings.issue_tracker_provider == "fake":
        return FakeIssueTracker(tenant_id=settings.tenant_id)
    return JiraIssueTrackerAdapter(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        secret_store=secret_store,
    )


def build_vcs_provider(settings: Settings, secret_store: SecretStore | None = None) -> VcsProvider:
    if settings.vcs_provider == "fake":
        return FakeVcsProvider(tenant_id=settings.tenant_id)
    return GitHubVcsAdapter(
        base_url=settings.github_base_url,
        token=settings.github_token,
        owner=settings.github_owner,
        secret_store=secret_store,
    )


def build_calendar_provider(
    settings: Settings,
    secret_store: SecretStore | None = None,
) -> CalendarProvider:
    if settings.calendar_provider == "fake":
        return FakeCalendarProvider(tenant_id=settings.tenant_id)
    return GoogleCalendarAdapter(
        base_url=settings.google_calendar_base_url,
        token=settings.google_calendar_token,
        calendar_id=settings.google_calendar_id,
        secret_store=secret_store,
    )


def build_llm_provider(settings: Settings) -> LlmProvider:
    if settings.llm_provider == "fake":
        return FakeLlmProvider()
    trace_sink = (
        NoopTraceSink()
        if settings.runtime_mode == "memory"
        else LangfuseTraceSink(
            host=settings.langfuse_host,
            public_key=_required(settings.langfuse_public_key, "langfuse_public_key"),
            secret_key=_required(settings.langfuse_secret_key, "langfuse_secret_key"),
        )
    )
    return LiteLlmProvider(
        base_url=settings.litellm_base_url,
        trace_sink=trace_sink,
        api_key=settings.litellm_api_key,
    )


def build_workflow_scheduler(settings: Settings) -> WorkflowScheduler:
    if settings.workflow_provider == "fake":
        return FakeWorkflowScheduler(schedule_id=settings.resolved_heartbeat_schedule_id)
    if settings.workflow_provider == "dbos":
        return DbosWorkflowScheduler(
            app_name=settings.dbos_app_name,
            system_database_url=settings.resolved_dbos_system_database_url,
            schedule_id=settings.resolved_heartbeat_schedule_id,
            tenant_id=settings.tenant_id,
            heartbeat_cron=settings.dbos_heartbeat_cron,
        )
    return TemporalWorkflowScheduler(
        target=settings.temporal_target,
        task_queue=settings.temporal_task_queue,
        schedule_id=settings.resolved_heartbeat_schedule_id,
        tenant_id=settings.tenant_id,
        interval_seconds=settings.temporal_heartbeat_interval_seconds,
    )


def build_workflow_worker(settings: Settings) -> WorkflowWorker:
    if settings.workflow_provider == "fake":
        return FakeWorkflowWorker()
    if settings.workflow_provider == "dbos":
        return DbosWorkflowWorker(
            app_name=settings.dbos_app_name,
            system_database_url=settings.resolved_dbos_system_database_url,
        )
    return TemporalWorkflowWorker(
        target=settings.temporal_target,
        task_queue=settings.temporal_task_queue,
    )


def build_workflow_readiness_probe(settings: Settings) -> ReadinessProbe:
    if settings.workflow_provider == "fake":
        return FakeWorkflowReadinessProbe()
    if settings.workflow_provider == "dbos":
        return DbosWorkflowReadinessProbe(
            app_name=settings.dbos_app_name,
            system_database_url=settings.resolved_dbos_system_database_url,
        )
    return TemporalWorkflowReadinessProbe(target=settings.temporal_target)


def build_readiness_probes(
    settings: Settings,
    executor_factory: Callable[[], AsyncReadinessExecutor],
    redis_client_factory: Callable[[], Redis],
) -> dict[str, ReadinessProbe]:
    if settings.runtime_mode == "memory":
        return {
            "settings": StaticReadinessProbe(),
            "registry": StaticReadinessProbe(),
            "graph_repository": StaticReadinessProbe(),
            "chat_provider": StaticReadinessProbe(),
            "llm_provider": StaticReadinessProbe(),
            "workflow_provider": StaticReadinessProbe(),
        }
    return {
        "database": DatabaseReadinessProbe(executor_factory()),
        "database_extensions": DatabaseExtensionsReadinessProbe(executor_factory()),
        "redis": RedisReadinessProbe(redis_client_factory()),
        "workflow_provider": build_workflow_readiness_probe(settings),
        "llm_provider": _llm_readiness_probe(settings),
        "llm_trace": _llm_trace_readiness_probe(settings),
    }


def _llm_readiness_probe(settings: Settings) -> ReadinessProbe:
    if settings.llm_provider == "fake":
        return StaticReadinessProbe()
    return HttpReadinessProbe(settings.litellm_base_url, "/health/readiness")


def _llm_trace_readiness_probe(settings: Settings) -> ReadinessProbe:
    if settings.runtime_mode == "memory" or settings.llm_provider == "fake":
        return StaticReadinessProbe()
    return HttpReadinessProbe(settings.langfuse_host, "/api/public/health")


def _required(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required when runtime_mode=container")
    return value


def _slack_http_client(settings: Settings) -> HttpSlackClient | DisabledSlackHttpClient:
    if settings.slack_bot_token:
        return HttpSlackClient(
            bot_token=settings.slack_bot_token,
            base_url=settings.slack_api_base_url,
            retry_attempts=settings.slack_retry_attempts,
            retry_backoff_seconds=settings.slack_retry_backoff_seconds,
        )
    return DisabledSlackHttpClient()


def _chat_rate_limiter(
    settings: Settings, redis_client: Redis | None
) -> InMemoryRateLimiter | RedisRateLimiter:
    if settings.runtime_mode == "memory":
        return InMemoryRateLimiter()
    return RedisRateLimiter(
        client=_required_redis(redis_client),
        window_seconds=settings.redis_rate_limit_window_seconds,
        max_events=settings.redis_rate_limit_max_events,
    )


def _required_redis(client: Redis | None) -> Redis:
    if client is None:
        raise ValueError("redis client is required when runtime_mode=container")
    return client
