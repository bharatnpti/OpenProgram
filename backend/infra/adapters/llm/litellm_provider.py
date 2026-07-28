from __future__ import annotations

import json
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

from core.domain.graph import JsonScalar
from core.domain.llm import LlmRequest, LlmResponse, LlmTool, LlmToolCall, TokenUsage

_logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer("openprogram.adapters.llm.litellm")

# Deterministic decoding settings applied to JSON-mode (structured parse/clarify)
# calls so a garbled reply cannot depend on sampling temperature or run unbounded.
_JSON_MODE_TEMPERATURE = 0.0
_JSON_MODE_MAX_TOKENS = 1024
_RESPONSE_COST_HEADER = "x-litellm-response-cost"


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
            trace_input = _trace_input(request)
            prompt_hash = sha256(
                json.dumps(trace_input, sort_keys=True).encode("utf-8")
            ).hexdigest()
            output_hash = sha256(response.text.encode("utf-8")).hexdigest()
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
                            "name": "openprogram.status_agent",
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
                            "output": response.text,
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
            span.set_attribute("openprogram.tenant_id", request.tenant_id)
            started = perf_counter()
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
                body: dict[str, object] = {
                    "model": request.model,
                    "messages": _chat_messages(request),
                    "metadata": {
                        "tenant_id": request.tenant_id,
                        "correlation_id": request.correlation_id,
                    },
                }
                if request.tools:
                    body["tools"] = [_tool_payload(tool) for tool in request.tools]
                if request.json_mode:
                    body["response_format"] = {"type": "json_object"}
                    body["temperature"] = _JSON_MODE_TEMPERATURE
                    body["max_tokens"] = _JSON_MODE_MAX_TOKENS
                http_response = await client.post(
                    "/v1/chat/completions",
                    headers=headers,
                    json=body,
                )
                http_response.raise_for_status()
                payload = http_response.json()
            latency_ms = (perf_counter() - started) * 1000
            cost_usd = _response_cost(http_response.headers, payload)
            response = _response_from_payload(request, payload, latency_ms, cost_usd)
            trace_id = await self.trace_sink.record(request, response)
            return LlmResponse(
                tenant_id=response.tenant_id,
                text=response.text,
                model=response.model,
                usage=response.usage,
                trace_id=trace_id,
                tool_calls=response.tool_calls,
                finish_reason=response.finish_reason,
                metadata=response.metadata,
            )


def _response_from_payload(
    request: LlmRequest, payload: Mapping[str, object], latency_ms: float, cost_usd: float = 0.0
) -> LlmResponse:
    choices = payload.get("choices")
    text = ""
    tool_calls: tuple[LlmToolCall, ...] = ()
    finish_reason: str | None = None
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            raw_finish_reason = first.get("finish_reason")
            finish_reason = raw_finish_reason if isinstance(raw_finish_reason, str) else None
            message = first.get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
                text = content if isinstance(content, str) else ""
                tool_calls = _tool_calls_from_message(message)

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
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        ),
        trace_id=str(payload.get("id") or uuid4()),
        tool_calls=tool_calls,
        finish_reason=finish_reason,
    )


def _int_field(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) else 0


def _response_cost(headers: httpx.Headers, payload: Mapping[str, object]) -> float:
    """Capture LiteLLM's computed request cost so Langfuse traces are meaningful.

    LiteLLM surfaces the cost in the ``x-litellm-response-cost`` header; some
    deployments also inline it on the usage or response body. Falls back to 0.0.
    """
    header = _parse_float(headers.get(_RESPONSE_COST_HEADER))
    if header is not None:
        return header
    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        usage_cost = _parse_float(usage.get("cost"))
        if usage_cost is not None:
            return usage_cost
    body_cost = _parse_float(payload.get("response_cost"))
    return body_cost if body_cost is not None else 0.0


def _parse_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _chat_messages(request: LlmRequest) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    if request.system is not None:
        messages.append({"role": "system", "content": request.system})
    messages.extend(
        {"role": message.role, "content": message.content} for message in request.messages
    )
    if request.prompt:
        messages.append({"role": "user", "content": request.prompt})
    if request.tool_calls:
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [_tool_call_payload(tool_call) for tool_call in request.tool_calls],
            }
        )
    messages.extend(
        {
            "role": "tool",
            "tool_call_id": result.tool_call_id,
            "content": result.content,
        }
        for result in request.tool_results
    )
    return messages


def _trace_input(request: LlmRequest) -> list[dict[str, object]]:
    # Langfuse is the intentional exception to app-log redaction: LLM prompts stay inspectable.
    return _chat_messages(request)


def _tool_payload(tool: LlmTool) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters),
        },
    }


def _tool_call_payload(tool_call: LlmToolCall) -> dict[str, object]:
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.name,
            "arguments": json.dumps(dict(tool_call.arguments), sort_keys=True),
        },
    }


def _tool_calls_from_message(message: Mapping[str, object]) -> tuple[LlmToolCall, ...]:
    raw_tool_calls = message.get("tool_calls")
    if not isinstance(raw_tool_calls, list | tuple):
        return ()
    tool_calls: list[LlmToolCall] = []
    for index, raw_tool_call in enumerate(raw_tool_calls):
        if not isinstance(raw_tool_call, Mapping):
            continue
        function = raw_tool_call.get("function")
        if not isinstance(function, Mapping):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw_id = raw_tool_call.get("id")
        tool_calls.append(
            LlmToolCall(
                id=raw_id if isinstance(raw_id, str) and raw_id else f"tool-call-{index}",
                name=name,
                arguments=_arguments_from_json(function.get("arguments")),
            )
        )
    return tuple(tool_calls)


def _arguments_from_json(value: object) -> Mapping[str, JsonScalar]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return _json_scalar_mapping(decoded)
    return _json_scalar_mapping(value)


def _json_scalar_mapping(value: object) -> dict[str, JsonScalar]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, JsonScalar] = {}
    for key, item in value.items():
        if isinstance(key, str) and (item is None or isinstance(item, str | int | float | bool)):
            result[key] = item
    return result
