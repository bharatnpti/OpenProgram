"""Investigate quotes the forecast's count of undated requirements, with their keys.

In a QA run one of two runs of "What should I do first to make <project>'s
date?" said only one requirement lacked an ETA, while Delivery counted six:
its step about undated requirements read release readiness, and the step that
read the forecast kept no count in its notes. What delivery_forecast returns
is now recorded as the steps run (AnswerFacts), said to the answer writer as
facts to quote, and held against the answer whatever the model wrote
(held_to_the_facts), the way held_to_the_notes holds unread absences.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import pytest

from core.application.ask_investigation import (
    ANSWER_REMINDER,
    FACTS_REMINDER,
    RESEARCH_SYSTEM_PROMPT,
    AnswerEvent,
    AnswerFacts,
    InvestigationService,
    UndatedRequirements,
    answer_reminder,
    held_to_the_facts,
)
from core.application.ask_service import ASK_SYSTEM_PROMPT, AskResponseView, AskService
from core.application.delivery_scope import DeliveryScopeService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.persona_views import PersonaViewService
from core.domain.auth import Principal, Role
from core.domain.graph import Task
from core.domain.llm import LlmToolCall
from core.ports.investigation import (
    EngineAnswer,
    EngineEvent,
    EnginePlan,
    EngineStepDone,
    InvestigationRun,
)
from infra.adapters.agents.deep_investigation import DeepAgentInvestigationEngine
from tests.contract.fakes import FakeLlmProvider
from tests.unit.test_ask_delivery_forecast import TENANT, TODAY, _forecasts
from tests.unit.test_deep_investigation import _notes, _response, _ScriptedLlm, _tasks

QUESTION = "What should I do first to make Checkout Revamp's date?"
STEP = "Find the delivery forecast for Checkout Revamp and its requirements with no ETA."
CHECKOUT = UndatedRequirements(
    scope_name="Checkout Revamp", count=3, open=3, keys=("CHK-2", "CHK-3", "CHK-4")
)
SIX = UndatedRequirements(
    scope_name="Checkout Revamp",
    count=6,
    open=13,
    keys=("CHK-1", "CHK-5", "CHK-7", "CHK-8", "CHK-9", "CHK-12"),
)


def _facts(*undated: UndatedRequirements) -> AnswerFacts:
    facts = AnswerFacts(as_of=TODAY)
    for fact in undated:
        facts.undated[fact.scope_name.casefold()] = fact
    return facts


def _held(answer: str, *undated: UndatedRequirements) -> str:
    view = AskResponseView(answer=answer, references=(), tools_used=(), trace_id="t")
    return held_to_the_facts(view, _facts(*undated)).answer


# ---- the hold ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "held"),
    [
        # The run that failed: a count the forecast does not give.
        (
            "• Only one requirement lacks an ETA or due date.",
            "• 3 of 3 open requirements of Checkout Revamp have no ETA or due date "
            "(CHK-2, CHK-3 and CHK-4).",
        ),
        # The run that passed, without its keys.
        (
            "• 3 open requirements have no ETA or due date (issue keys not provided), "
            "which adds uncertainty.",
            "• 3 of 3 open requirements of Checkout Revamp have no ETA or due date "
            "(CHK-2, CHK-3 and CHK-4), which adds uncertainty.",
        ),
        # Named as a thing to act on, the clause stays one.
        (
            "First: Ira Novak dates the 1 requirement with no ETA or due date.",
            "First: Ira Novak dates the 3 of 3 open requirements of Checkout Revamp with no "
            "ETA or due date (CHK-2, CHK-3 and CHK-4).",
        ),
        (
            "• Checkout Revamp has 2 undated requirements.",
            "• Checkout Revamp has 3 of 3 open requirements with no ETA or due date "
            "(CHK-2, CHK-3 and CHK-4).",
        ),
        (
            "• No requirement lacks an ETA.",
            "• 3 of 3 open requirements of Checkout Revamp have no ETA or due date "
            "(CHK-2, CHK-3 and CHK-4).",
        ),
    ],
)
def test_a_count_the_forecast_does_not_give_is_the_forecasts_with_its_keys(
    line: str, held: str
) -> None:
    answer = f"Checkout Revamp is at risk:\n{line}"

    assert _held(answer, CHECKOUT) == f"Checkout Revamp is at risk:\n{held}"


def test_the_forecasts_own_count_with_its_keys_is_left_as_written() -> None:
    answer = (
        "Checkout Revamp is at risk:\n"
        "• 3 requirements have no ETA or due date: CHK-2, CHK-3 and CHK-4.\n"
        "First: date them."
    )

    assert _held(answer, CHECKOUT) == answer


def test_more_keys_than_an_answer_names_are_counted() -> None:
    many = UndatedRequirements(
        scope_name="Checkout Revamp",
        count=14,
        open=20,
        keys=tuple(f"CHK-{n}" for n in range(1, 15)),
    )

    held = _held("Checkout Revamp is at risk:\n• Checkout Revamp: 2 have no ETA.", many)
    assert held == "Checkout Revamp is at risk:\n• Checkout Revamp: 2 have no ETA."

    held = _held("Checkout Revamp is at risk:\n• Checkout Revamp: 2 issues have no ETA.", many)

    # Its line names the project, so the clause does not again.
    assert held.splitlines()[1] == (
        "• Checkout Revamp: 14 of 20 open requirements have no ETA or due date (CHK-1, CHK-2, "
        "CHK-3, CHK-4, CHK-5, CHK-6, CHK-7, CHK-8, CHK-9, CHK-10 and 4 more)."
    )


def test_a_what_first_answer_that_leaves_them_out_gets_them_before_its_first_action() -> None:
    answer = (
        "To make Checkout Revamp's date, start with the security review.\n"
        "• Security review: missing, needed before production.\n"
        "First: Create and schedule the security review."
    )

    assert _held(answer, SIX).splitlines() == [
        "To make Checkout Revamp's date, start with the security review.",
        "• Security review: missing, needed before production.",
        "• 6 of 13 open requirements of Checkout Revamp have no ETA or due date "
        "(CHK-1, CHK-5, CHK-7, CHK-8, CHK-9 and CHK-12).",
        "First: Create and schedule the security review.",
    ]


def test_an_answer_with_no_forecast_read_or_no_first_action_is_left_alone() -> None:
    no_first = "Checkout Revamp is at risk:\n• Security review: missing."
    with_first = f"{no_first}\nFirst: Create the security review."

    assert _held(no_first, SIX) == no_first
    assert _held(with_first) == with_first
    # Nothing undated: nothing to add.
    assert (
        _held(
            with_first, UndatedRequirements(scope_name="Checkout Revamp", count=0, open=4, keys=())
        )
        == with_first
    )


def test_a_count_no_one_project_owns_is_left_alone() -> None:
    loyalty = UndatedRequirements(scope_name="Loyalty", count=1, open=2, keys=("LOY-4",))
    answer = "Checkout Revamp and Loyalty are at risk:\n• Only one requirement lacks an ETA."

    assert _held(answer, CHECKOUT, loyalty) == answer
    # Named in its line, it is that project's.
    named = "Checkout Revamp and Loyalty are at risk:\n• Loyalty: 2 requirements have no ETA."
    assert _held(named, CHECKOUT, loyalty).splitlines()[1] == (
        "• Loyalty: 1 of 2 open requirements has no ETA or due date (LOY-4)."
    )


def test_a_researcher_counts_undated_requirements_from_the_forecast_not_readiness() -> None:
    rule = (
        "is answered from delivery_forecast's open_without_date and open_without_date_keys, "
        "never from release readiness: a release criterion is not a requirement."
    )

    assert rule in RESEARCH_SYSTEM_PROMPT
    assert rule in ASK_SYSTEM_PROMPT


def test_the_facts_are_said_after_the_answer_rules_only_when_there_are_any() -> None:
    assert answer_reminder(()) == ANSWER_REMINDER
    assert answer_reminder((SIX.sentence(),)) == (
        f"{ANSWER_REMINDER} {FACTS_REMINDER} 6 of 13 open requirements of Checkout Revamp "
        "have no ETA or due date (CHK-1, CHK-5, CHK-7, CHK-8, CHK-9 and CHK-12)."
    )


# ---- recorded from the tool as the steps run ---------------------------------------------


async def _ask_service() -> AskService:
    """Ask over the forecast fixture, with CHK-2 and CHK-4 left with no due date."""
    forecasts, store, registry = await _forecasts()
    for key, status, state in (("CHK-2", "In Progress", "in_progress"), ("CHK-4", "To Do", "todo")):
        await store.upsert_node(
            Task(
                tenant_id=TENANT,
                id=key,
                name=f"{key} work",
                metadata={"key": key, "status": status, "state": state, "fix_versions": "R1"},
            )
        )
    return AskService(
        llm_provider=FakeLlmProvider(),
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
        risk_service=registry.risk_service(),
        forecast_service=forecasts,
        delivery_scope_service=DeliveryScopeService(store, store, today=lambda: TODAY),
        release_readiness_service=registry.release_readiness_service(),
        model="test-model",
    )


_WRONG = json.dumps(
    {
        "answer": [
            "To make Checkout Revamp's date, start with the security review.",
            "• Security review: missing, needed before production.",
            "• Only one requirement lacks an ETA or due date.",
            "First: Create and schedule the security review.",
        ],
        "references": ["checkout"],
    }
)
_HELD = [
    "To make Checkout Revamp's date, start with the security review.",
    "• Security review: missing, needed before production.",
    "• 3 of 3 open requirements of Checkout Revamp have no ETA or due date "
    "(CHK-2, CHK-3 and CHK-4).",
    "First: Create and schedule the security review.",
]
_FACT = (
    "3 of 3 open requirements of Checkout Revamp have no ETA or due date (CHK-2, CHK-3 and CHK-4)."
)


@dataclass
class _ForecastingEngine:
    """A researcher that reads the forecast and notes something else, then the bad answer."""

    seen: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "forecasting"

    async def run(self, run: InvestigationRun) -> AsyncGenerator[EngineEvent]:
        yield EnginePlan(steps=(STEP,))
        forecast = next(tool for tool in run.tools if tool.name == "delivery_forecast")
        await forecast.run({"node_id": "checkout"})
        yield EngineStepDone(
            index=0,
            notes=json.dumps({"findings": ["Only the security review has no ETA or due date."]}),
            tools_used=("delivery_forecast",),
        )
        self.seen.append(run.answer_facts())
        yield EngineAnswer(text=_WRONG, trace_id="t")


async def _answer(service: InvestigationService) -> AnswerEvent:
    events = [
        event
        async for event in service.investigate(
            principal=Principal(tenant_id=TENANT, subject="mgr", roles=frozenset({Role.MGR})),
            question=QUESTION,
            correlation_id="ask-investigate:test",
            as_of=TODAY,
        )
    ]
    (answered,) = [event for event in events if isinstance(event, AnswerEvent)]
    return answered


async def test_the_forecasts_count_reaches_the_answer_writer_and_holds_the_answer() -> None:
    engine = _ForecastingEngine()
    service = InvestigationService(
        ask_service=await _ask_service(), llm_provider=_ScriptedLlm(), engine=engine, model="m"
    )

    answered = await _answer(service)

    assert engine.seen == [(_FACT[:1].upper() + _FACT[1:],)]
    assert answered.view.answer.splitlines() == _HELD
    # The step's own note is shown as the researcher wrote it.
    assert answered.steps[0].findings == ("Only the security review has no ETA or due date.",)


async def test_a_forecast_of_another_day_or_a_pods_part_is_no_fact_for_today() -> None:
    facts = AnswerFacts(as_of=TODAY)
    project = {
        "project_name": "Checkout Revamp",
        "open_requirements": 3,
        "open_without_date": 3,
        "open_without_date_keys": ["CHK-2", "CHK-3", "CHK-4"],
    }

    facts.record(json.dumps({"as_of": "2026-09-21", "projects": [project]}))
    facts.record(
        json.dumps({"as_of": TODAY.isoformat(), "pod_id": "pod-pay", "projects": [project]})
    )
    facts.record(json.dumps({"error": "not yours"}))
    facts.record("not json")
    assert facts.lines() == ()

    facts.record(json.dumps({"as_of": TODAY.isoformat(), "projects": [project]}))
    assert facts.lines() == (_FACT[:1].upper() + _FACT[1:],)


async def test_on_deepagents_with_the_fake_model_the_answer_is_told_and_held() -> None:
    """The researcher reads the forecast and notes only the security review; the model errs."""
    forecast = LlmToolCall(
        id="call-forecast",
        name="delivery_forecast",
        arguments={"node_id": "checkout"},
        arguments_json=json.dumps({"node_id": "checkout"}),
    )
    llm = _ScriptedLlm(
        main=(_tasks(STEP), _response(_WRONG)),
        steps={
            STEP: (
                _response(calls=(forecast,)),
                _notes("Only the security review has no ETA or due date."),
            )
        },
    )
    service = InvestigationService(
        ask_service=await _ask_service(),
        llm_provider=llm,
        engine=DeepAgentInvestigationEngine(),
        model="m",
    )

    answered = await _answer(service)

    told = [
        turn.content
        for request in llm.of("investigation_main")
        for turn in request.turns
        if turn.role == "user" and FACTS_REMINDER in turn.content
    ]
    assert told and all(text.endswith(_FACT[:1].upper() + _FACT[1:]) for text in told)
    assert answered.view.answer.splitlines() == _HELD
