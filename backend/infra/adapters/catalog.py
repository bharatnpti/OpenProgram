from __future__ import annotations

from collections.abc import Callable

from config.settings import Settings
from core.ports.chat import ChatProvider, ChatWebhookMapper
from core.ports.llm import LlmProvider
from core.ports.readiness import ReadinessProbe
from core.ports.workflows import WorkflowScheduler, WorkflowWorker
from infra.adapters.chat.fake import FakeChatProvider, FakeChatWebhookMapper
from infra.adapters.chat.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from infra.adapters.chat.slack import DisabledSlackHttpClient, HttpSlackClient, SlackChatAdapter
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


def build_chat_provider(settings: Settings) -> ChatProvider:
    if settings.chat_provider == "fake":
        return FakeChatProvider(tenant_id=settings.tenant_id)
    http_client = (
        HttpSlackClient(
            bot_token=settings.slack_bot_token,
            base_url=settings.slack_api_base_url,
            retry_attempts=settings.slack_retry_attempts,
            retry_backoff_seconds=settings.slack_retry_backoff_seconds,
        )
        if settings.slack_bot_token
        else DisabledSlackHttpClient()
    )
    rate_limiter = (
        InMemoryRateLimiter()
        if settings.runtime_mode == "memory"
        else RedisRateLimiter(
            redis_url=settings.redis_url,
            window_seconds=settings.redis_rate_limit_window_seconds,
            max_events=settings.redis_rate_limit_max_events,
        )
    )
    return SlackChatAdapter(
        tenant_id=settings.tenant_id,
        http_client=http_client,
        rate_limiter=rate_limiter,
    )


def build_chat_webhook_mapper(settings: Settings, provider: str) -> ChatWebhookMapper | None:
    if provider == "fake":
        return FakeChatWebhookMapper(tenant_id=settings.tenant_id)
    if provider == "slack":
        from infra.adapters.chat.slack import SlackChatWebhookMapper

        return SlackChatWebhookMapper(tenant_id=settings.tenant_id)
    return None


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
        return FakeWorkflowScheduler(schedule_id=settings.temporal_schedule_id)
    return TemporalWorkflowScheduler(
        target=settings.temporal_target,
        task_queue=settings.temporal_task_queue,
        schedule_id=settings.temporal_schedule_id,
        tenant_id=settings.tenant_id,
        interval_seconds=settings.temporal_heartbeat_interval_seconds,
    )


def build_workflow_worker(settings: Settings) -> WorkflowWorker:
    if settings.workflow_provider == "fake":
        return FakeWorkflowWorker()
    return TemporalWorkflowWorker(
        target=settings.temporal_target,
        task_queue=settings.temporal_task_queue,
    )


def build_workflow_readiness_probe(settings: Settings) -> ReadinessProbe:
    if settings.workflow_provider == "fake":
        return FakeWorkflowReadinessProbe()
    return TemporalWorkflowReadinessProbe(target=settings.temporal_target)


def build_readiness_probes(
    settings: Settings,
    executor_factory: Callable[[], AsyncReadinessExecutor],
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
        "redis": RedisReadinessProbe(settings.redis_url),
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
