from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tracers.context import _tracing_v2_is_enabled

from core.application.ask_investigation import ANSWER_REMINDER
from core.domain.graph import JsonScalar
from core.domain.llm import LlmMessage, LlmRequest, LlmResponse, LlmToolCall, TokenUsage
from core.ports.investigation import (
    EngineAnswer,
    EngineEvent,
    EnginePlan,
    EngineStepDone,
    EngineStepFailed,
    InvestigationRun,
)
from infra.adapters.agents.deep_investigation import (
    FOLLOW_UP_STEPS,
    MAIN_MODEL_CALLS,
    RESEARCHER,
    DeepAgentInvestigationEngine,
)
from infra.adapters.llm.langchain_bridge import OpenProgramChatModel, to_message, to_request

AS_OF = date(2026, 10, 9)
BLOCKERS = "What blocks Payments Pod, and since when?"
CHANGES = "What changed in Payments Pod in the last 14 days?"
ANSWER = json.dumps({"answer": ["Payments Pod is late because:", "• A"], "references": []})


def _response(text: str = "", calls: tuple[LlmToolCall, ...] = ()) -> LlmResponse:
    return LlmResponse(
        tenant_id="demo",
        text=text,
        model="m",
        usage=TokenUsage(
            prompt_tokens=2, completion_tokens=1, total_tokens=3, cost_usd=0.0, latency_ms=1
        ),
        trace_id="trace-deep",
        tool_calls=calls,
    )


def _tasks(*steps: str) -> LlmResponse:
    return _response(
        calls=tuple(
            LlmToolCall(
                id=f"task-{n}",
                name="task",
                arguments={"description": step, "subagent_type": RESEARCHER},
                arguments_json=json.dumps({"description": step, "subagent_type": RESEARCHER}),
            )
            for n, step in enumerate(steps)
        )
    )


def _look(tool: str = "open_risks") -> LlmResponse:
    return _response(calls=(LlmToolCall(id=f"call-{tool}", name=tool, arguments={}),))


def _notes(*findings: str) -> LlmResponse:
    return _response(json.dumps({"findings": list(findings), "references": [], "gaps": []}))


type _Reply = LlmResponse | Exception


@dataclass
class _ScriptedLlm:
    """The main agent's turns in order; each researcher's by the step it was given."""

    main: Sequence[_Reply] = (_tasks(BLOCKERS, CHANGES), _response(ANSWER))
    steps: Mapping[str, Sequence[_Reply]] = field(default_factory=dict)
    slow: float = 0.0
    requests: list[LlmRequest] = field(default_factory=list)
    traced: list[bool] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    # Researchers that have reached their slow reply, and a signal once two have.
    waiting: list[str] = field(default_factory=list)
    two_waiting: asyncio.Event = field(default_factory=asyncio.Event)
    _asked: Counter[str] = field(default_factory=Counter)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        self.traced.append(bool(_tracing_v2_is_enabled()))
        if request.metadata["purpose"] == "investigation_main":
            key, replies = "main", self.main
        else:
            key = _first_user(request)
            replies = self.steps.get(key) or (_look(), _notes(f"Notes for {key}"))
            if self.slow:
                self.waiting.append(key)
                if len(self.waiting) >= 2:
                    self.two_waiting.set()
                try:
                    await asyncio.sleep(self.slow)
                except asyncio.CancelledError:
                    self.cancelled.append(key)
                    raise
        reply = replies[min(self._asked[key], len(replies) - 1)]
        self._asked[key] += 1
        if isinstance(reply, Exception):
            raise reply
        return reply

    def of(self, purpose: str) -> list[LlmRequest]:
        """The requests made for one purpose, in order."""
        return [r for r in self.requests if r.metadata["purpose"] == purpose]


def _first_user(request: LlmRequest) -> str:
    return next(turn.content for turn in request.turns if turn.role == "user")


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str = "Reads delivery data."
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"kinds": {"type": "array", "items": {"type": "string"}}},
        }
    )
    seen: list[Mapping[str, JsonScalar]] = field(default_factory=list)

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        self.seen.append(arguments)
        return json.dumps({"tool": self.name, "as_of": AS_OF.isoformat()})


def _run(llm: _ScriptedLlm, *tools: _Tool, max_steps: int = 3, rounds: int = 6) -> InvestigationRun:
    return InvestigationRun(
        tenant_id="demo",
        question="Why is Payments Pod late?",
        correlation_id="ask-investigate:test",
        as_of=AS_OF,
        model="deep-model",
        llm=llm,
        tools=tools or (_Tool("open_risks"), _Tool("pod_blockers")),
        max_steps=max_steps,
        max_tool_iterations=rounds,
    )


async def _events(run: InvestigationRun) -> list[EngineEvent]:
    return [event async for event in DeepAgentInvestigationEngine().run(run)]


async def test_the_main_agent_plans_researchers_look_up_and_the_answer_comes_last() -> None:
    llm = _ScriptedLlm()

    events = await _events(_run(llm))

    plan, *ended, answer = events
    assert plan == EnginePlan(steps=(BLOCKERS, CHANGES))
    assert sorted(ended, key=lambda e: e.index) == [  # type: ignore[union-attr]
        EngineStepDone(
            index=0, notes=_notes(f"Notes for {BLOCKERS}").text, tools_used=("open_risks",)
        ),
        EngineStepDone(
            index=1, notes=_notes(f"Notes for {CHANGES}").text, tools_used=("open_risks",)
        ),
    ]
    assert answer == EngineAnswer(text=ANSWER, trace_id="trace-deep")
    assert {request.model for request in llm.requests} == {"deep-model"}
    assert {request.correlation_id for request in llm.requests} == {"ask-investigate:test"}


async def test_no_shell_no_data_tools_for_the_main_agent_and_only_the_askers_for_researchers() -> (
    None
):
    llm = _ScriptedLlm()

    await _events(_run(llm, _Tool("open_risks"), _Tool("status_reasons")))

    main_tools = {tool.name for tool in llm.of("investigation_main")[0].tools}
    researcher_tools = {tool.name for tool in llm.of("investigation_step")[0].tools}
    assert "task" in main_tools
    assert "execute" not in main_tools | researcher_tools
    assert not main_tools & {"open_risks", "status_reasons"}
    assert {"open_risks", "status_reasons"} <= researcher_tools
    assert "task" not in researcher_tools
    # A researcher is given its step alone, with today's dates in its instructions.
    first = llm.of("investigation_step")[0]
    assert [(turn.role, turn.content) for turn in first.turns] in (
        [("user", BLOCKERS)],
        [("user", CHANGES)],
    )
    assert "Today is Friday 2026-10-09." in (first.system or "")


async def test_no_call_is_traced_to_langsmith_whatever_the_environment_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    llm = _ScriptedLlm()

    await _events(_run(llm))

    assert llm.traced and not any(llm.traced)


async def test_a_researcher_is_held_to_its_model_call_budget_and_its_step_fails() -> None:
    llm = _ScriptedLlm(main=(_tasks(BLOCKERS), _response(ANSWER)), steps={BLOCKERS: (_look(),)})

    events = await _events(_run(llm, rounds=3))

    # The budget is the rounds plus room for the notes: past it the step ends.
    assert len(llm.of("investigation_step")) == 3 + 2
    assert EngineStepFailed(index=0, tools_used=("open_risks",)) in events
    assert isinstance(events[-1], EngineAnswer)


async def test_a_step_whose_researcher_fails_is_a_failed_step_and_the_rest_answer() -> None:
    llm = _ScriptedLlm(steps={BLOCKERS: (RuntimeError("provider down"),)})

    events = await _events(_run(llm))

    assert EngineStepFailed(index=0) in events
    assert any(isinstance(e, EngineStepDone) and e.index == 1 for e in events)
    assert isinstance(events[-1], EngineAnswer)
    # The main agent was told, in the failed step's result.
    results = [t for t in llm.of("investigation_main")[-1].turns if t.role == "tool"]
    assert any("could not be looked up (RuntimeError)" in t.content for t in results)


async def test_follow_up_steps_join_the_plan_up_to_their_limit() -> None:
    extra = ("Who owns CHK-103?", "Who reviews it?", "What does it hold up?")
    llm = _ScriptedLlm(main=(_tasks(BLOCKERS), _tasks(*extra), _response(ANSWER)))

    events = await _events(_run(llm, max_steps=1))

    plans = [e for e in events if isinstance(e, EnginePlan)]
    assert plans[-1].steps == (BLOCKERS, *extra)
    # One step plus FOLLOW_UP_STEPS may run; the call past them is refused.
    done = [e for e in events if isinstance(e, EngineStepDone)]
    assert len(done) == 1 + FOLLOW_UP_STEPS
    assert len(llm.of("investigation_step")) == 2 * (1 + FOLLOW_UP_STEPS)


async def test_a_main_agent_past_its_call_budget_ends_the_run_with_an_error() -> None:
    llm = _ScriptedLlm(main=(_response(calls=(LlmToolCall(id="x", name="ls", arguments={}),)),))

    with pytest.raises(Exception, match="call limit"):
        await _events(_run(llm))

    assert len(llm.of("investigation_main")) == MAIN_MODEL_CALLS


async def test_closing_the_stream_stops_the_researchers_still_running() -> None:
    llm = _ScriptedLlm(slow=5.0)
    events = DeepAgentInvestigationEngine().run(_run(llm))

    first = await anext(events)
    # Not a fixed sleep: under load the researchers start later.
    async with asyncio.timeout(10):
        await llm.two_waiting.wait()
    await events.aclose()

    assert first == EnginePlan(steps=(BLOCKERS, CHANGES))
    assert sorted(llm.cancelled) == sorted([BLOCKERS, CHANGES])


async def test_the_main_agent_reads_the_conversation_before_the_question() -> None:
    llm = _ScriptedLlm()
    run = _run(llm)
    run = InvestigationRun(
        **{
            **run.__dict__,
            "history": (
                LlmMessage(role="user", content="Is Checkout Revamp red?"),
                LlmMessage(role="assistant", content="Yes: three blockers in Payments Pod."),
            ),
        }
    )

    await _events(run)

    first = llm.of("investigation_main")[0]
    assert [(t.role, t.content) for t in first.turns[:2]] == [
        ("user", "Is Checkout Revamp red?"),
        ("assistant", "Yes: three blockers in Payments Pod."),
    ]
    assert first.turns[2].content.endswith("Question: Why is Payments Pod late?")
    # Researchers get the step alone, never the conversation.
    assert all(len(r.turns) <= 3 for r in llm.of("investigation_step"))


async def test_a_researchers_list_arguments_reach_the_tool_whole() -> None:
    tool = _Tool("search_graph_nodes")
    call = LlmToolCall(
        id="c",
        name="search_graph_nodes",
        arguments={},
        arguments_json=json.dumps({"kinds": ["pod", "project"]}),
    )
    llm = _ScriptedLlm(
        main=(_tasks(BLOCKERS), _response(ANSWER)),
        steps={BLOCKERS: (_response(calls=(call,)), _notes("Found"))},
    )

    await _events(_run(llm, tool))

    assert tool.seen == [{"kinds": ["pod", "project"]}]


def test_a_conversation_is_sent_in_the_order_it_went() -> None:
    messages = [
        SystemMessage(content="Rules."),
        SystemMessage(content="More rules."),
        HumanMessage(content="Why?"),
        AIMessage(
            content="Planning.",
            tool_calls=[{"name": "write_todos", "args": {"todos": [{"content": "A"}]}, "id": "t1"}],
        ),
        ToolMessage(content="ok", tool_call_id="t1"),
        HumanMessage(content="Answer now."),
    ]
    tools = [{"type": "function", "function": {"name": "task", "description": "Delegate"}}]

    request = to_request(
        messages,
        tools=tools,
        model="m",
        tenant_id="demo",
        correlation_id="c",
        metadata={"purpose": "investigation_main"},
    )

    assert request.system == "Rules.\n\nMore rules."
    assert [(t.role, t.content, t.tool_call_id) for t in request.turns] == [
        ("user", "Why?", None),
        ("assistant", "Planning.", None),
        ("tool", "ok", "t1"),
        ("user", "Answer now.", None),
    ]
    (call,) = request.turns[1].tool_calls
    assert (call.id, call.name, call.arguments) == ("t1", "write_todos", {})
    assert json.loads(call.arguments_json or "") == {"todos": [{"content": "A"}]}
    assert [t.name for t in request.tools] == ["task"]
    assert request.metadata == {"purpose": "investigation_main"}


async def test_the_answer_rules_are_said_again_right_after_the_notes() -> None:
    llm = _ScriptedLlm()

    await _events(_run(llm))

    first, answering = llm.of("investigation_main")
    assert first.turns[-1].role == "user"
    assert [t.role for t in answering.turns[-3:]] == ["tool", "tool", "user"]
    assert answering.turns[-1].content == ANSWER_REMINDER
    assert ANSWER_REMINDER not in (answering.system or "")


def test_a_reply_maps_back_with_its_calls_whole_and_its_token_counts() -> None:
    call = LlmToolCall(id="c", name="task", arguments={}, arguments_json='{"todos": [1, 2]}')

    message = to_message(_response("Hi", calls=(call,)))

    assert message.content == "Hi"
    assert message.tool_calls == [
        {"name": "task", "args": {"todos": [1, 2]}, "id": "c", "type": "tool_call"}
    ]
    assert message.usage_metadata == {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}
    assert message.response_metadata["trace_id"] == "trace-deep"


async def test_the_model_reports_our_provider_and_is_async_only() -> None:
    model = OpenProgramChatModel(
        provider=_ScriptedLlm(), model_name="m", tenant_id="demo", correlation_id="c"
    )

    assert model._get_ls_params()["ls_provider"] == "openprogram"
    with pytest.raises(NotImplementedError):
        model.invoke("hello")
