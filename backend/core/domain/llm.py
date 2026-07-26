from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from core.domain.graph import JsonScalar

type LlmMessageRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True, kw_only=True)
class LlmMessage:
    role: LlmMessageRole
    content: str


@dataclass(frozen=True, kw_only=True)
class LlmTool:
    name: str
    description: str
    parameters: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class LlmToolCall:
    id: str
    name: str
    arguments: Mapping[str, JsonScalar] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class LlmToolResult:
    tool_call_id: str
    content: str


@dataclass(frozen=True, kw_only=True)
class LlmRequest:
    tenant_id: str
    prompt: str
    model: str
    correlation_id: str
    system: str | None = None
    messages: tuple[LlmMessage, ...] = ()
    tools: tuple[LlmTool, ...] = ()
    # Prior assistant tool-call turns carried through multi-turn tool loops.
    tool_calls: tuple[LlmToolCall, ...] = ()
    tool_results: tuple[LlmToolResult, ...] = ()
    # Request provider-enforced JSON output (response_format=json_object) plus
    # deterministic decoding settings for structured parse/clarification calls.
    json_mode: bool = False
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: float


@dataclass(frozen=True, kw_only=True)
class LlmResponse:
    tenant_id: str
    text: str
    model: str
    usage: TokenUsage
    trace_id: str
    tool_calls: tuple[LlmToolCall, ...] = ()
    finish_reason: str | None = None
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)
