"""An investigation plans what its question needs, and looks again when a step comes back thin.

In QA round V2 a manager asked Investigate why a project was at risk and what
to do first. The planner sent one step, the researcher came back with the
open merge requests, and the answer named neither the requirements with no
date nor the missing readiness criterion, and said "Not known: No
recommendations or next steps are stated in the notes." The planner now
covers the forecast, the undated requirements, readiness and the blockers for
such a question; it is asked once for more steps when it would answer from one
step or a thin one; and the answer names a first action from the notes.
These run the deepagents engine on a scripted fake model.
"""

from __future__ import annotations

import json
from datetime import date

from core.application.ask_investigation import (
    ANSWER_REMINDER,
    FIRST_ACTION_RULE,
    INVESTIGATE_FORMAT_RULES,
    ONE_STEP_REASON,
    PLAN_MORE_REMINDER,
    STEP_GUIDANCE,
    THIN_STEP_REASON,
    AnswerEvent,
    InvestigationService,
)
from core.domain.auth import Role
from core.domain.llm import LlmResponse, LlmToolCall
from core.ports.investigation import EngineAnswer, EnginePlan, EngineStepDone
from infra.adapters.agents.deep_investigation import MAIN_SYSTEM_PROMPT, RESEARCHER
from tests.unit.test_ask_investigation import _ask_service, _Engine, _Llm, _principal
from tests.unit.test_deep_investigation import (
    ANSWER,
    BLOCKERS,
    CHANGES,
    _events,
    _look,
    _notes,
    _response,
    _run,
    _ScriptedLlm,
    _Tool,
)

FORECAST = "Will Payments Pod make its committed date, and which requirements have no ETA?"
READINESS = "What is still missing before Payments Pod's project can go to production?"
TOO_SOON = json.dumps(
    {"answer": ["Payments Pod is at risk.", "Not known: what to do first."], "references": []}
)
_TOOLS = (_Tool("pod_blockers"), _Tool("delivery_forecast"), _Tool("release_readiness"))


def _reminders(llm: _ScriptedLlm) -> list[str]:
    """Every plan-more reminder the main agent was given, in order."""
    said = (PLAN_MORE_REMINDER.format(reason=r) for r in (ONE_STEP_REASON, THIN_STEP_REASON))
    reminders = set(said)
    return [
        turn.content
        for request in llm.of("investigation_main")
        for turn in request.turns
        if turn.role == "user" and turn.content in reminders
    ]


async def test_an_answer_from_one_step_is_asked_once_for_the_steps_it_left_out() -> None:
    llm = _ScriptedLlm(
        main=(
            _tasks_of(BLOCKERS),
            _response(TOO_SOON),
            _tasks_of(FORECAST, READINESS),
            _response(ANSWER),
        ),
        steps={BLOCKERS: (_look("pod_blockers"), _notes("CHK-103 blocked 9 days", "2 MRs open"))},
    )

    events = await _events(_run(llm, *_TOOLS))

    plans = [e for e in events if isinstance(e, EnginePlan)]
    assert plans[-1].steps == (BLOCKERS, FORECAST, READINESS)
    assert len([e for e in events if isinstance(e, EngineStepDone)]) == 3
    # The answer is the one written after every step, not the one from the first.
    assert events[-1] == EngineAnswer(text=ANSWER, trace_id="trace-deep")
    main = llm.of("investigation_main")
    assert len(main) == 4
    # Asked right after the notes, last, and once only.
    assert main[2].turns[-1].content == PLAN_MORE_REMINDER.format(reason=ONE_STEP_REASON)
    assert _reminders(llm) == [PLAN_MORE_REMINDER.format(reason=ONE_STEP_REASON)]


async def test_a_step_that_came_back_thin_is_looked_at_again() -> None:
    empty = _response(json.dumps({"findings": [], "references": [], "gaps": ["No activity"]}))
    llm = _ScriptedLlm(
        main=(
            _tasks_of(BLOCKERS, CHANGES),
            _response(TOO_SOON),
            _tasks_of(FORECAST),
            _response(ANSWER),
        ),
        steps={CHANGES: (_look(), empty)},
    )

    events = await _events(_run(llm, *_TOOLS))

    assert [e for e in events if isinstance(e, EnginePlan)][-1].steps == (
        BLOCKERS,
        CHANGES,
        FORECAST,
    )
    assert events[-1] == EngineAnswer(text=ANSWER, trace_id="trace-deep")
    assert _reminders(llm) == [PLAN_MORE_REMINDER.format(reason=THIN_STEP_REASON)]


async def test_the_planner_may_still_answer_after_the_reminder() -> None:
    llm = _ScriptedLlm(main=(_tasks_of(BLOCKERS), _response(TOO_SOON), _response(ANSWER)))

    events = await _events(_run(llm, *_TOOLS))

    assert events[-1] == EngineAnswer(text=ANSWER, trace_id="trace-deep")
    assert len(llm.of("investigation_main")) == 3


async def test_no_reminder_once_every_step_the_run_may_send_was_sent() -> None:
    llm = _ScriptedLlm(
        main=(_tasks_of(BLOCKERS), _tasks_of(CHANGES, FORECAST), _response(ANSWER)),
        steps={CHANGES: (_look(), _response(json.dumps({"findings": []})))},
    )

    events = await _events(_run(llm, *_TOOLS, max_steps=1))

    assert events[-1] == EngineAnswer(text=ANSWER, trace_id="trace-deep")
    assert _reminders(llm) == []
    assert len(llm.of("investigation_main")) == 3


async def test_two_steps_with_findings_answer_with_no_reminder() -> None:
    llm = _ScriptedLlm()

    await _events(_run(llm))

    assert _reminders(llm) == []
    assert len(llm.of("investigation_main")) == 2


def test_a_what_first_question_is_planned_across_everything_that_holds_the_date() -> None:
    assert "what to do first" in STEP_GUIDANCE
    assert "the delivery forecast and the requirements with no ETA or due date" in STEP_GUIDANCE
    assert "the release readiness of each project, release or pod" in STEP_GUIDANCE
    prompt = MAIN_SYSTEM_PROMPT.format(max_steps=3, follow_ups=2)
    assert "never answer from one thin step" in prompt
    assert FIRST_ACTION_RULE in INVESTIGATE_FORMAT_RULES
    assert "'First:' line if the question asks what to do" in ANSWER_REMINDER


async def test_the_first_action_reaches_the_reader_with_the_answer() -> None:
    answer = json.dumps(
        {
            "answer": [
                "Payments Pod is at risk because:",
                "• 3 requirements have no ETA or due date",
                "• Security review is missing, needed by 24 Nov",
                "First: Ira Novak gets owners and dates for the 3 undated requirements",
            ],
            "references": ["pod-payments"],
        }
    )
    engine = _Engine(
        [
            EnginePlan(steps=(FORECAST, READINESS)),
            EngineStepDone(
                index=0, notes=_notes("3 undated").text, tools_used=("delivery_forecast",)
            ),
            EngineStepDone(
                index=1, notes=_notes("1 missing").text, tools_used=("release_readiness",)
            ),
            EngineAnswer(text=answer, trace_id="t"),
        ]
    )
    service = InvestigationService(
        ask_service=await _ask_service(), llm_provider=_Llm(), engine=engine, model="m"
    )

    events = [
        event
        async for event in service.investigate(
            principal=_principal(Role.MGR),
            question="Why is Payments Pod at risk, and what should we do first?",
            correlation_id="c",
            as_of=date(2026, 10, 9),
        )
    ]

    (answered,) = [e for e in events if isinstance(e, AnswerEvent)]
    assert answered.view.answer.splitlines()[-1] == (
        "First: Ira Novak gets owners and dates for the 3 undated requirements"
    )


def _tasks_of(*steps: str) -> LlmResponse:
    """One task call per step, each with an id of its own, as a live model gives them."""
    return _response(
        calls=tuple(
            LlmToolCall(
                id=f"task-{abs(hash(step))}",
                name="task",
                arguments={"description": step, "subagent_type": RESEARCHER},
                arguments_json=json.dumps({"description": step, "subagent_type": RESEARCHER}),
            )
            for step in steps
        )
    )
