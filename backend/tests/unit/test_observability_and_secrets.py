from __future__ import annotations

import json

import httpx
import respx
from cryptography.fernet import Fernet

from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.ports.secrets import SecretRef
from infra.adapters.llm.litellm_provider import LangfuseTraceSink
from infra.adapters.secrets.encrypted import FernetSecretStore, InMemoryEncryptedSecretRecordStore
from infra.observability.logging import inject_correlation_id, redact_sensitive
from infra.observability.tracing import correlation_scope


def test_redaction_processor_redacts_raw_reply_keys_only() -> None:
    event = redact_sensitive(
        None,
        "info",
        {
            "raw_reply": "raw private reply",
            "dm_text": "raw outbound dm",
            "reply_text": "reply body",
            "message": "kept",
            "token": "secret-token",
            "safe": "kept",
        },
    )
    assert event == {
        "raw_reply": "[redacted]",
        "dm_text": "[redacted]",
        "reply_text": "[redacted]",
        "message": "kept",
        "token": "secret-token",
        "safe": "kept",
    }


async def test_logging_injects_correlation_id() -> None:
    async with correlation_scope("corr-log"):
        event = inject_correlation_id(None, "info", {"event": "hello"})
    assert event["correlation_id"] == "corr-log"


async def test_secret_store_encrypts_records() -> None:
    record_store = InMemoryEncryptedSecretRecordStore()
    secret_store = FernetSecretStore(Fernet(Fernet.generate_key()), record_store)
    ref = SecretRef(tenant_id="demo", connector="chat", key="bot_token")
    await secret_store.put(ref, "plain-value")
    assert await secret_store.get(ref) == "plain-value"
    ciphertext = await record_store.get_ciphertext(ref)
    assert b"plain-value" not in ciphertext


@respx.mock
async def test_langfuse_trace_sink_records_llm_payloads() -> None:
    route = respx.post("https://langfuse.test/api/public/ingestion").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    sink = LangfuseTraceSink(
        host="https://langfuse.test",
        public_key="pk-test",
        secret_key="sk-test",
    )

    await sink.record(
        LlmRequest(
            tenant_id="demo",
            prompt="Reply:\nraw private reply about blockers",
            model="test-model",
            correlation_id="corr-1",
            metadata={
                "service": "status_parser",
            },
        ),
        LlmResponse(
            tenant_id="demo",
            text='{"progress_note":"raw private reply about blockers"}',
            model="test-model",
            usage=TokenUsage(
                prompt_tokens=5,
                completion_tokens=7,
                total_tokens=12,
                cost_usd=0.0,
                latency_ms=15.0,
            ),
            trace_id="trace-1",
        ),
    )

    payload = json.loads(route.calls[0].request.content)
    serialized = json.dumps(payload)
    assert "raw private reply about blockers" in serialized
    generation = payload["batch"][1]["body"]
    assert generation["input"] == [
        {"role": "user", "content": "Reply:\nraw private reply about blockers"}
    ]
    assert generation["output"] == '{"progress_note":"raw private reply about blockers"}'
    assert generation["metadata"]["prompt_sha256"]
    assert generation["metadata"]["output_sha256"]
