from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.domain.graph import JsonScalar


@dataclass(frozen=True, kw_only=True)
class LlmRequest:
    tenant_id: str
    prompt: str
    model: str
    correlation_id: str
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
