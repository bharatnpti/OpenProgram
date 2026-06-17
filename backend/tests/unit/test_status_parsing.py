from __future__ import annotations

from dataclasses import dataclass, field

from core.application.status_parsing import StatusParser
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.status import CheckInSignals, Mood


@dataclass
class CapturingLlmProvider:
    text: str
    requests: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            tenant_id=request.tenant_id,
            text=self.text,
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost_usd=0.0,
                latency_ms=1.0,
            ),
            trace_id="trace-parser",
        )


async def test_status_parser_converts_valid_json_to_signals() -> None:
    provider = CapturingLlmProvider(
        text=(
            '{"progress_note":"API handoff is ready",'
            '"blockers":["schema review"],"eta_change_days":2,"mood":"negative"}'
        )
    )
    parser = StatusParser(provider, model="test-model")

    signals = await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="raw reply with private detail",
        correlation_id="corr-1",
    )

    assert signals == CheckInSignals(
        progress_note="API handoff is ready",
        blockers=("schema review",),
        eta_change_days=2,
        mood=Mood.NEGATIVE,
    )
    assert provider.requests[0].metadata == {
        "service": "status_parser",
        "purpose": "parse_checkin_signals",
        "developer_id": "dev-1",
        "redact_input": True,
        "redact_output": True,
    }


async def test_status_parser_falls_back_to_raw_reply_when_output_is_malformed() -> None:
    provider = CapturingLlmProvider(text="not valid structured output")
    parser = StatusParser(provider, model="test-model")

    signals = await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Finished the cache work; waiting on review.",
        correlation_id="corr-1",
    )

    assert signals == CheckInSignals(
        progress_note="Finished the cache work; waiting on review.",
    )
