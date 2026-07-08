from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from config.settings import Settings
from core.domain.errors import ProviderConfigurationError
from core.ports.auth import AuthCredentials
from infra.adapters import catalog
from infra.adapters.calendar.google_adapter import GoogleCalendarAdapter
from infra.adapters.chat.fake import FakeChatProvider, FakeChatWebhookMapper
from infra.adapters.chat.mock_slack import InMemoryMockSlackStore, MockSlackChatAdapter
from infra.adapters.chat.slack import SlackChatAdapter, SlackChatWebhookMapper
from infra.adapters.directory.mock_slack import MockSlackDirectoryProvider
from infra.adapters.github.github_adapter import GitHubVcsAdapter
from infra.adapters.gitlab.gitlab_adapter import GitLabVcsAdapter
from infra.adapters.integrations.fake import (
    FakeCalendarProvider,
    FakeIssueTracker,
    FakeVcsProvider,
)
from infra.adapters.jira.jira_adapter import JiraIssueTrackerAdapter
from infra.adapters.llm.fake import FakeLlmProvider
from infra.adapters.llm.litellm_provider import LiteLlmProvider
from infra.adapters.workflows.dbos import DbosWorkflowScheduler, DbosWorkflowWorker
from infra.adapters.workflows.fake import FakeWorkflowScheduler, FakeWorkflowWorker
from infra.adapters.workflows.temporal import TemporalWorkflowScheduler, TemporalWorkflowWorker
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_status import (
    PostgresConversationRepository,
    PostgresRollupRepository,
    PostgresStatusRepository,
    PostgresSyncCursorRepository,
)
from infra.registry import ServiceRegistry

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="


def _settings(**overrides: object) -> Settings:
    settings_factory = cast(Callable[..., Settings], Settings)
    return settings_factory(_env_file=None, **overrides)


def test_registry_selects_fake_providers() -> None:
    registry = ServiceRegistry(
        _settings(
            secret_key=SECRET_KEY,
            runtime_mode="memory",
            heartbeat_schedule_id="fake-heartbeat",
            chat_provider="fake",
            issue_tracker_provider="fake",
            vcs_provider="fake",
            calendar_provider="fake",
            llm_provider="fake",
            workflow_provider="fake",
        )
    )

    assert isinstance(registry.chat_provider(), FakeChatProvider)
    assert isinstance(registry.chat_webhook_mapper("fake"), FakeChatWebhookMapper)
    assert isinstance(registry.llm_provider(), FakeLlmProvider)
    assert isinstance(registry.issue_tracker(), FakeIssueTracker)
    assert isinstance(registry.vcs_provider(), FakeVcsProvider)
    assert isinstance(registry.calendar_provider(), FakeCalendarProvider)
    workflow_scheduler = registry.workflow_scheduler()
    assert isinstance(workflow_scheduler, FakeWorkflowScheduler)
    assert workflow_scheduler.schedule_id == "fake-heartbeat"
    assert isinstance(registry.workflow_worker(), FakeWorkflowWorker)


def test_registry_selects_real_configured_provider_adapters() -> None:
    registry = ServiceRegistry(
        _settings(
            secret_key=SECRET_KEY,
            runtime_mode="memory",
            chat_provider="slack",
            issue_tracker_provider="jira",
            jira_base_url="https://jira.test",
            jira_email="agent@example.com",
            jira_api_token="token",
            vcs_provider="github",
            github_base_url="https://github.test",
            github_token="token",
            github_owner="acme",
            calendar_provider="google",
            google_calendar_base_url="https://calendar.test",
            google_calendar_token="token",
            llm_provider="litellm",
            heartbeat_schedule_id="temporal-heartbeat",
            workflow_provider="temporal",
        )
    )

    assert isinstance(registry.chat_provider(), SlackChatAdapter)
    assert isinstance(registry.chat_webhook_mapper("slack"), SlackChatWebhookMapper)
    assert isinstance(registry.llm_provider(), LiteLlmProvider)
    assert isinstance(registry.issue_tracker(), JiraIssueTrackerAdapter)
    assert isinstance(registry.vcs_provider(), GitHubVcsAdapter)
    assert isinstance(registry.calendar_provider(), GoogleCalendarAdapter)
    workflow_scheduler = registry.workflow_scheduler()
    assert isinstance(workflow_scheduler, TemporalWorkflowScheduler)
    assert workflow_scheduler.schedule_id == "temporal-heartbeat"
    assert isinstance(registry.workflow_worker(), TemporalWorkflowWorker)


def test_registry_selects_gitlab_vcs_adapter() -> None:
    registry = ServiceRegistry(
        _settings(
            secret_key=SECRET_KEY,
            runtime_mode="memory",
            chat_provider="fake",
            issue_tracker_provider="fake",
            vcs_provider="gitlab",
            gitlab_base_url="https://gitlab.test/api/v4",
            gitlab_token="token",
            gitlab_namespace_id="136978033",
            calendar_provider="fake",
            llm_provider="fake",
            workflow_provider="fake",
        )
    )

    assert isinstance(registry.vcs_provider(), GitLabVcsAdapter)


def test_container_slack_chat_provider_requires_bot_token() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        runtime_mode="container",
        chat_provider="slack",
        slack_bot_token=None,
        slack_signing_secret="signing-secret",
    )

    with pytest.raises(ProviderConfigurationError) as exc_info:
        catalog.build_chat_provider(settings)

    assert "slack_bot_token" in str(exc_info.value)


@pytest.mark.parametrize(
    ("slack_bot_token", "slack_signing_secret", "expected_healthy"),
    [
        (None, None, False),
        ("xoxb-test", None, False),
        (None, "signing-secret", False),
        ("xoxb-test", "signing-secret", True),
    ],
)
async def test_container_slack_readiness_requires_bot_token_and_signing_secret(
    slack_bot_token: str | None,
    slack_signing_secret: str | None,
    expected_healthy: bool,
) -> None:
    probes = catalog.build_readiness_probes(
        _settings(
            secret_key=SECRET_KEY,
            runtime_mode="container",
            chat_provider="slack",
            directory_provider="fake",
            llm_provider="fake",
            workflow_provider="fake",
            slack_bot_token=slack_bot_token,
            slack_signing_secret=slack_signing_secret,
        ),
        _FakeReadinessExecutor,
        _FakeRedis,
    )

    assert await probes["slack_provider"].check() is expected_healthy


@pytest.mark.parametrize(
    ("chat_provider", "directory_provider"),
    [
        ("fake", "fake"),
        ("mock_slack", "mock_slack"),
    ],
)
async def test_container_non_real_slack_readiness_does_not_require_real_credentials(
    chat_provider: str,
    directory_provider: str,
) -> None:
    probes = catalog.build_readiness_probes(
        _settings(
            secret_key=SECRET_KEY,
            runtime_mode="container",
            chat_provider=chat_provider,
            directory_provider=directory_provider,
            llm_provider="fake",
            workflow_provider="fake",
            slack_bot_token=None,
            slack_signing_secret=None,
        ),
        _FakeReadinessExecutor,
        _FakeRedis,
    )

    assert await probes["slack_provider"].check() is True


async def test_registry_selects_mock_slack_provider_adapters() -> None:
    registry = ServiceRegistry(
        _settings(
            secret_key=SECRET_KEY,
            runtime_mode="memory",
            chat_provider="mock_slack",
            directory_provider="mock_slack",
            issue_tracker_provider="fake",
            vcs_provider="fake",
            calendar_provider="fake",
            llm_provider="fake",
            workflow_provider="fake",
            chat_simulator_enabled=True,
        )
    )

    chat_provider = registry.chat_provider()
    assert isinstance(chat_provider, MockSlackChatAdapter)
    assert isinstance(registry.chat_webhook_mapper("mock_slack"), SlackChatWebhookMapper)
    assert isinstance(registry.directory_provider(), MockSlackDirectoryProvider)
    assert isinstance(registry._chat_simulator_store(), InMemoryMockSlackStore)
    assert registry.chat_simulator_available() is True
    status = await registry.chat_simulator_status()
    assert status["provider"] == "mock_slack"
    assert status["message_count"] == 0


def test_registry_defaults_to_dbos_workflow_provider() -> None:
    registry = ServiceRegistry(
        _settings(
            secret_key=SECRET_KEY,
            runtime_mode="memory",
            heartbeat_schedule_id="dbos-heartbeat",
        )
    )

    workflow_scheduler = registry.workflow_scheduler()
    assert isinstance(workflow_scheduler, DbosWorkflowScheduler)
    assert workflow_scheduler.schedule_id == "dbos-heartbeat"
    assert isinstance(registry.workflow_worker(), DbosWorkflowWorker)


async def test_registry_current_principal_uses_auth_provider() -> None:
    registry = ServiceRegistry(
        _settings(
            secret_key=SECRET_KEY,
            runtime_mode="memory",
            dev_principal_subject="dev-1",
            dev_principal_roles="dev,sm",
        )
    )

    principal = await registry.current_principal(
        AuthCredentials(authorization="token"),
    ).get()

    assert principal.subject == "dev-1"
    assert principal.scopes == frozenset({"dev-mode", "token"})


def test_registry_returns_memory_phase_1_repositories() -> None:
    registry = ServiceRegistry(_settings(secret_key=SECRET_KEY, runtime_mode="memory"))

    graph_store = registry.graph_repository()

    assert isinstance(graph_store, InMemoryGraphStore)
    assert registry.status_repository() is graph_store
    assert registry.conversation_repository() is graph_store
    assert registry.rollup_repository() is graph_store
    assert registry.sync_cursor_repository() is graph_store


def test_registry_returns_postgres_phase_1_repositories() -> None:
    registry = ServiceRegistry(_settings(secret_key=SECRET_KEY, runtime_mode="container"))

    assert isinstance(registry.status_repository(), PostgresStatusRepository)
    assert isinstance(registry.conversation_repository(), PostgresConversationRepository)
    assert isinstance(registry.rollup_repository(), PostgresRollupRepository)
    assert isinstance(registry.sync_cursor_repository(), PostgresSyncCursorRepository)
    assert registry.status_repository() is registry.status_repository()
    assert registry.conversation_repository() is registry.conversation_repository()


class _FakeReadinessExecutor:
    async def fetch(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> list[dict[str, object]]:
        if "pg_extension" in query:
            return [{"extname": "age"}, {"extname": "timescaledb"}, {"extname": "vector"}]
        return [{"ok": 1}]


class _FakeRedis:
    async def ping(self) -> bool:
        return True
