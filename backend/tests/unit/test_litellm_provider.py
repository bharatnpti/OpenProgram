from __future__ import annotations

import json

import httpx
import respx

from core.domain.llm import (
    LlmMessage,
    LlmRequest,
    LlmResponse,
    LlmTool,
    LlmToolCall,
    LlmToolResult,
    TokenUsage,
)
from infra.adapters.llm.fake import FakeLlmProvider
from infra.adapters.llm.litellm_provider import LangfuseTraceSink, LiteLlmProvider


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
        ),
    )


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
