from __future__ import annotations

import httpx
import respx

from core.domain.llm import LlmRequest
from infra.adapters.llm.litellm_provider import LangfuseTraceSink, LiteLlmProvider


@respx.mock
async def test_litellm_response_survives_langfuse_outage() -> None:
    respx.post("https://litellm.test/v1/chat/completions").mock(
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
