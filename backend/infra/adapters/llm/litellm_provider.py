from __future__ import annotations

import asyncio
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
from core.domain.llm import (
    LlmRequest,
    LlmResponse,
    LlmTool,
    LlmToolCall,
    LlmTurn,
    TokenUsage,
)

_logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer("openprogram.adapters.llm.litellm")

# Deterministic decoding settings applied to JSON-mode (structured parse/clarify)
# calls so a garbled reply cannot depend on sampling temperature or run unbounded.
# Note this rules out reasoning models for JSON mode: they accept only their
# default temperature, which would make a status parse non-reproducible.
_JSON_MODE_TEMPERATURE = 0.0
_JSON_MODE_MAX_TOKENS = 1024
_RESPONSE_COST_HEADER = "x-litellm-response-cost"

# A completion once stalled ~60 s on a hung connection before an
# httpx.ConnectError ended it and a workflow step retried it. Connecting is
# now bounded at 5 s, reading, writing and the pool keep the 30 s they had,
# and a request that got no response at all is tried again, briefly, before
# its error is raised.
_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (0.5, 1.0)
# Failures that leave no response behind: no connection, or no answer in
# time. A read timeout may repeat a completion the endpoint was still
# working on, which costs tokens but runs nothing. RemoteProtocolError stays
# out, as it is also raised for a response cut off midway, and an HTTP error
# status is the endpoint's answer: neither is retried.
_RETRYABLE_ERRORS: tuple[type[httpx.TransportError], ...] = (
    httpx.ConnectError,
    httpx.TimeoutException,
)
# Module-level so tests can skip the backoff.
_sleep = asyncio.sleep

# Trace-export failures are almost always one standing condition (the sink is
# not running, or credentials are wrong), so the same warning would otherwise
# repeat once per LLM call. Report each distinct cause once per process and
# count the rest, which keeps the signal without burying real output.
_reported_trace_failures: set[tuple[str, str]] = set()


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
                # Trace export is best effort: losing a trace must never fail
                # the LLM call. Reported without a stack -- the traceback is
                # always the same transport chain -- and only the first time
                # each cause is seen.
                cause = (self.host, f"{type(exc).__name__}: {exc}")
                if cause not in _reported_trace_failures:
                    _reported_trace_failures.add(cause)
                    _logger.warning(
                        "langfuse_trace_record_failed",
                        error_type=type(exc).__name__,
                        error=str(exc),
                        host=self.host,
                        trace_id=trace_id,
                        note="further failures with this cause are not logged",
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
            # Tenant and correlation travel on the OTel span and the
            # Langfuse trace, not in the request body: OpenAI rejects a
            # top-level `metadata` unless `store` is enabled, and storing
            # completions would leave check-in text with the provider --
            # a retention decision that is not this adapter's to make.
            body: dict[str, object] = {
                "model": request.model,
                "messages": _chat_messages(request),
            }
            if request.tools:
                body["tools"] = [_tool_payload(tool) for tool in request.tools]
            if request.json_mode:
                body["response_format"] = {"type": "json_object"}
                body["temperature"] = _JSON_MODE_TEMPERATURE
                # `max_completion_tokens` is the field current chat models
                # accept; `max_tokens` is refused outright by newer ones.
                body["max_completion_tokens"] = _JSON_MODE_MAX_TOKENS
            http_response = await self._post_completion(headers, body)
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

    async def _post_completion(
        self, headers: Mapping[str, str], body: Mapping[str, object]
    ) -> httpx.Response:
        """POST the completion, again after a failure that left no response.

        Only this one HTTP request is retried, so a retry repeats nothing
        else: tools run in the caller's tool loop once a response is back,
        and the trace sink records each successful response once. A response
        with an error status is raised as it came. When the last attempt
        fails too, its own error is raised, so a workflow step's retry still
        sees what it always did.
        """
        attempt = 1
        async with httpx.AsyncClient(base_url=self.base_url, timeout=_REQUEST_TIMEOUT) as client:
            while True:
                try:
                    response = await client.post(
                        "/v1/chat/completions",
                        headers=headers,
                        json=body,
                    )
                except _RETRYABLE_ERRORS as exc:
                    if attempt >= _MAX_ATTEMPTS:
                        raise
                    backoff = _RETRY_BACKOFF_SECONDS[attempt - 1]
                    # The error type only: the body carries check-in text.
                    _logger.warning(
                        "llm_request_retry",
                        attempt=attempt,
                        max_attempts=_MAX_ATTEMPTS,
                        error_type=type(exc).__name__,
                        backoff_seconds=backoff,
                    )
                    await _sleep(backoff)
                    attempt += 1
                    continue
                response.raise_for_status()
                return response


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
    if request.turns:
        messages.extend(_turn_payload(turn) for turn in request.turns)
        return messages
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


def _turn_payload(turn: LlmTurn) -> dict[str, object]:
    if turn.role == "tool":
        return {"role": "tool", "tool_call_id": turn.tool_call_id or "", "content": turn.content}
    payload: dict[str, object] = {"role": turn.role, "content": turn.content or None}
    if turn.tool_calls:
        payload["tool_calls"] = [_tool_call_payload(call) for call in turn.tool_calls]
    elif payload["content"] is None:
        payload["content"] = ""
    return payload


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
    arguments = tool_call.arguments_json or json.dumps(dict(tool_call.arguments), sort_keys=True)
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {"name": tool_call.name, "arguments": arguments},
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
        raw_arguments = function.get("arguments")
        tool_calls.append(
            LlmToolCall(
                id=raw_id if isinstance(raw_id, str) and raw_id else f"tool-call-{index}",
                name=name,
                arguments=_arguments_from_json(raw_arguments),
                arguments_json=_arguments_text(raw_arguments),
            )
        )
    return tuple(tool_calls)


def _arguments_text(value: object) -> str | None:
    """The arguments as the model sent them: its JSON string, or the object as JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return json.dumps(dict(value), sort_keys=True)
    return None


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
