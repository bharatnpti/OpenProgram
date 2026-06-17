from __future__ import annotations

from collections.abc import Callable
from typing import cast

from config.settings import Settings
from infra.adapters.calendar.google_adapter import GoogleCalendarAdapter
from infra.adapters.chat.fake import FakeChatProvider, FakeChatWebhookMapper
from infra.adapters.chat.slack import SlackChatAdapter, SlackChatWebhookMapper
from infra.adapters.github.github_adapter import GitHubVcsAdapter
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

    principal = await registry.current_principal("token").get()

    assert principal.subject == "dev-1"
    assert principal.scopes == frozenset({"dev-mode", "token"})


def test_registry_returns_memory_phase_1_repositories() -> None:
    registry = ServiceRegistry(_settings(secret_key=SECRET_KEY, runtime_mode="memory"))

    graph_store = registry.graph_repository()

    assert isinstance(graph_store, InMemoryGraphStore)
    assert registry.status_repository() is graph_store
    assert registry.rollup_repository() is graph_store
    assert registry.sync_cursor_repository() is graph_store


def test_registry_returns_postgres_phase_1_repositories() -> None:
    registry = ServiceRegistry(_settings(secret_key=SECRET_KEY, runtime_mode="container"))

    assert isinstance(registry.status_repository(), PostgresStatusRepository)
    assert isinstance(registry.rollup_repository(), PostgresRollupRepository)
    assert isinstance(registry.sync_cursor_repository(), PostgresSyncCursorRepository)
    assert registry.status_repository() is registry.status_repository()
