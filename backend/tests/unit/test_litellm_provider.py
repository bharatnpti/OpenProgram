from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import pytest
import respx

from core.domain.llm import (
    LlmMessage,
    LlmRequest,
    LlmResponse,
    LlmTool,
    LlmToolCall,
    LlmToolResult,
    LlmTurn,
    TokenUsage,
)
from infra.adapters.llm import litellm_provider
from infra.adapters.llm.fake import FakeLlmProvider
from infra.adapters.llm.litellm_provider import (
    LangfuseTraceSink,
    LiteLlmProvider,
    NoopTraceSink,
)


@respx.mock
async def test_litellm_response_survives_langfuse_outage() -> None:
    litellm_route = respx.post("https://litellm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "trace-llm",
                "model": "test-model",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )
    )
    respx.post("https://langfuse.test/api/public/ingestion").mock(
        return_value=httpx.Response(503, json={"status": "down"})
    )
    provider = LiteLlmProvider(
        base_url="https://litellm.test",
        trace_sink=LangfuseTraceSink(
            host="https://langfuse.test",
            public_key="pk-test",
            secret_key="sk-test",
        ),
    )

    response = await provider.complete(
        LlmRequest(
            tenant_id="demo",
            prompt="hello",
            model="test-model",
            correlation_id="corr-1",
        )
    )

    assert response.text == "ok"
    assert response.trace_id == "trace-llm"
    assert json.loads(litellm_route.calls[0].request.content)["messages"] == [
        {"role": "user", "content": "hello"}
    ]


@respx.mock
async def test_litellm_sends_system_history_then_prompt() -> None:
    route = respx.post("https://litellm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "trace-llm",
                "model": "test-model",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )
    )
    provider = LiteLlmProvider(
        base_url="https://litellm.test",
        trace_sink=LangfuseTraceSink(
            host="https://langfuse.test",
            public_key="pk-test",
            secret_key="sk-test",
        ),
    )
    respx.post("https://langfuse.test/api/public/ingestion").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await provider.complete(
        LlmRequest(
            tenant_id="demo",
            prompt="What changed?",
            model="test-model",
            correlation_id="corr-1",
            system="You are concise.",
            messages=(
                LlmMessage(role="user", content="Yesterday I was blocked."),
                LlmMessage(role="assistant", content="Noted."),
            ),
        )
    )

    assert json.loads(route.calls[0].request.content)["messages"] == [
        {"role": "system", "content": "You are concise."},
        {"role": "user", "content": "Yesterday I was blocked."},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "What changed?"},
    ]


@respx.mock
async def test_litellm_json_mode_sends_response_format_and_deterministic_settings() -> None:
    route = respx.post("https://litellm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "trace-llm",
                "model": "test-model",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    )
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=NoopTraceSink())

    await provider.complete(
        LlmRequest(
            tenant_id="demo",
            prompt="return status json",
            model="test-model",
            correlation_id="corr-1",
            json_mode=True,
        )
    )

    body = json.loads(route.calls[0].request.content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.0
    assert body["max_completion_tokens"] > 0
    assert "max_tokens" not in body


@respx.mock
async def test_litellm_sends_no_top_level_metadata() -> None:
    """A plain OpenAI-compatible endpoint refuses `metadata` without `store`.

    Tenant and correlation are carried on the span and the trace instead, so
    the adapter works against a provider directly and not only through a
    gateway that tolerates extra fields.
    """
    route = respx.post("https://litellm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "trace-llm",
                "model": "test-model",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    )
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=NoopTraceSink())

    await provider.complete(
        LlmRequest(tenant_id="demo", prompt="hi", model="test-model", correlation_id="corr-1")
    )

    body = json.loads(route.calls[0].request.content)
    assert "metadata" not in body
    assert set(body) == {"model", "messages"}


@respx.mock
async def test_litellm_omits_json_mode_settings_by_default() -> None:
    route = respx.post("https://litellm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "trace-llm",
                "model": "test-model",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    )
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=NoopTraceSink())

    await provider.complete(
        LlmRequest(tenant_id="demo", prompt="hi", model="test-model", correlation_id="corr-1")
    )

    body = json.loads(route.calls[0].request.content)
    assert "response_format" not in body
    assert "temperature" not in body
    assert "max_tokens" not in body
    assert "max_completion_tokens" not in body


@respx.mock
async def test_litellm_captures_response_cost_from_header() -> None:
    respx.post("https://litellm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"x-litellm-response-cost": "0.001234"},
            json={
                "id": "trace-llm",
                "model": "test-model",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    )
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=NoopTraceSink())

    response = await provider.complete(
        LlmRequest(tenant_id="demo", prompt="hi", model="test-model", correlation_id="corr-1")
    )

    assert response.usage.cost_usd == 0.001234


async def test_fake_llm_provider_captures_multi_turn_request() -> None:
    provider = FakeLlmProvider()
    request = LlmRequest(
        tenant_id="demo",
        prompt="Summarize",
        model="test-model",
        correlation_id="corr-1",
        system="Use the history.",
        messages=(LlmMessage(role="user", content="Progress: API done."),),
    )

    response = await provider.complete(request)

    assert response.text == "summary: Summarize"
    assert provider.requests == [request]


@respx.mock
async def test_litellm_sends_tools_and_parses_tool_calls() -> None:
    route = respx.post("https://litellm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "trace-llm",
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "fetch_conversation_history",
                                        "arguments": '{"since_days": 7, "limit": 3}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )
    )
    respx.post("https://langfuse.test/api/public/ingestion").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    provider = LiteLlmProvider(
        base_url="https://litellm.test",
        trace_sink=LangfuseTraceSink(
            host="https://langfuse.test",
            public_key="pk-test",
            secret_key="sk-test",
        ),
    )

    response = await provider.complete(
        LlmRequest(
            tenant_id="demo",
            prompt="Need more context?",
            model="test-model",
            correlation_id="corr-1",
            tools=(
                LlmTool(
                    name="fetch_conversation_history",
                    description="Fetch history",
                    parameters={"type": "object", "properties": {}},
                ),
            ),
            tool_calls=(
                LlmToolCall(
                    id="prior-call",
                    name="fetch_conversation_history",
                    arguments={"limit": 1},
                ),
            ),
            tool_results=(LlmToolResult(tool_call_id="prior-call", content="prior result"),),
        )
    )

    body = json.loads(route.calls[0].request.content)
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "fetch_conversation_history",
                "description": "Fetch history",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert body["messages"][-2]["tool_calls"][0]["id"] == "prior-call"
    assert body["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "prior-call",
        "content": "prior result",
    }
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls == (
        LlmToolCall(
            id="call-1",
            name="fetch_conversation_history",
            arguments={"since_days": 7, "limit": 3},
            arguments_json='{"since_days": 7, "limit": 3}',
        ),
    )


@respx.mock
async def test_litellm_keeps_list_arguments_as_sent_and_sends_them_back() -> None:
    """A to-do list is a list of objects: the scalar arguments drop it, the JSON keeps it."""
    todos = '{"todos": [{"content": "Check blockers", "status": "pending"}]}'
    route = respx.post("https://litellm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "trace-llm",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "write_todos", "arguments": todos},
                                }
                            ],
                        }
                    }
                ],
            },
        )
    )
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=NoopTraceSink())
    request = LlmRequest(tenant_id="demo", prompt="Plan", model="m", correlation_id="c")

    response = await provider.complete(request)
    (call,) = response.tool_calls
    await provider.complete(
        LlmRequest(
            tenant_id="demo",
            prompt="Plan",
            model="m",
            correlation_id="c",
            tool_calls=(call,),
            tool_results=(LlmToolResult(tool_call_id="call-1", content="ok"),),
        )
    )

    assert call.arguments == {}
    assert call.arguments_json == todos
    resent = json.loads(route.calls[1].request.content)["messages"][-2]["tool_calls"][0]
    assert resent["function"]["arguments"] == todos


@respx.mock
async def test_litellm_sends_a_kept_conversation_in_its_own_order() -> None:
    route = respx.post("https://litellm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "t", "choices": [{"message": {}}]})
    )
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=NoopTraceSink())
    call = LlmToolCall(id="c1", name="task", arguments={}, arguments_json='{"d": [1]}')

    await provider.complete(
        LlmRequest(
            tenant_id="demo",
            prompt="never sent",
            model="m",
            correlation_id="c",
            system="Rules.",
            messages=(LlmMessage(role="user", content="never sent either"),),
            turns=(
                LlmTurn(role="user", content="Why?"),
                LlmTurn(role="assistant", tool_calls=(call,)),
                LlmTurn(role="tool", content="notes", tool_call_id="c1"),
                LlmTurn(role="user", content="Answer now."),
            ),
        )
    )

    assert json.loads(route.calls[0].request.content)["messages"] == [
        {"role": "system", "content": "Rules."},
        {"role": "user", "content": "Why?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "task", "arguments": '{"d": [1]}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "notes"},
        {"role": "user", "content": "Answer now."},
    ]


async def test_fake_llm_provider_returns_scripted_responses() -> None:
    scripted = LlmResponse(
        tenant_id="demo",
        text="",
        model="test-model",
        usage=TokenUsage(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            cost_usd=0.0,
            latency_ms=1.0,
        ),
        trace_id="trace-scripted",
        tool_calls=(
            LlmToolCall(
                id="call-1",
                name="fetch_conversation_history",
                arguments={"limit": 1},
            ),
        ),
        finish_reason="tool_calls",
    )
    provider = FakeLlmProvider(responses=[scripted])
    request = LlmRequest(
        tenant_id="demo",
        prompt="Summarize",
        model="test-model",
        correlation_id="corr-1",
    )

    response = await provider.complete(request)

    assert response == scripted
    assert provider.requests == [request]


_LITELLM_COMPLETIONS = "https://litellm.test/v1/chat/completions"
_COMPLETION = {
    "id": "trace-llm",
    "model": "test-model",
    "choices": [{"message": {"content": "ok"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
_PROMPT = "check-in text that must never reach a log"


@dataclass
class _CountingSink:
    recorded: int = 0

    async def record(self, request: LlmRequest, response: LlmResponse) -> str:
        self.recorded += 1
        return response.trace_id


@dataclass
class _RecordingLogger:
    warnings: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def warning(self, event: str, **fields: object) -> None:
        self.warnings.append((event, fields))


@pytest.fixture
def backoffs(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The backoff delays a provider waited, without the waiting."""
    waited: list[float] = []

    async def no_wait(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(litellm_provider, "_sleep", no_wait)
    return waited


@pytest.fixture
def retry_log(monkeypatch: pytest.MonkeyPatch) -> _RecordingLogger:
    logger = _RecordingLogger()
    monkeypatch.setattr(litellm_provider, "_logger", logger)
    return logger


def _retry_request() -> LlmRequest:
    return LlmRequest(tenant_id="demo", prompt=_PROMPT, model="test-model", correlation_id="c-1")


@respx.mock
async def test_litellm_retries_a_failed_connection_then_returns_the_response(
    backoffs: list[float], retry_log: _RecordingLogger
) -> None:
    """A first-wave completion stalled ~60 s on a hung connection until a
    ConnectError, and only a workflow step's retry recovered it."""
    route = respx.post(_LITELLM_COMPLETIONS).mock(
        side_effect=[
            httpx.ConnectError,
            httpx.ConnectError,
            httpx.Response(200, json=_COMPLETION),
        ]
    )
    sink = _CountingSink()
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=sink)

    response = await provider.complete(_retry_request())

    assert response.text == "ok"
    assert route.call_count == 3
    assert backoffs == [0.5, 1.0]
    # Traced once, for the one response.
    assert sink.recorded == 1
    assert [
        (event, fields["attempt"], fields["error_type"]) for event, fields in retry_log.warnings
    ] == [
        ("llm_request_retry", 1, "ConnectError"),
        ("llm_request_retry", 2, "ConnectError"),
    ]
    assert _PROMPT not in repr(retry_log.warnings)


@respx.mock
@pytest.mark.parametrize(
    "timed_out",
    [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout],
)
async def test_litellm_retries_a_request_that_timed_out(
    timed_out: type[httpx.TimeoutException], backoffs: list[float], retry_log: _RecordingLogger
) -> None:
    route = respx.post(_LITELLM_COMPLETIONS).mock(
        side_effect=[timed_out, httpx.Response(200, json=_COMPLETION)]
    )
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=NoopTraceSink())

    response = await provider.complete(_retry_request())

    assert response.text == "ok"
    assert route.call_count == 2
    assert backoffs == [0.5]
    assert retry_log.warnings[0][1]["error_type"] == timed_out.__name__


@respx.mock
async def test_litellm_raises_the_connect_error_after_three_attempts(
    backoffs: list[float], retry_log: _RecordingLogger
) -> None:
    route = respx.post(_LITELLM_COMPLETIONS).mock(side_effect=httpx.ConnectError)
    sink = _CountingSink()
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=sink)

    with pytest.raises(httpx.ConnectError):
        await provider.complete(_retry_request())

    assert route.call_count == 3
    assert backoffs == [0.5, 1.0]
    assert len(retry_log.warnings) == 2
    assert sink.recorded == 0


@respx.mock
async def test_litellm_does_not_retry_an_error_status(
    backoffs: list[float], retry_log: _RecordingLogger
) -> None:
    route = respx.post(_LITELLM_COMPLETIONS).mock(
        return_value=httpx.Response(500, json={"error": "upstream"})
    )
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=NoopTraceSink())

    with pytest.raises(httpx.HTTPStatusError):
        await provider.complete(_retry_request())

    assert route.call_count == 1
    assert backoffs == []
    assert retry_log.warnings == []


@respx.mock
async def test_litellm_bounds_connecting_at_5s_and_keeps_30s_for_the_rest() -> None:
    timeouts: list[object] = []

    def answer(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json=_COMPLETION)

    respx.post(_LITELLM_COMPLETIONS).mock(side_effect=answer)
    provider = LiteLlmProvider(base_url="https://litellm.test", trace_sink=NoopTraceSink())

    await provider.complete(_retry_request())

    assert timeouts == [{"connect": 5.0, "read": 30.0, "write": 30.0, "pool": 30.0}]
