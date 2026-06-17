from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from core.application.agents.tool_loop import ToolCallingAgent
from core.application.tools.conversation_history import ConversationHistoryTool
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.graph import JsonScalar
from core.domain.llm import LlmRequest, LlmResponse, LlmToolCall, TokenUsage
from tests.contract.fakes import FakeConversationRepository, FakeLlmProvider


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
        trace_id="trace-test",
        tool_calls=tool_calls,
        finish_reason=finish_reason,
    )


async def test_conversation_history_tool_fetches_recent_and_day_turns() -> None:
    repository = FakeConversationRepository()
    reference_at = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    await repository.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-old",
            conversation_date=date(2026, 1, 8),
            role=ConversationRole.USER,
            content="Older retained context.",
            correlation_id="corr-old",
            chat_message_id="msg-old",
            observed_at=reference_at - timedelta(days=2),
        )
    )
    await repository.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-1",
            conversation_date=date(2026, 1, 10),
            role=ConversationRole.AGENT,
            content="Can you share status?",
            correlation_id="corr-1",
            chat_message_id="msg-agent",
            observed_at=reference_at - timedelta(minutes=10),
        )
    )

    tool = ConversationHistoryTool(
        tenant_id="demo",
        developer_id="dev-1",
        repository=repository,
        retention_days=30,
        reference_at=reference_at,
    )

    recent = await tool.run({"since_days": 7, "limit": 2})
    day = await tool.run({"on": "2026-01-10"})

    assert "Older retained context." in recent
    assert "Can you share status?" in day


async def test_tool_calling_agent_executes_tool_call_then_returns_final_response() -> None:
    provider = FakeLlmProvider(
        responses=[
            _response(
                tool_calls=(
                    LlmToolCall(
                        id="call-1",
                        name="echo_tool",
                        arguments={"value": "history"},
                    ),
                ),
                finish_reason="tool_calls",
            ),
            _response(text="final answer", finish_reason="stop"),
        ]
    )
    tool = EchoTool()
    agent = ToolCallingAgent(llm_provider=provider, max_tool_iterations=3)

    response = await agent.run(
        LlmRequest(
            tenant_id="demo",
            prompt="Need more context?",
            model="test-model",
            correlation_id="corr-1",
        ),
        tools=(tool,),
    )

    assert response.text == "final answer"
    assert len(provider.requests) == 2
    assert provider.requests[0].tools[0].name == "echo_tool"
    assert provider.requests[1].tool_results[0].content == "echo: history"


async def test_tool_calling_agent_stops_at_iteration_cap() -> None:
    provider = FakeLlmProvider(
        responses=[
            _response(
                tool_calls=(
                    LlmToolCall(id="call-1", name="echo_tool", arguments={"value": "one"}),
                ),
                finish_reason="tool_calls",
            ),
            _response(
                tool_calls=(
                    LlmToolCall(id="call-2", name="echo_tool", arguments={"value": "two"}),
                ),
                finish_reason="tool_calls",
            ),
        ]
    )
    agent = ToolCallingAgent(llm_provider=provider, max_tool_iterations=1)

    response = await agent.run(
        LlmRequest(
            tenant_id="demo",
            prompt="Need more context?",
            model="test-model",
            correlation_id="corr-1",
        ),
        tools=(EchoTool(),),
    )

    assert response.tool_calls == (
        LlmToolCall(id="call-2", name="echo_tool", arguments={"value": "two"}),
    )
    assert len(provider.requests) == 2


@dataclass
class EchoTool:
    calls: list[dict[str, JsonScalar]] = field(default_factory=list)
    name: str = "echo_tool"
    description: str = "Echo the value argument."
    parameters: dict[str, object] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        self.calls.append(dict(arguments))
        return f"echo: {arguments.get('value', '')}"
