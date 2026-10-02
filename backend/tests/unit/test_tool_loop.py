from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest

from core.application.agents import tool_loop as tool_loop_module
from core.application.agents.tool_loop import ToolCallingAgent
from core.application.tools.conversation_history import ConversationHistoryTool
from core.application.tools.git_activity import GitActivityTool
from core.application.tools.issue_tracker import IssueTrackerTool
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.errors import ProviderUnavailable
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.llm import LlmRequest, LlmResponse, LlmToolCall, TokenUsage
from core.ports.issue_tracker import IssueTracker
from tests.contract.fakes import (
    FakeConversationRepository,
    FakeIssueTracker,
    FakeLlmProvider,
    FakeTimeSeriesRepository,
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


async def test_issue_tracker_tool_fetches_active_and_exact_issue() -> None:
    assignee = UserRef(tenant_id="demo", external_id="dev-1")
    tracker = FakeIssueTracker(
        issues={
            "PO-1": Issue(
                tenant_id="demo",
                key="PO-1",
                title="Build graph sync",
                state=IssueState.BLOCKED,
                assignee=assignee,
                updated_at=datetime(2026, 1, 10, 8, 0, tzinfo=UTC),
            ),
            "PO-2": Issue(
                tenant_id="demo",
                key="PO-2",
                title="Unassigned work",
                state=IssueState.TODO,
            ),
        }
    )
    tool = IssueTrackerTool(tenant_id="demo", developer_id="dev-1", issue_tracker=tracker)

    active = await tool.run({})
    exact = await tool.run({"issue_key": "PO-2"})

    assert "PO-1: Build graph sync | state=blocked" in active
    assert "PO-2: Unassigned work | state=todo" in exact


async def test_issue_tracker_tool_degrades_when_the_tracker_cannot_answer() -> None:
    """An unfetchable issue must not abort the check-in that mentioned it.

    The tool runs inside the reply parse, so a tracker that is unreachable,
    unauthorized, or simply does not hold the key the model asked about has to
    come back as a sentence rather than an exception.
    """

    class UnavailableTracker:
        async def get_issue(self, tenant_id: str, key: str) -> Issue:
            raise ProviderUnavailable(f"issue {key} not found")

        async def list_active_for(self, user: UserRef) -> list[Issue]:
            raise ProviderUnavailable("tracker unreachable")

    tool = IssueTrackerTool(
        tenant_id="demo",
        developer_id="dev-1",
        issue_tracker=cast(IssueTracker, UnavailableTracker()),
    )

    assert await tool.run({"issue_key": "CHK-103"}) == "Issue CHK-103 could not be fetched."
    assert await tool.run({}) == "Active issues could not be fetched."


async def test_git_activity_tool_filters_recent_developer_facts_by_issue_key() -> None:
    repository = FakeTimeSeriesRepository()
    reference_at = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    await repository.append_fact(
        FactEvent(
            tenant_id="demo",
            source="vcs_commit",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
            payload={"repo": "repo-1", "sha": "abc123", "message": "PO-1 wire status parser"},
            observed_at=reference_at - timedelta(hours=1),
            correlation_id="vcs:commit:demo:repo-1:abc123",
        )
    )
    await repository.append_fact(
        FactEvent(
            tenant_id="demo",
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
            payload={"repo": "repo-1", "id": "7", "title": "PO-2 unrelated", "merged": False},
            observed_at=reference_at - timedelta(hours=2),
            correlation_id="vcs:pull_request:demo:repo-1:7",
        )
    )
    tool = GitActivityTool(
        tenant_id="demo",
        developer_id="dev-1",
        repository=repository,
        reference_at=reference_at,
    )

    output = await tool.run({"issue_key": "PO-1", "since_days": 3})

    assert "commit: repo=repo-1, sha=abc123, message=PO-1 wire status parser" in output
    assert "PO-2 unrelated" not in output


async def test_git_activity_tool_reports_merge_request_state_and_finds_it_by_branch() -> None:
    # The LLM judges "it's done" against this output, so a draft or closed
    # request must say so; the branch carries the key when the title does not.
    repository = FakeTimeSeriesRepository()
    reference_at = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    await repository.append_fact(
        FactEvent(
            tenant_id="demo",
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
            payload={
                "repo": "repo-1",
                "id": "3",
                "title": "Refund edge cases",
                "merged": False,
                "state": "open",
                "draft": True,
                "source_branch": "PO-6-refund-edge-cases",
            },
            observed_at=reference_at - timedelta(hours=1),
            correlation_id="vcs:pull_request:demo:repo-1:3",
        )
    )
    tool = GitActivityTool(
        tenant_id="demo",
        developer_id="dev-1",
        repository=repository,
        reference_at=reference_at,
    )

    output = await tool.run({"issue_key": "PO-6", "since_days": 3})

    assert (
        "pull_request: repo=repo-1, title=Refund edge cases, merged=False, state=open, "
        "draft=True, branch=PO-6-refund-edge-cases"
    ) in output


async def test_git_activity_tool_default_lookback_matches_recent_fact_window() -> None:
    repository = FakeTimeSeriesRepository()
    reference_at = datetime(2026, 1, 30, 12, 0, tzinfo=UTC)
    await repository.append_fact(
        FactEvent(
            tenant_id="demo",
            source="vcs_commit",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
            payload={"repo": "repo-1", "sha": "old123", "message": "PO-1 active two weeks ago"},
            observed_at=reference_at - timedelta(days=14),
            correlation_id="vcs:commit:demo:repo-1:old123",
        )
    )
    await repository.append_fact(
        FactEvent(
            tenant_id="demo",
            source="vcs_commit",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
            payload={"repo": "repo-1", "sha": "stale123", "message": "PO-1 stale activity"},
            observed_at=reference_at - timedelta(days=31),
            correlation_id="vcs:commit:demo:repo-1:stale123",
        )
    )
    tool = GitActivityTool(
        tenant_id="demo",
        developer_id="dev-1",
        repository=repository,
        reference_at=reference_at,
    )

    output = await tool.run({})

    assert "old123" in output
    assert "stale123" not in output


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


async def test_tool_calling_agent_stops_at_iteration_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            _response(text="final answer at cap", finish_reason="stop"),
        ]
    )
    agent = ToolCallingAgent(llm_provider=provider, max_tool_iterations=1)
    logger = CapturingLogger()
    monkeypatch.setattr(tool_loop_module, "_logger", logger)

    response = await agent.run(
        LlmRequest(
            tenant_id="demo",
            prompt="Need more context?",
            model="test-model",
            correlation_id="corr-1",
        ),
        tools=(EchoTool(),),
    )

    assert response.text == "final answer at cap"
    assert response.tool_calls == ()
    assert len(provider.requests) == 3
    assert provider.requests[2].tools == ()
    assert provider.requests[2].tool_calls == (
        LlmToolCall(id="call-1", name="echo_tool", arguments={"value": "one"}),
    )
    assert provider.requests[2].tool_results[0].content == "echo: one"
    assert logger.events == [
        {
            "event": "tool_loop_cap_reached",
            "correlation_id": "corr-1",
            "iteration": 1,
            "tool_call_count": 1,
        }
    ]


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


@dataclass
class CapturingLogger:
    events: list[dict[str, object]] = field(default_factory=list)

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append({"event": event, **kwargs})
