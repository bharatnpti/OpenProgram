from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx
import pytest
import respx

from core.domain.errors import ProviderUnavailable
from core.domain.messaging import ChatUserRef, OutboundMessage
from infra.adapters.chat.rate_limit import InMemoryRateLimiter
from infra.adapters.chat.slack import HttpSlackClient, SlackChatAdapter, SlackChatWebhookMapper
from tests.contract.contracts import assert_chat_contract, assert_chat_webhook_mapper_contract


@dataclass
class RecordingSlackHttpClient:
    messages: list[tuple[str, str]] = field(default_factory=list)

    async def open_conversation(self, user_id: str) -> str:
        return f"C-{user_id}"

    async def post_message(self, channel_id: str, text: str) -> str:
        self.messages.append((channel_id, text))
        return "1700000000.000001"

    async def latest_reply(self, thread_id: str) -> Mapping[str, object] | None:
        return None


async def test_slack_adapter_satisfies_chat_contract() -> None:
    adapter = SlackChatAdapter(
        tenant_id="demo",
        http_client=RecordingSlackHttpClient(),
        rate_limiter=InMemoryRateLimiter(),
    )
    await assert_chat_contract(adapter)


def test_slack_chat_webhook_mapper_satisfies_contract() -> None:
    assert_chat_webhook_mapper_contract(
        SlackChatWebhookMapper(tenant_id="demo"),
        {
            "event": {
                "user": "U123",
                "text": "blocked on API",
                "ts": "1700000000.000001",
                "channel": "C123",
            }
        },
    )


async def test_slack_adapter_maps_webhook_and_sends_dm() -> None:
    http = RecordingSlackHttpClient()
    adapter = SlackChatAdapter(
        tenant_id="demo",
        http_client=http,
        rate_limiter=InMemoryRateLimiter(),
    )
    inbound = adapter.map_webhook(
        {
            "event": {
                "user": "U123",
                "text": "blocked on API",
                "ts": "1700000000.000001",
                "channel": "C123",
            }
        },
        correlation_id="corr-1",
    )
    assert inbound.user.external_id == "U123"
    assert inbound.thread_id == "C123"
    message_id = await adapter.send_dm(
        ChatUserRef(tenant_id="demo", external_id="U123"),
        OutboundMessage(tenant_id="demo", text="thanks", correlation_id="corr-2"),
    )
    assert message_id == "1700000000.000001"
    assert http.messages == [("C-U123", "thanks")]


@respx.mock
async def test_http_slack_client_maps_recorded_dm_roundtrip() -> None:
    client = HttpSlackClient(bot_token="xoxb-test", base_url="https://slack.test/api")
    respx.post("https://slack.test/api/conversations.open").mock(
        return_value=httpx.Response(200, json={"ok": True, "channel": {"id": "D123"}})
    )
    respx.post("https://slack.test/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1700000000.000001"})
    )
    respx.get("https://slack.test/api/conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [{"user": "U123", "text": "reply", "ts": "1700000000.000002"}],
            },
        )
    )

    assert await client.open_conversation("U123") == "D123"
    assert await client.post_message("D123", "hello") == "1700000000.000001"
    reply = await client.latest_reply("D123")
    assert reply is not None
    assert reply["text"] == "reply"


@respx.mock
async def test_http_slack_client_retries_rate_limit() -> None:
    client = HttpSlackClient(
        bot_token="xoxb-test",
        base_url="https://slack.test/api",
        retry_attempts=2,
        retry_backoff_seconds=0.0,
    )
    respx.post("https://slack.test/api/chat.postMessage").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}, json={"ok": False}),
            httpx.Response(200, json={"ok": True, "ts": "1700000000.000003"}),
        ]
    )

    assert await client.post_message("D123", "hello") == "1700000000.000003"


@respx.mock
async def test_http_slack_client_maps_provider_failure() -> None:
    client = HttpSlackClient(bot_token="xoxb-test", base_url="https://slack.test/api")
    respx.post("https://slack.test/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )

    with pytest.raises(ProviderUnavailable):
        await client.post_message("missing", "hello")
