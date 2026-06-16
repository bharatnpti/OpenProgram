from __future__ import annotations

from config.settings import Settings
from infra.adapters.chat.fake import FakeChatProvider, FakeChatWebhookMapper
from infra.adapters.chat.slack import SlackChatAdapter, SlackChatWebhookMapper
from infra.adapters.llm.fake import FakeLlmProvider
from infra.adapters.llm.litellm_provider import LiteLlmProvider
from infra.adapters.workflows.fake import FakeWorkflowScheduler, FakeWorkflowWorker
from infra.adapters.workflows.temporal import TemporalWorkflowScheduler, TemporalWorkflowWorker
from infra.registry import ServiceRegistry

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="


def test_registry_selects_fake_providers() -> None:
    registry = ServiceRegistry(
        Settings(
            secret_key=SECRET_KEY,
            runtime_mode="memory",
            chat_provider="fake",
            llm_provider="fake",
            workflow_provider="fake",
        )
    )

    assert isinstance(registry.chat_provider(), FakeChatProvider)
    assert isinstance(registry.chat_webhook_mapper("fake"), FakeChatWebhookMapper)
    assert isinstance(registry.llm_provider(), FakeLlmProvider)
    assert isinstance(registry.workflow_scheduler(), FakeWorkflowScheduler)
    assert isinstance(registry.workflow_worker(), FakeWorkflowWorker)


def test_registry_selects_real_configured_provider_adapters() -> None:
    registry = ServiceRegistry(
        Settings(
            secret_key=SECRET_KEY,
            runtime_mode="memory",
            chat_provider="slack",
            llm_provider="litellm",
            workflow_provider="temporal",
        )
    )

    assert isinstance(registry.chat_provider(), SlackChatAdapter)
    assert isinstance(registry.chat_webhook_mapper("slack"), SlackChatWebhookMapper)
    assert isinstance(registry.llm_provider(), LiteLlmProvider)
    assert isinstance(registry.workflow_scheduler(), TemporalWorkflowScheduler)
    assert isinstance(registry.workflow_worker(), TemporalWorkflowWorker)
