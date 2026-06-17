from __future__ import annotations

import json

import httpx
import respx

from core.domain.llm import LlmMessage, LlmRequest
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
