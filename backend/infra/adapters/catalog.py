from __future__ import annotations

from collections.abc import Awaitable, Callable

from redis.asyncio import Redis

from config.settings import Settings
from core.application.persona_views import ProviderNames
from core.domain.errors import ProviderConfigurationError
from core.ports.calendar import CalendarProvider
from core.ports.chat import ChatProvider, ChatWebhookMapper
from core.ports.connections import ConnectionResolver
from core.ports.directory import DirectoryProvider
from core.ports.issue_tracker import IssueTracker
from core.ports.llm import LlmProvider
from core.ports.readiness import ReadinessProbe
from core.ports.vcs import VcsProvider
from core.ports.workflows import RollupRefresher, WorkflowScheduler, WorkflowWorker
from infra.adapters.calendar.google_adapter import GoogleCalendarAdapter
from infra.adapters.chat.fake import FakeChatProvider, FakeChatWebhookMapper
from infra.adapters.chat.mock_slack import (
    InMemoryMockSlackStore,
    MockSlackChatAdapter,
    MockSlackStore,
    RedisMockSlackStore,
)
from infra.adapters.chat.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from infra.adapters.chat.send_once import (
    InMemorySendOnceStore,
    RedisSendOnceStore,
    SendOnceStore,
)
from infra.adapters.chat.slack import (
    DisabledSlackHttpClient,
    HttpSlackClient,
    InMemoryConversationCache,
    RedisConversationCache,
    SlackChatAdapter,
)
from infra.adapters.chat.slack_connection import (
    APP_TOKEN,
    SIGNING_SECRET,
    ConnectionSlackHttpClient,
    SlackCredentialSource,
    SlackCredentialsReadinessProbe,
)
from infra.adapters.chat.slack_socket import (
    ChatEventSink,
    RedisSocketHeartbeat,
    SlackSocketModeListener,
    SocketHeartbeatReadinessProbe,
)
from infra.adapters.connections.routing import (
    TenantRoutedCalendarProvider,
    TenantRoutedIssueTracker,
    TenantRoutedVcsProvider,
)
from infra.adapters.directory.fake import FakeDirectoryProvider
from infra.adapters.directory.mock_slack import MockSlackDirectoryProvider
from infra.adapters.directory.slack import SlackDirectoryProvider
from infra.adapters.github.github_adapter import GitHubVcsAdapter
from infra.adapters.gitlab.gitlab_adapter import GitLabVcsAdapter
from infra.adapters.integrations.fake import (
    FakeCalendarProvider,
    FakeIssueTracker,
    FakeVcsProvider,
)
from infra.adapters.jira.jira_adapter import JiraIssueTrackerAdapter
from infra.adapters.llm.fake import FakeLlmProvider
from infra.adapters.llm.litellm_provider import LangfuseTraceSink, LiteLlmProvider, NoopTraceSink
from infra.adapters.llm.readiness import LlmEndpointReadinessProbe, LlmReadinessHistory
from infra.adapters.readiness import (
    AsyncReadinessExecutor,
    DatabaseExtensionsReadinessProbe,
    DatabaseReadinessProbe,
    DisabledReadinessProbe,
    HttpReadinessProbe,
    RedisReadinessProbe,
    StaticReadinessProbe,
    WorkflowBacklogReadinessProbe,
)
from infra.adapters.workflows.dbos import (
    DbosRollupRefresher,
    DbosWorkflowReadinessProbe,
    DbosWorkflowScheduler,
    DbosWorkflowWorker,
)
from infra.adapters.workflows.fake import (
    FakeRollupRefresher,
    FakeWorkflowReadinessProbe,
    FakeWorkflowScheduler,
    FakeWorkflowWorker,
)
from infra.adapters.workflows.temporal import (
    TemporalRollupRefresher,
    TemporalWorkflowReadinessProbe,
    TemporalWorkflowScheduler,
    TemporalWorkflowWorker,
)


def build_chat_provider(
    settings: Settings,
    redis_client: Redis | None = None,
    mock_slack_store: MockSlackStore | None = None,
    connections: ConnectionResolver | None = None,
) -> ChatProvider:
    if settings.chat_provider == "fake":
        return FakeChatProvider(tenant_id=settings.tenant_id)
    if settings.chat_provider == "mock_slack":
        return MockSlackChatAdapter(
            tenant_id=settings.tenant_id,
            store=mock_slack_store or build_mock_slack_store(settings, redis_client),
            rate_limiter=_chat_rate_limiter(settings, redis_client),
        )
    http_client = _slack_http_client(settings, connections)
    conversation_cache = (
        InMemoryConversationCache()
        if settings.runtime_mode == "memory"
        else RedisConversationCache(
            tenant_id=settings.tenant_id,
            client=_required_redis(redis_client),
        )
    )
    send_once_store: SendOnceStore = (
        InMemorySendOnceStore()
        if settings.runtime_mode == "memory"
        else RedisSendOnceStore(
            client=_required_redis(redis_client),
            ttl_seconds=settings.chat_send_once_ttl_seconds,
        )
    )
    return SlackChatAdapter(
        tenant_id=settings.tenant_id,
        http_client=http_client,
        rate_limiter=_chat_rate_limiter(settings, redis_client),
        conversation_cache=conversation_cache,
        send_once_store=send_once_store,
    )


def effective_directory_provider(settings: Settings) -> str:
    """The directory provider ``build_directory_provider`` actually builds."""
    if settings.directory_provider == "mock_slack":
        return "mock_slack"
    if settings.runtime_mode == "memory" or settings.directory_provider == "fake":
        return "fake"
    return "slack"


def build_directory_provider(
    settings: Settings, connections: ConnectionResolver | None = None
) -> DirectoryProvider:
    provider = effective_directory_provider(settings)
    if provider == "mock_slack":
        return MockSlackDirectoryProvider()
    if provider == "fake":
        return FakeDirectoryProvider()
    return SlackDirectoryProvider(http_client=_slack_http_client(settings, connections))


def build_chat_webhook_mapper(settings: Settings, provider: str) -> ChatWebhookMapper | None:
    if provider == "fake":
        return FakeChatWebhookMapper(tenant_id=settings.tenant_id)
    if provider in {"slack", "mock_slack"}:
        from infra.adapters.chat.slack import SlackChatWebhookMapper

        return SlackChatWebhookMapper(tenant_id=settings.tenant_id)
    return None


def build_slack_socket_listener(
    settings: Settings,
    redis_client: Redis | None,
    sink: ChatEventSink,
    connections: ConnectionResolver | None = None,
) -> SlackSocketModeListener | None:
    """Socket Mode intake for real Slack, or None when events arrive another way.

    With tenant connections the app token is read at every (re)connect, so one
    saved in the console is used from the next reconnect; until one is set the
    listener reports the missing token and keeps retrying at its slowest pace.
    """
    if not settings.slack_socket_mode or settings.runtime_mode != "container":
        return None
    url_opener: HttpSlackClient | ConnectionSlackHttpClient
    if connections is not None:
        url_opener = ConnectionSlackHttpClient(
            credentials=slack_credentials(settings, connections),
            token_key=APP_TOKEN,
            base_url=settings.slack_api_base_url,
            retry_attempts=settings.slack_retry_attempts,
            retry_backoff_seconds=settings.slack_retry_backoff_seconds,
        )
    else:
        if not settings.slack_app_token or not settings.slack_app_token.strip():
            raise ProviderConfigurationError(
                "slack_app_token (xapp-, scope connections:write) is required when "
                "slack_inbound_transport=socket"
            )
        url_opener = HttpSlackClient(
            bot_token=settings.slack_app_token,
            base_url=settings.slack_api_base_url,
            retry_attempts=settings.slack_retry_attempts,
            retry_backoff_seconds=settings.slack_retry_backoff_seconds,
        )
    return SlackSocketModeListener(
        url_opener=url_opener,
        sink=sink,
        heartbeat=_slack_socket_heartbeat(settings, _required_redis(redis_client)),
    )


def slack_channel_poster(
    settings: Settings, connections: ConnectionResolver
) -> Callable[[str, str], Awaitable[str]]:
    """Post a message to a Slack channel with the tenant's (or the server's) bot token."""
    client = ConnectionSlackHttpClient(
        credentials=slack_credentials(settings, connections),
        base_url=settings.slack_api_base_url,
        retry_attempts=settings.slack_retry_attempts,
        retry_backoff_seconds=settings.slack_retry_backoff_seconds,
    )
    return client.post_message


def slack_credentials(
    settings: Settings, connections: ConnectionResolver | None
) -> SlackCredentialSource:
    return SlackCredentialSource(
        tenant_id=settings.tenant_id,
        connections=connections,
        bot_token=settings.slack_bot_token,
        app_token=settings.slack_app_token,
        signing_secret=settings.slack_signing_secret,
    )


def build_mock_slack_store(
    settings: Settings,
    redis_client: Redis | None = None,
) -> MockSlackStore:
    if settings.runtime_mode == "memory":
        return InMemoryMockSlackStore()
    return RedisMockSlackStore(client=_required_redis(redis_client))


def build_issue_tracker(
    settings: Settings,
    connections: ConnectionResolver | None = None,
) -> IssueTracker:
    jira = JiraIssueTrackerAdapter(
        base_url=settings.jira_base_url if settings.issue_tracker_provider == "jira" else None,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        deployment=settings.jira_deployment,
        story_points_field=settings.jira_story_points_field,
        connections=connections,
    )
    if settings.issue_tracker_provider != "fake":
        return jira
    fake = FakeIssueTracker(tenant_id=settings.tenant_id)
    if connections is None:
        return fake
    # A demo tenant keeps the sample tracker until an admin turns Jira on.
    return TenantRoutedIssueTracker(fallback=fake, routes={"jira": jira}, connections=connections)


def build_vcs_provider(
    settings: Settings, connections: ConnectionResolver | None = None
) -> VcsProvider:
    gitlab = GitLabVcsAdapter(
        base_url=settings.gitlab_base_url,
        token=settings.gitlab_token if settings.vcs_provider == "gitlab" else None,
        namespace_id=settings.gitlab_namespace_id,
        connections=connections,
    )
    github = GitHubVcsAdapter(
        base_url=settings.github_base_url,
        token=settings.github_token if settings.vcs_provider == "github" else None,
        owner=settings.github_owner,
        connections=connections,
    )
    fallback: VcsProvider
    if settings.vcs_provider == "fake":
        fallback = FakeVcsProvider(tenant_id=settings.tenant_id)
    elif settings.vcs_provider == "gitlab":
        fallback = gitlab
    else:
        fallback = github
    if connections is None:
        return fallback
    # GitLab and GitHub are alternatives: whichever the tenant turned on wins.
    return TenantRoutedVcsProvider(
        fallback=fallback,
        routes={"gitlab": gitlab, "github": github},
        connections=connections,
    )


def build_calendar_provider(
    settings: Settings,
    connections: ConnectionResolver | None = None,
) -> CalendarProvider:
    google = GoogleCalendarAdapter(
        base_url=settings.google_calendar_base_url,
        token=settings.google_calendar_token,
        calendar_id=settings.google_calendar_id,
        connections=connections,
    )
    if settings.calendar_provider != "fake":
        return google
    fake = FakeCalendarProvider(tenant_id=settings.tenant_id)
    if connections is None:
        return fake
    return TenantRoutedCalendarProvider(
        fallback=fake, routes={"google_calendar": google}, connections=connections
    )


def build_llm_provider(settings: Settings) -> LlmProvider:
    if settings.llm_provider == "fake":
        return FakeLlmProvider()
    return LiteLlmProvider(
        base_url=settings.litellm_base_url,
        trace_sink=_langfuse_trace_sink(settings) or NoopTraceSink(),
        api_key=settings.litellm_api_key,
    )


def _langfuse_trace_sink(settings: Settings) -> LangfuseTraceSink | None:
    """Where LLM calls are traced, or None when tracing is not configured.

    Tracing needs a real LLM in container mode, a Langfuse host and both
    Langfuse keys; the host alone says nothing, as it has a localhost default.
    The llm_trace readiness probe asks this same question, so /ready checks
    Langfuse exactly when calls are traced there and reports it disabled
    otherwise.
    """
    public_key = settings.langfuse_public_key
    secret_key = settings.langfuse_secret_key
    if (
        settings.runtime_mode == "memory"
        or settings.llm_provider == "fake"
        or not settings.langfuse_host.strip()
        or not (public_key and public_key.strip())
        or not (secret_key and secret_key.strip())
    ):
        return None
    return LangfuseTraceSink(
        host=settings.langfuse_host, public_key=public_key, secret_key=secret_key
    )


_TRACKER_NAMES = {"jira": "Jira"}
_VCS_NAMES = {"gitlab": "GitLab", "github": "GitHub"}


def build_provider_names(settings: Settings) -> ProviderNames:
    """How a reason or brief names the configured tracker and code host: "Jira", "GitLab"."""
    return ProviderNames(
        tracker=_TRACKER_NAMES.get(settings.issue_tracker_provider, "the issue tracker"),
        vcs=_VCS_NAMES.get(settings.vcs_provider, "Git"),
    )


def build_workflow_scheduler(settings: Settings) -> WorkflowScheduler:
    if settings.workflow_provider == "fake":
        return FakeWorkflowScheduler(schedule_id=settings.resolved_heartbeat_schedule_id)
    if settings.workflow_provider == "dbos":
        return DbosWorkflowScheduler(
            app_name=settings.dbos_app_name,
            system_database_url=settings.resolved_dbos_system_database_url,
            system_pool_size=settings.dbos_system_pool_size,
            sync_queue_concurrency=settings.sync_queue_concurrency,
            schedule_id=settings.resolved_heartbeat_schedule_id,
            tenant_id=settings.tenant_id,
            heartbeat_cron=settings.dbos_heartbeat_cron,
            reply_debounce_seconds=settings.reply_debounce_seconds,
        )
    return TemporalWorkflowScheduler(
        target=settings.temporal_target,
        task_queue=settings.temporal_task_queue,
        schedule_id=settings.resolved_heartbeat_schedule_id,
        tenant_id=settings.tenant_id,
        interval_seconds=settings.temporal_heartbeat_interval_seconds,
        reply_debounce_seconds=settings.reply_debounce_seconds,
    )


def build_rollup_refresher(settings: Settings) -> RollupRefresher:
    """Re-records a day's rollup soon after a change made outside a check-in (N27)."""
    if settings.workflow_provider == "fake":
        return FakeRollupRefresher()
    if settings.workflow_provider == "dbos":
        return DbosRollupRefresher(
            app_name=settings.dbos_app_name,
            system_database_url=settings.resolved_dbos_system_database_url,
            system_pool_size=settings.dbos_system_pool_size,
            sync_queue_concurrency=settings.sync_queue_concurrency,
        )
    return TemporalRollupRefresher(
        target=settings.temporal_target,
        task_queue=settings.temporal_task_queue,
    )


def build_workflow_worker(settings: Settings) -> WorkflowWorker:
    if settings.workflow_provider == "fake":
        return FakeWorkflowWorker()
    if settings.workflow_provider == "dbos":
        return DbosWorkflowWorker(
            app_name=settings.dbos_app_name,
            system_database_url=settings.resolved_dbos_system_database_url,
            system_pool_size=settings.dbos_system_pool_size,
            sync_queue_concurrency=settings.sync_queue_concurrency,
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
            system_pool_size=settings.dbos_system_pool_size,
            sync_queue_concurrency=settings.sync_queue_concurrency,
        )
    return TemporalWorkflowReadinessProbe(target=settings.temporal_target)


def build_readiness_probes(
    settings: Settings,
    executor_factory: Callable[[], AsyncReadinessExecutor],
    redis_client_factory: Callable[[], Redis],
    workflow_backlog_count: Callable[[], Awaitable[int]] | None = None,
    llm_history: LlmReadinessHistory | None = None,
    connections: ConnectionResolver | None = None,
) -> dict[str, ReadinessProbe]:
    """The probes for one ``/ready`` call.

    ``llm_history`` outlives the call (the registry owns it), so a lone failed
    LLM probe right after a success reports ready; without it every failure
    degrades.
    """
    backlog_probe: ReadinessProbe = (
        WorkflowBacklogReadinessProbe(
            workflow_backlog_count,
            threshold=settings.workflow_backlog_ready_threshold,
        )
        if workflow_backlog_count is not None
        else StaticReadinessProbe()
    )
    if settings.runtime_mode == "memory":
        return {
            "settings": StaticReadinessProbe(),
            "registry": StaticReadinessProbe(),
            "graph_repository": StaticReadinessProbe(),
            "chat_provider": StaticReadinessProbe(),
            "llm_provider": StaticReadinessProbe(),
            "workflow_provider": StaticReadinessProbe(),
            "workflow_backlog": backlog_probe,
        }
    probes: dict[str, ReadinessProbe] = {
        "database": DatabaseReadinessProbe(executor_factory()),
        "database_extensions": DatabaseExtensionsReadinessProbe(executor_factory()),
        "redis": RedisReadinessProbe(redis_client_factory()),
        "slack_provider": _slack_provider_readiness_probe(settings, connections),
        "workflow_provider": build_workflow_readiness_probe(settings),
        "workflow_backlog": backlog_probe,
        "llm_provider": _llm_readiness_probe(settings, llm_history),
        "llm_trace": _llm_trace_readiness_probe(settings),
    }
    if settings.slack_socket_mode:
        probes["slack_socket"] = SocketHeartbeatReadinessProbe(
            _slack_socket_heartbeat(settings, redis_client_factory())
        )
    return probes


def _slack_provider_readiness_probe(
    settings: Settings, connections: ConnectionResolver | None = None
) -> ReadinessProbe:
    slack_selected = settings.chat_provider == "slack" or settings.directory_provider == "slack"
    if not slack_selected:
        return StaticReadinessProbe(healthy=True)
    # The bot token sends DMs either way; the second credential authenticates
    # inbound events and so follows the transport. Each may come from the
    # tenant's connection or from the server's settings.
    inbound_key = APP_TOKEN if settings.slack_inbound_transport == "socket" else SIGNING_SECRET
    return SlackCredentialsReadinessProbe(
        credentials=slack_credentials(settings, connections), inbound_key=inbound_key
    )


def _slack_socket_heartbeat(settings: Settings, redis_client: Redis) -> RedisSocketHeartbeat:
    return RedisSocketHeartbeat(tenant_id=settings.tenant_id, client=redis_client)


def _llm_readiness_probe(
    settings: Settings, history: LlmReadinessHistory | None = None
) -> ReadinessProbe:
    if settings.llm_provider == "fake":
        return StaticReadinessProbe()
    # Same base URL and key the adapter sends completions with, so the probe
    # answers for whatever sits there: a LiteLLM gateway, OpenAI, or the mock.
    return LlmEndpointReadinessProbe(
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
        history=history if history is not None else LlmReadinessHistory(),
    )


def _llm_trace_readiness_probe(settings: Settings) -> ReadinessProbe:
    sink = _langfuse_trace_sink(settings)
    if sink is None:
        # Nothing is traced, so there is nothing to wait for: tracing that is
        # not configured must not leave /ready degraded.
        return DisabledReadinessProbe()
    return HttpReadinessProbe(sink.host, "/api/public/health")


def _slack_http_client(
    settings: Settings, connections: ConnectionResolver | None = None
) -> HttpSlackClient | DisabledSlackHttpClient | ConnectionSlackHttpClient:
    if connections is not None and settings.runtime_mode == "container":
        # The token may be set in the console at any time: read it per call.
        return ConnectionSlackHttpClient(
            credentials=slack_credentials(settings, connections),
            base_url=settings.slack_api_base_url,
            retry_attempts=settings.slack_retry_attempts,
            retry_backoff_seconds=settings.slack_retry_backoff_seconds,
        )
    if settings.slack_bot_token:
        return HttpSlackClient(
            bot_token=settings.slack_bot_token,
            base_url=settings.slack_api_base_url,
            retry_attempts=settings.slack_retry_attempts,
            retry_backoff_seconds=settings.slack_retry_backoff_seconds,
        )
    if settings.runtime_mode == "container":
        raise ProviderConfigurationError("slack_bot_token is required when runtime_mode=container")
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
