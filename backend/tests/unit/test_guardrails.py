"""The guardrails in docs/guardrails.md: what each one refuses, and what it lets through."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from api.dependencies import get_ask_service
from api.main import create_app
from config.settings import Settings
from core.application.agents.tool_loop import ToolCallingAgent
from core.application.outbound_text import LINK_REMOVED, carries_link_or_markup, scrubbed
from core.application.status_parsing import (
    GENERIC_CLARIFICATION_QUESTION,
    ClarificationEvaluator,
)
from core.domain.errors import (
    LlmBudgetExhausted,
    LlmRequestTooLarge,
    OutboundLimitReached,
    ProviderUnavailable,
)
from core.domain.graph import JsonScalar
from core.domain.llm import LlmRequest, LlmResponse, LlmToolCall, LlmTurn, TokenUsage
from core.domain.messaging import ChatUserRef, OutboundMessage
from infra.adapters.chat.rate_limit import InMemoryRateLimiter
from infra.adapters.chat.send_cap import InMemoryDailySendCap, RedisDailySendCap
from infra.adapters.chat.slack import SlackChatAdapter
from infra.adapters.llm.fake import FakeLlmProvider
from infra.adapters.llm.guard import InMemoryTokenLedger, LlmCallGuard, RedisTokenLedger
from infra.adapters.llm.litellm_provider import LiteLlmProvider, NoopTraceSink

TODAY = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
TOMORROW = datetime(2026, 10, 10, 0, 1, tzinfo=UTC)
LITELLM = "https://litellm.test"


def _request(prompt: str = "status?", tenant_id: str = "demo") -> LlmRequest:
    return LlmRequest(
        tenant_id=tenant_id,
        prompt=prompt,
        model="test-model",
        correlation_id="corr-1",
        metadata={"purpose": "test"},
    )


def _completion(total_tokens: int) -> dict[str, object]:
    return {
        "id": "trace-1",
        "model": "test-model",
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": total_tokens - 1,
            "completion_tokens": 1,
            "total_tokens": total_tokens,
        },
    }


def _guarded_provider(guard: LlmCallGuard) -> LiteLlmProvider:
    return LiteLlmProvider(base_url=LITELLM, trace_sink=NoopTraceSink(), guard=guard)


# --- Language-model calls -----------------------------------------------------


@respx.mock
async def test_a_request_over_the_size_ceiling_is_refused_before_it_is_sent() -> None:
    route = respx.post(f"{LITELLM}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion(10))
    )
    provider = _guarded_provider(
        LlmCallGuard(ledger=InMemoryTokenLedger(), daily_token_budget=0, max_request_chars=100)
    )

    with pytest.raises(LlmRequestTooLarge):
        await provider.complete(_request("x" * 101))
    await provider.complete(_request("x" * 100))

    assert route.call_count == 1


@respx.mock
async def test_an_agent_harness_conversation_counts_toward_the_size_ceiling() -> None:
    route = respx.post(f"{LITELLM}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion(10))
    )
    provider = _guarded_provider(
        LlmCallGuard(ledger=InMemoryTokenLedger(), daily_token_budget=0, max_request_chars=100)
    )
    kept = (LlmTurn(role="user", content="x" * 60), LlmTurn(role="assistant", content="y" * 41))

    with pytest.raises(LlmRequestTooLarge):
        await provider.complete(replace(_request(""), turns=kept))

    assert route.call_count == 0


@respx.mock
async def test_calls_stop_once_the_days_token_budget_is_spent_and_resume_next_day() -> None:
    route = respx.post(f"{LITELLM}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion(60))
    )
    now = [TODAY]
    ledger = InMemoryTokenLedger()
    provider = _guarded_provider(
        LlmCallGuard(
            ledger=ledger, daily_token_budget=100, max_request_chars=0, clock=lambda: now[0]
        )
    )

    await provider.complete(_request())
    await provider.complete(_request())  # 60 spent: still under 100, so it runs
    with pytest.raises(LlmBudgetExhausted):
        await provider.complete(_request())
    # Another tenant has its own budget.
    await provider.complete(_request(tenant_id="other"))
    now[0] = TOMORROW
    await provider.complete(_request())

    assert route.call_count == 4
    assert await ledger.used("demo", date(2026, 10, 9)) == 120
    assert await ledger.used("demo", date(2026, 10, 10)) == 60


def test_a_refused_call_is_a_provider_outage_to_every_caller() -> None:
    # Callers already degrade on ProviderUnavailable: briefs and gate scans
    # fall back, a reply waits in the inbound queue.
    assert issubclass(LlmBudgetExhausted, ProviderUnavailable)
    assert issubclass(LlmRequestTooLarge, ProviderUnavailable)


async def test_the_redis_ledger_counts_per_tenant_and_day() -> None:
    redis = _FakeRedis()
    ledger = RedisTokenLedger(client=redis)  # type: ignore[arg-type]

    await ledger.add("demo", date(2026, 10, 9), 40)
    await ledger.add("demo", date(2026, 10, 9), 2)

    assert await ledger.used("demo", date(2026, 10, 9)) == 42
    assert await ledger.used("demo", date(2026, 10, 10)) == 0
    assert redis.ttl["openprogram:llm:tokens:demo:2026-10-09"] == 2 * 86_400


def test_the_guardrails_are_on_by_default(settings: Settings) -> None:
    assert settings.llm_daily_token_budget == 10_000_000
    assert settings.llm_max_request_chars == 400_000
    assert settings.chat_daily_dm_limit == 100


# --- The tool loop ------------------------------------------------------------


async def test_tool_calls_past_the_per_turn_limit_are_answered_but_not_run() -> None:
    calls = tuple(
        LlmToolCall(id=f"call-{n}", name="echo_tool", arguments={"value": str(n)}) for n in range(3)
    )
    provider = FakeLlmProvider(
        responses=[_llm_response(tool_calls=calls), _llm_response(text="done")]
    )
    tool = _EchoTool()
    agent = ToolCallingAgent(llm_provider=provider, max_tool_calls_per_turn=2)

    response = await agent.run(_request(), tools=(tool,))

    assert response.text == "done"
    assert [call["value"] for call in tool.calls] == ["0", "1"]
    results = provider.requests[1].tool_results
    assert [result.tool_call_id for result in results] == ["call-0", "call-1", "call-2"]
    assert "was not run" in results[2].content


async def test_a_long_tool_result_is_cut_before_the_model_reads_it() -> None:
    provider = FakeLlmProvider(
        responses=[
            _llm_response(tool_calls=(LlmToolCall(id="c", name="echo_tool", arguments={}),)),
            _llm_response(text="done"),
        ]
    )
    agent = ToolCallingAgent(llm_provider=provider, max_tool_result_chars=50)

    await agent.run(_request(), tools=(_EchoTool(output="y" * 500),))

    content = provider.requests[1].tool_results[0].content
    assert content.startswith("y" * 50)
    assert "y" * 51 not in content
    assert "cut" in content


# --- Model-drafted words sent to people ---------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Log in at https://evil.example/login to confirm",
        "See www.evil.example now",
        "Click <https://evil.example|your Jira ticket>",
        "Hey <!channel> look",
        "Ask <@U123> about it",
    ],
)
def test_links_and_chat_markup_are_found(text: str) -> None:
    assert carries_link_or_markup(text)


def test_plain_words_are_not_flagged() -> None:
    assert not carries_link_or_markup("What is your ETA for CHK-4? Is it 3 < 5 days?")


def test_scrubbing_takes_links_and_markup_out_and_fits_the_text() -> None:
    text = "Please review <https://evil.example|MR 12> and\nhttps://evil.example/x <!here>"

    assert scrubbed(text, max_chars=200) == f"Please review {LINK_REMOVED} and {LINK_REMOVED}"
    assert scrubbed("a" * 30, max_chars=10) == "a" * 9 + "…"


@pytest.mark.parametrize(
    "question",
    [
        "Could you confirm at https://evil.example/verify?",
        "Could you ping <!channel> about CHK-4?",
        "Why? " * 100,
    ],
)
async def test_a_drafted_follow_up_with_a_link_markup_or_no_end_is_replaced(
    question: str,
) -> None:
    provider = FakeLlmProvider(
        responses=[_llm_response(text=f'{{"sufficient": false, "question": "{question}"}}')]
    )

    decision = await ClarificationEvaluator(provider, model="test-model").evaluate(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Working on CHK-4, nearly there.",
        correlation_id="corr-1",
    )

    assert decision.question == GENERIC_CLARIFICATION_QUESTION


async def test_a_plain_drafted_follow_up_is_sent_as_drafted() -> None:
    provider = FakeLlmProvider(
        responses=[
            _llm_response(text='{"sufficient": false, "question": "What is your ETA\\nfor CHK-4?"}')
        ]
    )

    decision = await ClarificationEvaluator(provider, model="test-model").evaluate(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Working on CHK-4, nearly there.",
        correlation_id="corr-1",
    )

    assert decision.question == "What is your ETA for CHK-4?"


async def test_slack_never_posts_a_broadcast_mention() -> None:
    client = _RecordingSlackClient()
    adapter = SlackChatAdapter(
        tenant_id="demo", http_client=client, rate_limiter=InMemoryRateLimiter()
    )

    await adapter.send_dm(
        ChatUserRef(tenant_id="demo", external_id="U1"),
        _message("CHK-4 <!channel> title, <!here|here>, <@U2> and <https://jira.example|CHK-4>"),
    )

    assert client.messages == [
        ("C-U1", "CHK-4 @channel title, here, <@U2> and <https://jira.example|CHK-4>")
    ]


# --- How many messages one person gets ----------------------------------------


async def test_a_recipient_gets_no_more_than_the_days_limit_of_dms() -> None:
    client = _RecordingSlackClient()
    now = [TODAY]
    adapter = SlackChatAdapter(
        tenant_id="demo",
        http_client=client,
        rate_limiter=InMemoryRateLimiter(),
        send_cap=InMemoryDailySendCap(limit=2, clock=lambda: now[0]),
    )
    alice = ChatUserRef(tenant_id="demo", external_id="U1")
    bob = ChatUserRef(tenant_id="demo", external_id="U2")

    await adapter.send_dm(alice, _message("one", key="k1"))
    # A retry of a sent message returns its id and is not counted again.
    await adapter.send_dm(alice, _message("one", key="k1"))
    await adapter.send_dm(alice, _message("two", key="k2"))
    with pytest.raises(OutboundLimitReached):
        await adapter.send_dm(alice, _message("three", key="k3"))
    await adapter.send_dm(bob, _message("one", key="k4"))
    now[0] = TOMORROW
    await adapter.send_dm(alice, _message("three", key="k3"))

    assert [text for _, text in client.messages] == ["one", "two", "one", "three"]


async def test_a_limit_of_zero_counts_nothing() -> None:
    cap = InMemoryDailySendCap(limit=0)

    for _ in range(500):
        await cap.take("demo", "U1")


async def test_the_redis_cap_is_shared_and_expires_after_the_day() -> None:
    redis = _FakeRedis()
    cap = RedisDailySendCap(client=redis, limit=1, clock=lambda: TODAY)  # type: ignore[arg-type]

    await cap.take("demo", "U1")
    with pytest.raises(OutboundLimitReached):
        await cap.take("demo", "U1")

    assert redis.ttl["openprogram:chat:daily-dms:demo:U1:2026-10-09"] == 2 * 86_400


# --- The API ------------------------------------------------------------------


def test_an_admin_cannot_dispatch_work_for_another_tenant(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"workflow_provider": "fake"}))
    with TestClient(app) as client:
        client.post("/config/members", json={"id": "dev-1", "name": "Dev One"})
        responses = [
            client.post(
                "/admin/workflows/checkin/dispatch",
                json={"tenant_id": "other", "developer_id": "dev-1"},
            ),
            client.post(
                "/admin/workflows/sync/jira", json={"tenant_id": "other", "project_key": "P"}
            ),
            client.post(
                "/admin/workflows/sync/github", json={"tenant_id": "other", "repo_name": "a/b"}
            ),
            client.post(
                "/admin/workflows/sync/calendar",
                json={
                    "tenant_id": "other",
                    "user_id": "dev-1",
                    "start": "2026-01-10",
                    "end": "2026-01-11",
                },
            ),
        ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]
    assert "another tenant" in responses[0].json()["detail"]


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(LlmBudgetExhausted("spent"), 503), (LlmRequestTooLarge("big"), 413)],
)
def test_ask_answers_a_refused_model_call_with_a_clear_status(
    settings: Settings, error: Exception, status_code: int
) -> None:
    app = create_app(settings=settings)
    app.dependency_overrides[get_ask_service] = lambda: _RefusingAskService(error)
    with TestClient(app) as client:
        response = client.post("/ask", json={"question": "How is the team doing?"})

    assert response.status_code == status_code


# --- Helpers ------------------------------------------------------------------


def _llm_response(text: str = "", tool_calls: tuple[LlmToolCall, ...] = ()) -> LlmResponse:
    return LlmResponse(
        tenant_id="demo",
        text=text,
        model="test-model",
        usage=TokenUsage(
            prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0.0, latency_ms=1.0
        ),
        trace_id="trace-test",
        tool_calls=tool_calls,
    )


def _message(text: str, key: str | None = None) -> OutboundMessage:
    metadata: dict[str, JsonScalar] = {"idempotency_key": key} if key else {}
    return OutboundMessage(tenant_id="demo", text=text, correlation_id="corr", metadata=metadata)


@dataclass
class _EchoTool:
    output: str | None = None
    calls: list[dict[str, JsonScalar]] = field(default_factory=list)
    name: str = "echo_tool"
    description: str = "Echo the value argument."
    parameters: dict[str, object] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        self.calls.append(dict(arguments))
        return self.output if self.output is not None else f"echo: {arguments.get('value')}"


@dataclass
class _RecordingSlackClient:
    messages: list[tuple[str, str]] = field(default_factory=list)

    async def open_conversation(self, user_id: str) -> str:
        return f"C-{user_id}"

    async def post_message(self, channel_id: str, text: str) -> str:
        self.messages.append((channel_id, text))
        return f"ts-{len(self.messages)}"

    async def latest_reply(self, thread_id: str) -> Mapping[str, object] | None:
        return None

    async def list_users(self, cursor: str | None = None) -> Mapping[str, object]:
        return {}


@dataclass
class _FakeRedis:
    values: dict[str, int] = field(default_factory=dict)
    ttl: dict[str, int] = field(default_factory=dict)

    async def get(self, key: str) -> str | None:
        value = self.values.get(key)
        return None if value is None else str(value)

    async def incr(self, key: str) -> int:
        return await self.incrby(key, 1)

    async def incrby(self, key: str, amount: int) -> int:
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttl[key] = seconds
        return True


@dataclass
class _RefusingAskService:
    error: Exception

    async def ask(self, **_: object) -> object:
        raise self.error
