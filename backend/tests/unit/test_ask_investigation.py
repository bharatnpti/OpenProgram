from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field
from datetime import date

from core.application.ask_investigation import (
    ANSWER_FAILED,
    ANSWER_REMINDER,
    ANSWER_RULES,
    INVESTIGATE_FORMAT_RULES,
    STEP_FAILED,
    STEP_TIMED_OUT,
    TIMED_OUT,
    AnswerEvent,
    FailedEvent,
    InvestigationEvent,
    InvestigationLimits,
    InvestigationService,
    PlanEvent,
    StepEvent,
    step_notes,
)
from core.application.ask_service import ANSWER_FORMAT_RULES, AskService
from core.application.blocker_resolution import BlockerResolutionService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.persona_views import PersonaViewService
from core.application.risk_service import RiskService
from core.domain.auth import Principal, Role
from core.domain.graph import Developer, Pod, Task
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.risk import RiskProviderConfig
from core.ports.investigation import (
    EngineAnswer,
    EngineEvent,
    EnginePlan,
    EngineStepDone,
    EngineStepFailed,
    InvestigationRun,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore

AS_OF = date(2026, 10, 9)
QUESTION = "Why is Payments Pod late?"
ANSWER = json.dumps(
    {
        "answer": ["Payments Pod is late because:", "• CHK-103 blocked 9 days"],
        "references": ["pod-payments", "CHK-103"],
    }
)


@dataclass
class _Engine:
    """Plays a script of engine events; a float in it is a wait of that many seconds."""

    script: Sequence[EngineEvent | float | Exception]
    runs: list[InvestigationRun] = field(default_factory=list)
    closed: list[bool] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "scripted"

    async def run(self, run: InvestigationRun) -> AsyncGenerator[EngineEvent]:
        self.runs.append(run)
        try:
            for item in self.script:
                if isinstance(item, float):
                    await asyncio.sleep(item)
                elif isinstance(item, Exception):
                    raise item
                else:
                    if isinstance(item, EngineAnswer | EngineStepDone):
                        # The engine's model calls go through the run's own provider.
                        await run.llm.complete(_request())
                    yield item
        finally:
            self.closed.append(True)


@dataclass
class _Llm:
    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            tenant_id="demo",
            text="",
            model="m",
            usage=TokenUsage(
                prompt_tokens=3, completion_tokens=2, total_tokens=5, cost_usd=0.01, latency_ms=1
            ),
            trace_id="t",
        )


def _request() -> LlmRequest:
    return LlmRequest(tenant_id="demo", prompt="", model="m", correlation_id="c")


async def _ask_service() -> AskService:
    store = InMemoryGraphStore()
    for node in (
        Pod(tenant_id="demo", id="pod-payments", name="Payments Pod"),
        Developer(
            tenant_id="demo",
            id="dev-kai",
            name="Kai Thompson",
            metadata={"chat_external_id": "U0KAI00001"},
        ),
        Task(tenant_id="demo", id="CHK-103", name="3-D Secure", metadata={"key": "CHK-103"}),
    ):
        await store.upsert_node(node)
    return AskService(
        llm_provider=_Llm(),
        graph_repository=store,
        time_series_repository=store,
        flow_metrics_service=FlowMetricsService(
            graph_repository=store, time_series_repository=store
        ),
        persona_view_service=PersonaViewService(
            graph_repository=store,
            status_repository=store,
            rollup_repository=store,
            time_series_repository=store,
        ),
        risk_service=RiskService(
            graph_repository=store,
            time_series_repository=store,
            status_repository=store,
            blocker_resolution=BlockerResolutionService(store, store),
            rollup_repository=store,
            provider_config=RiskProviderConfig(default_no_pr_days=3, default_stale_days=30),
        ),
        model="test-model",
    )


async def _investigate(
    engine: _Engine,
    *,
    limits: InvestigationLimits | None = None,
    principal: Principal | None = None,
) -> tuple[list[InvestigationEvent], AskService]:
    ask = await _ask_service()
    service = InvestigationService(
        ask_service=ask, llm_provider=_Llm(), engine=engine, model="deep-model", limits=limits
    )
    events = [
        event
        async for event in service.investigate(
            principal=principal or _principal(Role.MGR),
            question=QUESTION,
            correlation_id="ask-investigate:test",
            as_of=AS_OF,
        )
    ]
    return events, ask


def _principal(*roles: Role) -> Principal:
    return Principal(tenant_id="demo", subject="U1001", roles=frozenset(roles))


async def test_the_engines_plan_steps_and_answer_reach_the_reader_readable() -> None:
    engine = _Engine(
        [
            EnginePlan(steps=("What blocks Payments Pod?", "What changed?")),
            EngineStepDone(
                index=1,
                notes=json.dumps({"findings": ["U0KAI00001 has not replied since Monday"]}),
                tools_used=("recent_facts", "recent_facts"),
            ),
            EngineStepDone(
                index=0,
                notes='Notes:\n{"findings": ["CHK-103 blocked 9 days (pod-payments)"]}',
                tools_used=("pod_blockers",),
            ),
            EngineAnswer(text=ANSWER, trace_id="trace-answer"),
        ]
    )

    events, _ = await _investigate(engine)

    plan, changed, blocked, answer = events
    assert isinstance(plan, PlanEvent)
    assert [(s.index, s.question, s.status) for s in plan.steps] == [
        (0, "What blocks Payments Pod?", "running"),
        (1, "What changed?", "running"),
    ]
    assert isinstance(changed, StepEvent)
    assert changed.step.findings == ("Kai Thompson has not replied since Monday",)
    assert changed.step.tools_used == ("recent_facts",)
    assert isinstance(blocked, StepEvent)
    assert blocked.step.findings == ("CHK-103 blocked 9 days (Payments Pod)",)
    assert isinstance(answer, AnswerEvent)
    assert answer.view.answer == "Payments Pod is late because:\n• CHK-103 blocked 9 days"
    assert answer.view.trace_id == "trace-answer"
    assert [source.label for source in answer.view.sources] == ["Payments Pod", "CHK-103"]
    assert answer.view.tools_used == ("pod_blockers", "recent_facts")
    assert [step.status for step in answer.steps] == ["done", "done"]
    assert engine.closed == [True]


async def test_the_engine_gets_the_askers_own_tools_model_and_limits() -> None:
    engine = _Engine([EngineAnswer(text=ANSWER, trace_id="t")])
    executive = _principal(Role.EXEC)

    _, ask = await _investigate(
        engine, principal=executive, limits=InvestigationLimits(max_steps=2, max_tool_iterations=4)
    )

    (run,) = engine.runs
    assert {tool.name for tool in run.tools} == {
        tool.name for tool in ask.tools_for(executive, AS_OF)
    }
    assert "pod_checkins" not in {tool.name for tool in run.tools}
    assert (run.model, run.as_of, run.question) == ("deep-model", AS_OF, QUESTION)
    assert (run.max_steps, run.max_tool_iterations) == (2, 4)


async def test_steps_added_later_join_the_plan_and_a_failed_one_says_so() -> None:
    engine = _Engine(
        [
            EnginePlan(steps=("A?",)),
            EngineStepFailed(index=0, tools_used=("open_risks",)),
            EnginePlan(steps=("A?", "B?")),
            EngineStepDone(index=1, notes="- B is fine", tools_used=()),
            EngineAnswer(text=ANSWER, trace_id="t"),
        ]
    )

    events, _ = await _investigate(engine)

    replanned = events[2]
    assert isinstance(replanned, PlanEvent)
    assert [(s.question, s.status, s.error) for s in replanned.steps] == [
        ("A?", "failed", STEP_FAILED),
        ("B?", "running", None),
    ]
    answer = events[-1]
    assert isinstance(answer, AnswerEvent)
    assert answer.steps[1].findings == ("B is fine",)


async def test_an_engine_that_runs_too_long_is_stopped_and_closed() -> None:
    engine = _Engine([EnginePlan(steps=("A?", "B?")), 5.0, EngineAnswer(text=ANSWER, trace_id="t")])

    events, _ = await _investigate(engine, limits=InvestigationLimits(timeout_seconds=0.1))

    failed = events[-1]
    assert isinstance(failed, FailedEvent)
    assert failed.message == TIMED_OUT
    assert {step.error for step in failed.steps} == {STEP_TIMED_OUT}
    assert engine.closed == [True]


async def test_an_engine_that_fails_or_ends_without_answering_says_why() -> None:
    raising, _ = await _investigate(_Engine([EnginePlan(steps=("A?",)), RuntimeError("down")]))
    silent, _ = await _investigate(_Engine([EnginePlan(steps=("A?",))]))

    for events in (raising, silent):
        failed = events[-1]
        assert isinstance(failed, FailedEvent)
        assert failed.message == ANSWER_FAILED
        assert [step.question for step in failed.steps] == ["A?"]


async def test_a_step_the_plan_never_named_is_not_shown() -> None:
    engine = _Engine(
        [
            EnginePlan(steps=("A?",)),
            EngineStepDone(index=4, notes="stray", tools_used=()),
            EngineAnswer(text=ANSWER, trace_id="t"),
        ]
    )

    events, _ = await _investigate(engine)

    assert [type(event) for event in events] == [PlanEvent, AnswerEvent]


async def test_a_reader_that_stops_listening_closes_the_engine() -> None:
    engine = _Engine([EnginePlan(steps=("A?",)), 5.0, EngineAnswer(text=ANSWER, trace_id="t")])
    service = InvestigationService(
        ask_service=await _ask_service(), llm_provider=_Llm(), engine=engine, model="m"
    )
    events = service.investigate(
        principal=_principal(Role.MGR), question=QUESTION, correlation_id="c", as_of=AS_OF
    )

    first = await anext(events)
    waiting = asyncio.create_task(anext(events))
    await asyncio.sleep(0.05)
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)

    assert isinstance(first, PlanEvent)
    assert engine.closed == [True]


def test_step_notes_read_the_object_wherever_it_is_and_lines_when_there_is_none() -> None:
    fenced = 'Here:\n```json\n{"findings": ["• One", "Two"], "references": ["pod-x"]}\n```'
    notes = step_notes(fenced)
    assert notes.findings == ("One", "Two")
    assert notes.references == ("pod-x",)

    prose = step_notes("- First finding\n\n* Second finding")
    assert prose.findings == ("First finding", "Second finding")

    many = step_notes(json.dumps({"findings": [f"Finding {n}" for n in range(10)]}))
    assert len(many.findings) == 6


def test_the_answer_rules_keep_every_quick_rule_but_the_shape() -> None:
    shape, no_forecast, *quick = INVESTIGATE_FORMAT_RULES
    assert tuple(quick) == ANSWER_FORMAT_RULES[1:]
    assert shape != ANSWER_FORMAT_RULES[0]
    assert "a colour is not a forecast" in no_forecast
    for rule in INVESTIGATE_FORMAT_RULES:
        assert rule in ANSWER_RULES
    assert "a red or amber status alone does not" in ANSWER_REMINDER


async def test_an_id_written_before_its_own_name_reads_as_the_name_once() -> None:
    """Seen live: "waits on CHK-102 (Refund edge cases)" read as
    "Refund edge cases (Refund edge cases)"."""
    reader = await (await _ask_service()).reader("demo", AS_OF)

    assert (
        reader.readable("Blocked on pod-payments (Payments Pod), then Payments Pod (pod-payments)")
        == "Blocked on Payments Pod, then Payments Pod"
    )
