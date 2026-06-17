from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from core.application.status_parsing import (
    ClarificationDecision,
    ClarificationEvaluator,
    StatusParser,
)
from core.domain.conversation import ConversationRole, ConversationTurn
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
        conversation_turns=(
            ConversationTurn(
                tenant_id="demo",
                developer_id="dev-1",
                conversation_id="corr-1",
                conversation_date=date(2026, 1, 10),
                role=ConversationRole.SYSTEM,
                content="Status check-in conversation context.",
                correlation_id="corr-1",
                chat_message_id="msg-system",
                observed_at=datetime(2026, 1, 10, 8, 59, tzinfo=UTC),
            ),
            ConversationTurn(
                tenant_id="demo",
                developer_id="dev-1",
                conversation_id="corr-1",
                conversation_date=date(2026, 1, 10),
                role=ConversationRole.AGENT,
                content="Can you share progress and blockers?",
                correlation_id="corr-1",
                chat_message_id="msg-agent",
                observed_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            ),
            ConversationTurn(
                tenant_id="demo",
                developer_id="dev-1",
                conversation_id="corr-1",
                conversation_date=date(2026, 1, 10),
                role=ConversationRole.USER,
                content="I am finishing the API handoff.",
                correlation_id="corr-1",
                chat_message_id="msg-user",
                observed_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
            ),
        ),
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
    }
    assert provider.requests[0].system is not None
    assert [(message.role, message.content) for message in provider.requests[0].messages] == [
        ("system", "Status check-in conversation context."),
        ("assistant", "Can you share progress and blockers?"),
        ("user", "I am finishing the API handoff."),
    ]


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


async def test_status_parser_falls_back_when_json_is_not_an_object() -> None:
    provider = CapturingLlmProvider(text='["not", "an", "object"]')
    parser = StatusParser(provider, model="test-model")

    signals = await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Raw status text.",
        correlation_id="corr-1",
    )

    assert signals == CheckInSignals(progress_note="Raw status text.")


async def test_status_parser_ignores_wrongly_typed_optional_fields() -> None:
    provider = CapturingLlmProvider(
        text=('{"progress_note":"   ","blockers":"blocked","eta_change_days":true,"mood":"angry"}')
    )
    parser = StatusParser(provider, model="test-model")

    signals = await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Fallback progress.",
        correlation_id="corr-1",
    )

    assert signals == CheckInSignals(progress_note="Fallback progress.")


async def test_clarification_evaluator_parses_needed_question() -> None:
    provider = CapturingLlmProvider(
        text='{"sufficient":false,"question":"What is blocking the handoff?","signals":null}'
    )
    evaluator = ClarificationEvaluator(provider, model="test-model")

    decision = await evaluator.evaluate(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Still working on it.",
        correlation_id="corr-1",
    )

    assert decision == ClarificationDecision(
        sufficient=False,
        question="What is blocking the handoff?",
    )
    assert provider.requests[0].metadata["purpose"] == "evaluate_checkin_clarification"


async def test_clarification_evaluator_parses_sufficient_signals() -> None:
    provider = CapturingLlmProvider(
        text=(
            '{"sufficient":true,"question":null,'
            '"signals":{"progress_note":"API handoff is ready",'
            '"blockers":[],"eta_change_days":0,"mood":"positive"}}'
        )
    )
    evaluator = ClarificationEvaluator(provider, model="test-model")

    decision = await evaluator.evaluate(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="API handoff is ready.",
        correlation_id="corr-1",
    )

    assert decision == ClarificationDecision(
        sufficient=True,
        signals=CheckInSignals(
            progress_note="API handoff is ready",
            eta_change_days=0,
            mood=Mood.POSITIVE,
        ),
    )
