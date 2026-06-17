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
class LlmRequest:
    tenant_id: str
    prompt: str
    model: str
    correlation_id: str
    system: str | None = None
    messages: tuple[LlmMessage, ...] = ()
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
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)
