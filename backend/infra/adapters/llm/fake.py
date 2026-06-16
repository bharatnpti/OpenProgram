from __future__ import annotations

from core.domain.llm import LlmRequest, LlmResponse, TokenUsage


class FakeLlmProvider:
    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            tenant_id=request.tenant_id,
            text=f"summary: {request.prompt[:24]}",
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=5,
                completion_tokens=7,
                total_tokens=12,
                cost_usd=0.0,
                latency_ms=1.0,
            ),
            trace_id="trace-fake",
            metadata={"correlation_id": request.correlation_id},
        )
