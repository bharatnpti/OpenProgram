from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.domain.messaging import ChatUserRef, OutboundMessage
from infra.adapters.chat.rate_limit import InMemoryRateLimiter
from infra.adapters.chat.slack import SlackChatAdapter
from tests.contract.contracts import assert_chat_contract


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
