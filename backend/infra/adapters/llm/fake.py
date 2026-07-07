from __future__ import annotations

from dataclasses import dataclass, field

from core.domain.llm import LlmRequest, LlmResponse, TokenUsage


@dataclass
class FakeLlmProvider:
    responses: list[LlmResponse] = field(default_factory=list)
    requests: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        purpose = request.metadata.get("purpose")
        if purpose == "parse_checkin_signals":
            text = '{"progress_note":"Status update received","blockers":[],"eta_change_days":null}'
        elif purpose == "evaluate_checkin_clarification":
            text = (
                '{"sufficient":true,"question":null,'
                '"signals":{"progress_note":"Status update received",'
                '"blockers":[],"eta_change_days":null}}'
            )
        else:
            text = f"summary: {request.prompt[:24]}"
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
