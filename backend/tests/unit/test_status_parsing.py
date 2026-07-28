from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from core.application.agents.tool_loop import ToolCallingAgent
from core.application.status_parsing import (
    GENERIC_CLARIFICATION_QUESTION,
    ClarificationDecision,
    ClarificationEvaluator,
    StatusParser,
)
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.graph import JsonScalar
from core.domain.llm import LlmRequest, LlmResponse, LlmToolCall, TokenUsage
from core.domain.status import CheckInSignals, CrossPersonMention, IssueClaim
from tests.contract.fakes import FakeLlmProvider


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


def _response(
    *,
    text: str = "",
    tool_calls: tuple[LlmToolCall, ...] = (),
    finish_reason: str | None = None,
) -> LlmResponse:
    return LlmResponse(
        tenant_id="demo",
        text=text,
        model="test-model",
        usage=TokenUsage(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            cost_usd=0.0,
            latency_ms=1.0,
        ),
        trace_id="trace-parser",
        tool_calls=tool_calls,
        finish_reason=finish_reason,
    )


async def test_status_parser_converts_valid_json_to_signals() -> None:
    provider = CapturingLlmProvider(
        text=(
            '{"progress_note":"API handoff is ready",'
            '"blockers":["schema review"],"eta_change_days":2,'
            '"blockers_answered":true,"eta_answered":true,'
            '"issue_updates":[{"issue_key":"PO-1","claimed_done":true,'
            '"claimed_state":"done","note":"API handoff ready"}]}'
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
        blockers_answered=True,
        eta_answered=True,
        issue_updates=(
            IssueClaim(
                issue_key="PO-1",
                claimed_done=True,
                claimed_state="done",
                note="API handoff ready",
            ),
        ),
    )
    assert provider.requests[0].metadata == {
        "service": "status_parser",
        "purpose": "parse_checkin_signals",
        "developer_id": "dev-1",
    }
    assert provider.requests[0].system is not None
    assert "Do not invent blockers" in provider.requests[0].system
    assert [(message.role, message.content) for message in provider.requests[0].messages] == [
        ("system", "Status check-in conversation context."),
        ("assistant", "Can you share progress and blockers?"),
        ("user", "I am finishing the API handoff."),
    ]


async def test_status_parser_extracts_cross_person_requests() -> None:
    provider = CapturingLlmProvider(
        text=(
            '{"progress_note":"Blocked on schema review",'
            '"blockers":["schema review"],"eta_change_days":null,'
            '"blockers_answered":true,"eta_answered":false,'
            '"requests":['
            '{"name":"Alice Chen","kind":"review","note":"API schema review",'
            '"email":"alice@example.com"},'
            '{"name":"Bob","kind":"FYI","note":"status awareness","email":null}'
            "]}"
        )
    )
    parser = StatusParser(provider, model="test-model")

    signals = await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Blocked waiting on Alice Chen to review the API schema.",
        correlation_id="corr-1",
    )

    assert signals == CheckInSignals(
        progress_note="Blocked on schema review",
        blockers=("schema review",),
        blockers_answered=True,
        requests=(
            CrossPersonMention(
                raw_name="Alice Chen",
                kind="review",
                note="API schema review",
                email="alice@example.com",
            ),
        ),
    )
    assert "requests array" in provider.requests[0].prompt
    assert "specific named person" in provider.requests[0].prompt


async def test_status_parser_extracts_issue_updates() -> None:
    provider = CapturingLlmProvider(
        text=(
            '{"progress_note":"Finished PO-7 and opened PR",'
            '"blockers":[],"eta_change_days":0,'
            '"blockers_answered":true,"eta_answered":true,'
            '"issue_updates":[{"issue_key":"PO-7","claimed_done":true,'
            '"claimed_state":"done","note":"PR is ready"}]}'
        )
    )
    parser = StatusParser(provider, model="test-model")

    signals = await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="PO-7 is done; PR is ready.",
        correlation_id="corr-1",
    )

    assert signals.issue_updates == (
        IssueClaim(
            issue_key="PO-7",
            claimed_done=True,
            claimed_state="done",
            note="PR is ready",
        ),
    )
    assert "issue_updates array" in provider.requests[0].prompt


async def test_status_parser_falls_back_to_low_confidence_when_output_is_malformed() -> None:
    provider = CapturingLlmProvider(text="not valid structured output")
    parser = StatusParser(provider, model="test-model")

    signals = await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Finished the cache work; waiting on review.",
        correlation_id="corr-1",
    )

    # Safe fallback: keep the raw note but mark it unverified so it cannot roll up
    # green (parser_confident False, no answered blocker/ETA signals).
    assert signals == CheckInSignals(
        progress_note="Finished the cache work; waiting on review.",
        parser_confident=False,
    )
    assert signals.blockers_answered is False
    assert signals.eta_answered is False
    # One terse "return only valid JSON" retry happens before the fallback.
    assert len(provider.requests) == 2


async def test_status_parser_falls_back_to_low_confidence_when_json_is_not_an_object() -> None:
    provider = CapturingLlmProvider(text='["not", "an", "object"]')
    parser = StatusParser(provider, model="test-model")

    signals = await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Raw status text.",
        correlation_id="corr-1",
    )

    assert signals == CheckInSignals(progress_note="Raw status text.", parser_confident=False)


async def test_status_parser_requests_json_mode() -> None:
    provider = CapturingLlmProvider(
        text='{"progress_note":"x","blockers":[],"eta_change_days":0,'
        '"blockers_answered":true,"eta_answered":true}'
    )
    parser = StatusParser(provider, model="test-model")

    await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="x",
        correlation_id="corr-1",
    )

    assert provider.requests[0].json_mode is True


async def test_status_parser_parses_fenced_json_without_retry() -> None:
    provider = CapturingLlmProvider(
        text=(
            "```json\n"
            '{"progress_note":"API handoff is ready","blockers":[],'
            '"eta_change_days":0,"blockers_answered":true,"eta_answered":true}\n'
            "```"
        )
    )
    parser = StatusParser(provider, model="test-model")

    signals = await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="API handoff is ready.",
        correlation_id="corr-1",
    )

    assert signals.progress_note == "API handoff is ready"
    assert signals.parser_confident is True
    assert len(provider.requests) == 1


async def test_status_parser_ignores_wrongly_typed_optional_fields() -> None:
    provider = CapturingLlmProvider(
        text=('{"progress_note":"   ","blockers":"blocked","eta_change_days":true}')
    )
    parser = StatusParser(provider, model="test-model")

    signals = await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Fallback progress.",
        correlation_id="corr-1",
    )

    assert signals == CheckInSignals(progress_note="Fallback progress.")


async def test_status_parser_includes_prior_blockers_without_resolving_them() -> None:
    provider = CapturingLlmProvider(
        text=('{"progress_note":"Same as yesterday","blockers":[],"eta_change_days":null}')
    )
    parser = StatusParser(provider, model="test-model")

    await parser.parse_reply(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Same as yesterday.",
        correlation_id="corr-1",
        prior_blockers=("release gate",),
    )

    assert "Previously open blockers" in provider.requests[0].prompt
    assert "release gate" in provider.requests[0].prompt
    assert (
        "mark them resolved only if the reply says they are resolved" in provider.requests[0].prompt
    )


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
            '"blockers":[],"eta_change_days":0,'
            '"blockers_answered":true,"eta_answered":true,'
            '"issue_updates":[{"issue_key":"PO-1","claimed_done":true,'
            '"claimed_state":"done","note":"Jira should be done"}]}}'
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
            blockers_answered=True,
            eta_answered=True,
            issue_updates=(
                IssueClaim(
                    issue_key="PO-1",
                    claimed_done=True,
                    claimed_state="done",
                    note="Jira should be done",
                ),
            ),
        ),
    )


async def test_clarification_evaluator_parses_fenced_json() -> None:
    provider = CapturingLlmProvider(
        text=(
            "```json\n"
            '{"is_status_update":true,"sufficient":false,'
            '"question":"What is blocking the handoff?","signals":null}\n'
            "```"
        )
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
    assert len(provider.requests) == 1


async def test_clarification_evaluator_corrupt_output_is_not_sufficient() -> None:
    # Safety-critical: a garbled reply must never finalize as healthy/sufficient.
    provider = CapturingLlmProvider(text="totally not valid json")
    evaluator = ClarificationEvaluator(provider, model="test-model")

    decision = await evaluator.evaluate(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Graph sync is blocked on schema review.",
        correlation_id="corr-1",
    )

    assert decision.sufficient is False
    assert decision.question == GENERIC_CLARIFICATION_QUESTION
    # One terse "return only valid JSON" retry happens before the safe fallback.
    assert len(provider.requests) == 2


async def test_clarification_evaluator_missing_sufficient_flag_is_not_sufficient() -> None:
    # Valid JSON object but no sufficiency flag must default to a clarification.
    provider = CapturingLlmProvider(text='{"progress_note":"looks done"}')
    evaluator = ClarificationEvaluator(provider, model="test-model")

    decision = await evaluator.evaluate(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="It is done.",
        correlation_id="corr-1",
    )

    assert decision.sufficient is False
    assert decision.question == GENERIC_CLARIFICATION_QUESTION


async def test_clarification_evaluator_parses_non_status_intent() -> None:
    provider = CapturingLlmProvider(
        text='{"is_status_update":false,"sufficient":false,"question":null,"signals":null}'
    )
    evaluator = ClarificationEvaluator(provider, model="test-model")

    decision = await evaluator.evaluate(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Thanks!",
        correlation_id="corr-1",
    )

    assert decision == ClarificationDecision(sufficient=False, is_status_update=False)
    assert "is_status_update boolean" in provider.requests[0].prompt


async def test_clarification_evaluator_treats_ok_as_non_status_without_llm() -> None:
    provider = CapturingLlmProvider(
        text='{"is_status_update":true,"sufficient":true,"question":null,"signals":null}'
    )
    evaluator = ClarificationEvaluator(provider, model="test-model")

    decision = await evaluator.evaluate(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply=" ok \n",
        correlation_id="corr-1",
    )

    assert decision == ClarificationDecision(sufficient=False, is_status_update=False)
    assert provider.requests == []


async def test_clarification_evaluator_parses_final_json_after_tool_cap() -> None:
    provider = FakeLlmProvider(
        responses=[
            _response(
                tool_calls=(
                    LlmToolCall(
                        id="call-1",
                        name="fetch_conversation_history",
                        arguments={"limit": 5},
                    ),
                ),
                finish_reason="tool_calls",
            ),
            _response(
                tool_calls=(
                    LlmToolCall(
                        id="call-2",
                        name="fetch_conversation_history",
                        arguments={"limit": 10},
                    ),
                ),
                finish_reason="tool_calls",
            ),
            _response(
                text=(
                    '{"sufficient":true,"question":null,'
                    '"signals":{"progress_note":"History confirms handoff is ready",'
                    '"blockers":[],"eta_change_days":0,'
                    '"blockers_answered":true,"eta_answered":true}}'
                ),
                finish_reason="stop",
            ),
        ]
    )
    tool = StaticTool()
    evaluator = ClarificationEvaluator(
        provider,
        model="test-model",
        tool_agent=ToolCallingAgent(provider, max_tool_iterations=1),
    )

    decision = await evaluator.evaluate(
        tenant_id="demo",
        developer_id="dev-1",
        raw_reply="Ready.",
        correlation_id="corr-1",
        tools=(tool,),
    )

    assert decision == ClarificationDecision(
        sufficient=True,
        signals=CheckInSignals(
            progress_note="History confirms handoff is ready",
            eta_change_days=0,
            blockers_answered=True,
            eta_answered=True,
        ),
    )
    assert len(provider.requests) == 3
    assert provider.requests[2].tools == ()
    assert provider.requests[2].tool_results[0].content == "history: 5"


@dataclass
class StaticTool:
    calls: list[dict[str, JsonScalar]] = field(default_factory=list)
    name: str = "fetch_conversation_history"
    description: str = "Fetch static test history."
    parameters: dict[str, object] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        self.calls.append(dict(arguments))
        return f"history: {arguments.get('limit', '')}"
