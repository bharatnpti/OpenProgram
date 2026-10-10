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
    # The arguments exactly as the model wrote them, as JSON. ``arguments``
    # keeps only the scalar values; an agent harness whose own tools take lists
    # or objects (a to-do list) reads them from here. None when not known.
    arguments_json: str | None = None


@dataclass(frozen=True, kw_only=True)
class LlmToolResult:
    tool_call_id: str
    content: str


type LlmTurnRole = Literal["user", "assistant", "tool"]


@dataclass(frozen=True, kw_only=True)
class LlmTurn:
    """One message of a conversation that is sent exactly as it went, in order.

    An assistant turn may carry tool calls; a tool turn answers one by id.
    """

    role: LlmTurnRole
    content: str = ""
    tool_calls: tuple[LlmToolCall, ...] = ()
    tool_call_id: str | None = None


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
    # The whole conversation in order, after ``system``, for a caller that keeps
    # its own (an agent harness). When set, ``messages``, ``prompt``,
    # ``tool_calls`` and ``tool_results`` are not sent.
    turns: tuple[LlmTurn, ...] = ()
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
