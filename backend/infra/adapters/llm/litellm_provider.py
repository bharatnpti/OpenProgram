from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from typing import Protocol
from uuid import uuid4

import httpx
import structlog
from opentelemetry import trace

from core.domain.llm import LlmRequest, LlmResponse, TokenUsage

_logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer("pulseops.adapters.llm.litellm")
_REDACTED_INPUT = "[redacted input]"
_REDACTED_OUTPUT = "[redacted output]"


class LlmTraceSink(Protocol):
    async def record(self, request: LlmRequest, response: LlmResponse) -> str: ...


class NoopTraceSink:
    async def record(self, request: LlmRequest, response: LlmResponse) -> str:
        return response.trace_id


@dataclass(frozen=True)
class LangfuseTraceSink:
    host: str
    public_key: str
    secret_key: str

    async def record(self, request: LlmRequest, response: LlmResponse) -> str:
        with _tracer.start_as_current_span("langfuse.record"):
            trace_id = response.trace_id
            timestamp = datetime.now(tz=UTC).isoformat()
            generation_id = f"{trace_id}-generation"
            prompt_hash = sha256(request.prompt.encode("utf-8")).hexdigest()
            output_hash = sha256(response.text.encode("utf-8")).hexdigest()
            trace_input = (
                _REDACTED_INPUT
                if _metadata_flag(request.metadata, "redact_input")
                else request.prompt
            )
            generation_output = (
                _REDACTED_OUTPUT
                if _metadata_flag(request.metadata, "redact_output")
                else response.text
            )
            trace_metadata = {
                "tenant_id": request.tenant_id,
                "correlation_id": request.correlation_id,
                "prompt_sha256": prompt_hash,
                "output_sha256": output_hash,
                **dict(request.metadata),
            }
            payload = {
                "batch": [
                    {
                        "id": f"{trace_id}-trace-create",
                        "type": "trace-create",
                        "timestamp": timestamp,
                        "body": {
                            "id": trace_id,
                            "name": "pulseops.status_agent",
                            "userId": request.tenant_id,
                            "input": trace_input,
                            "metadata": trace_metadata,
                        },
                    },
                    {
                        "id": f"{generation_id}-create",
                        "type": "generation-create",
                        "timestamp": timestamp,
                        "body": {
                            "id": generation_id,
                            "traceId": trace_id,
                            "name": "litellm.complete",
                            "model": response.model,
                            "input": trace_input,
                            "output": generation_output,
                            "usage": {
                                "input": response.usage.prompt_tokens,
                                "output": response.usage.completion_tokens,
                                "total": response.usage.total_tokens,
                                "unit": "TOKENS",
                            },
                            "metadata": {
                                "cost_usd": response.usage.cost_usd,
                                "latency_ms": response.usage.latency_ms,
                                "prompt_sha256": prompt_hash,
                                "output_sha256": output_hash,
                                **dict(request.metadata),
                            },
                        },
                    },
                ]
            }
            try:
                async with httpx.AsyncClient(base_url=self.host, timeout=10.0) as client:
                    result = await client.post(
                        "/api/public/ingestion",
                        auth=(self.public_key, self.secret_key),
                        json=payload,
                    )
                    result.raise_for_status()
            except Exception as exc:
                _logger.warning(
                    "langfuse_trace_record_failed",
                    error_type=type(exc).__name__,
                    trace_id=trace_id,
                    exc_info=True,
                )
            return trace_id


@dataclass(frozen=True)
class LiteLlmProvider:
    base_url: str
    trace_sink: LlmTraceSink
    api_key: str | None = None

    async def complete(self, request: LlmRequest) -> LlmResponse:
        with _tracer.start_as_current_span("litellm.complete") as span:
            span.set_attribute("llm.model", request.model)
            span.set_attribute("pulseops.tenant_id", request.tenant_id)
            started = perf_counter()
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
                http_response = await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": request.model,
                        "messages": [{"role": "user", "content": request.prompt}],
                        "metadata": {
                            "tenant_id": request.tenant_id,
                            "correlation_id": request.correlation_id,
                        },
                    },
                )
                http_response.raise_for_status()
                payload = http_response.json()
            latency_ms = (perf_counter() - started) * 1000
            response = _response_from_payload(request, payload, latency_ms)
            trace_id = await self.trace_sink.record(request, response)
            return LlmResponse(
                tenant_id=response.tenant_id,
                text=response.text,
                model=response.model,
                usage=response.usage,
                trace_id=trace_id,
                metadata=response.metadata,
            )


def _response_from_payload(
    request: LlmRequest, payload: Mapping[str, object], latency_ms: float
) -> LlmResponse:
    choices = payload.get("choices")
    text = ""
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
                text = content if isinstance(content, str) else ""

    usage_payload = payload.get("usage")
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    if isinstance(usage_payload, Mapping):
        prompt_tokens = _int_field(usage_payload, "prompt_tokens")
        completion_tokens = _int_field(usage_payload, "completion_tokens")
        total_tokens = _int_field(usage_payload, "total_tokens")

    model = payload.get("model")
    return LlmResponse(
        tenant_id=request.tenant_id,
        text=text,
        model=model if isinstance(model, str) else request.model,
        usage=TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=0.0,
            latency_ms=latency_ms,
        ),
        trace_id=str(payload.get("id") or uuid4()),
    )


def _int_field(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) else 0


def _metadata_flag(metadata: Mapping[str, object], key: str) -> bool:
    value = metadata.get(key)
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.lower() == "true"
