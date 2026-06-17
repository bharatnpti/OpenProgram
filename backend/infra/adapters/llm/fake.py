from __future__ import annotations

from dataclasses import dataclass, field

from core.domain.llm import LlmRequest, LlmResponse, TokenUsage


@dataclass
class FakeLlmProvider:
    requests: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        text = (
            '{"progress_note":"Status update received",'
            '"blockers":[],"eta_change_days":null,"mood":"neutral"}'
            if request.metadata.get("purpose") == "parse_checkin_signals"
            else f"summary: {request.prompt[:24]}"
        )
        return LlmResponse(
            tenant_id=request.tenant_id,
            text=text,
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
