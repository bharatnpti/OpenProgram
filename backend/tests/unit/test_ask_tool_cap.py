"""An answer written when Ask ran out of tool steps never claims what it did not read.

In QA round V2 a scrum master asked what blocks a project. The quick answer's
tool loop hit its cap of three rounds (``tool_loop_cap_reached``), the model
was asked to answer with no tools and no word about the reads it never made,
and it said the project "has no blockers reported today" while a blocker was
open in one of its pods. Now the unrun reads are answered "Not run", the model
is told it ran out, a claim of absence no finished read shows is cut, and the
answer says what it could not check. Investigate holds its answer to the same.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.agents.tool_loop import NOT_RUN, ToolCallingAgent
from core.application.ask_investigation import (
    ANSWER_RULES,
    RESEARCH_SYSTEM_PROMPT,
    STEP_OUT_OF_STEPS,
    AnswerEvent,
    InvestigationService,
)
from core.application.ask_service import (
    ASK_CAP_NOTICE,
    ASK_MAX_TOOL_ITERATIONS,
    ASK_TOOL_GUIDANCE,
    without_unread_negatives,
)
from core.domain.auth import Role
from core.domain.graph import EdgeKind, GraphEdge, Pod, Project
from core.domain.llm import LlmRequest, LlmToolCall
from core.ports.investigation import (
    EngineAnswer,
    EnginePlan,
    EngineStepDone,
    EngineStepFailed,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeLlmProvider
from tests.unit.test_ask_investigation import _ask_service as _investigation_ask_service
from tests.unit.test_ask_investigation import _Engine, _Llm, _principal
from tests.unit.test_ask_service import AS_OF, _ask_service, _llm_response
from tests.unit.test_ask_service import _principal as _asker

QUESTION = "What is blocking Analytics Revamp?"
_CLAIMED = (
    "Analytics Revamp has no blockers reported today.\n"
    "No open blockers found for the Analytics Revamp project."
)


def _call(n: int, name: str, **arguments: str) -> LlmToolCall:
    return LlmToolCall(id=f"call-{n}", name=name, arguments=arguments)


async def _store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for node in (
        Project(tenant_id="demo", id="project-analytics", name="Analytics Revamp"),
        Pod(tenant_id="demo", id="pod-reporting", name="Reporting Pod"),
    ):
        await store.upsert_node(node)
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="project-analytics",
            to_node_id="pod-reporting",
            kind=EdgeKind.CONTAINS,
        )
    )
    return store


def _rounds(*calls: LlmToolCall, answer: str) -> FakeLlmProvider:
    """One round per call, then the answer written at the cap."""
    return FakeLlmProvider(
        responses=[*(_llm_response(tool_calls=(call,)) for call in calls), _llm_response(answer)]
    )


async def _ask(llm: FakeLlmProvider, *, rounds: int = 2) -> tuple[str, list[LlmRequest]]:
    service = _ask_service(await _store(), llm)
    service._tool_agent = ToolCallingAgent(  # noqa: SLF001 -- a short cap for the test
        llm_provider=llm, max_tool_iterations=rounds, cap_notice=ASK_CAP_NOTICE
    )
    view = await service.ask(
        principal=_asker(Role.SM),
        question=QUESTION,
        correlation_id="ask-test",
        as_of=AS_OF,
    )
    return view.answer, llm.requests


async def test_the_answer_at_the_cap_says_what_it_could_not_check_and_claims_no_absence() -> None:
    llm = _rounds(
        _call(1, "search_graph_nodes", query="Analytics Revamp"),
        _call(2, "graph_neighbors", node_id="project-analytics"),
        _call(3, "pod_blockers", pod_id="pod-reporting"),
        answer=json.dumps({"answer": _CLAIMED, "references": ["project-analytics"]}),
    )

    answer, requests = await _ask(llm)

    assert answer == "I ran out of steps before checking the open blockers of Reporting Pod."
    final = requests[-1]
    # The model was told: the read it asked for came back as not run.
    assert final.tools == ()
    assert final.tool_calls[-1].name == "pod_blockers"
    assert final.tool_results[-1].content == NOT_RUN
    assert ASK_CAP_NOTICE in (final.system or "")


async def test_an_answer_that_says_it_ran_out_keeps_its_own_words_and_what_it_read() -> None:
    written = (
        "I ran out of steps before checking every pod's open blockers.\n"
        "• Analytics Revamp is amber: 2 open risks on aged MRs\n"
        "• No blockers in Reporting Pod"
    )
    llm = _rounds(
        _call(1, "open_risks"),
        _call(2, "status_reasons", node_id="project-analytics"),
        _call(3, "pod_blockers"),
        answer=json.dumps({"answer": written, "references": []}),
    )

    answer, _ = await _ask(llm)

    # Its own caveat stands, so none is added; the unread claim still goes.
    assert answer == (
        "I ran out of steps before checking every pod's open blockers.\n"
        "• Analytics Revamp is amber: 2 open risks on aged MRs"
    )


async def test_a_claim_a_finished_read_shows_stays_and_the_unread_one_is_named() -> None:
    llm = _rounds(
        _call(1, "pod_blockers", pod_id="pod-reporting"),
        _call(2, "open_risks"),
        _call(3, "delivery_forecast", node_id="project-analytics"),
        answer=json.dumps(
            {"answer": "Nothing is blocking Analytics Revamp.\n• 0 open risks", "references": []}
        ),
    )

    answer, _ = await _ask(llm)

    assert answer == (
        "I ran out of steps before checking the delivery forecast of Analytics Revamp.\n"
        "Nothing is blocking Analytics Revamp.\n"
        "• 0 open risks"
    )


async def test_an_unread_node_is_never_named_by_its_raw_id() -> None:
    llm = _rounds(
        _call(1, "open_risks"),
        _call(2, "open_risks"),
        _call(3, "pod_checkins", pod_id="pod-nowhere"),
        answer=json.dumps({"answer": "Analytics Revamp is amber.", "references": []}),
    )

    answer, _ = await _ask(llm)

    assert answer.splitlines()[0] == "I ran out of steps before checking the check-ins."
    assert "pod-nowhere" not in answer


async def test_a_quick_answer_takes_five_rounds_by_default_and_the_setting_agrees() -> None:
    calls = [_call(n, "open_risks") for n in range(ASK_MAX_TOOL_ITERATIONS + 1)]
    llm = _rounds(*calls, answer=json.dumps({"answer": "Analytics Revamp is amber."}))
    service = _ask_service(await _store(), llm)

    await service.ask(
        principal=_asker(Role.MGR),
        question=QUESTION,
        correlation_id="ask-test",
        as_of=AS_OF,
    )

    # Five rounds of reads, the round past the cap, then the answer.
    assert len(llm.requests) == ASK_MAX_TOOL_ITERATIONS + 2
    assert ASK_MAX_TOOL_ITERATIONS == 5
    assert Settings.model_fields["ask_max_tool_iterations"].default == ASK_MAX_TOOL_ITERATIONS


def test_the_tool_guidance_sends_a_blocker_question_to_pod_blockers() -> None:
    assert "What blocks a project or pod is answered from pod_blockers" in ASK_TOOL_GUIDANCE
    assert "Never say anything has no blockers" in ASK_TOOL_GUIDANCE


@pytest.mark.parametrize(
    "line",
    [
        "Analytics Revamp has no blockers reported today.",
        "No open blockers found for the project.",
        "• Nothing is blocking Reporting Pod",
        "Nobody in Reporting Pod is blocked.",
        "Reporting Pod is not blocked.",
        "It doesn't have any blockers.",
        "Blockers: none",
        "There are no open risks.",
        "Nothing is missing before production.",
        "Analytics Revamp is ready for production.",
    ],
)
def test_a_claim_of_absence_goes_when_nothing_read_it(line: str) -> None:
    answer = f"Analytics Revamp is amber because:\n{line}"

    assert without_unread_negatives(answer, read=()) == "Analytics Revamp is amber because:"


@pytest.mark.parametrize(
    "line",
    [
        "• 1 open blocker: INS-2 (Raj Iyer), 4 days, no reviewer assigned",
        "No reviewer is assigned for INS-2, which blocks the MR.",
        "• 2 open risks on aged MRs",
        "No status reply: Sofia Bergmann",
    ],
)
def test_what_an_answer_does_say_stays(line: str) -> None:
    answer = f"Analytics Revamp is amber because:\n{line}"

    assert without_unread_negatives(answer, read=()) == answer


def test_only_the_sentence_goes_and_a_bullet_keeps_its_mark() -> None:
    answer = "• No blockers in Reporting Pod. 2 risks are open."

    assert without_unread_negatives(answer, read=()) == "• 2 risks are open."
    # Read, and nothing of that kind left unrun: it stands.
    assert without_unread_negatives(answer, read={"pod_blockers"}) == answer
    # Read, but another read of that kind never ran: it goes.
    assert (
        without_unread_negatives(answer, read={"pod_blockers"}, pending={"pod_blockers"})
        == "• 2 risks are open."
    )


# ---- Investigate ----------------------------------------------------------------------


async def _investigate(*script: object) -> AnswerEvent:
    engine = _Engine(list(script))  # type: ignore[arg-type]
    service = InvestigationService(
        ask_service=await _investigation_ask_service(),
        llm_provider=_Llm(),
        engine=engine,
        model="m",
    )
    events = [
        event
        async for event in service.investigate(
            principal=_principal(Role.MGR),
            question=QUESTION,
            correlation_id="c",
            as_of=date(2026, 10, 9),
        )
    ]
    (answered,) = [event for event in events if isinstance(event, AnswerEvent)]
    return answered


async def test_a_step_out_of_steps_leaves_no_claim_of_absence_and_is_named() -> None:
    answered = await _investigate(
        EnginePlan(steps=("What are the open risks?", "What blocks Payments Pod?")),
        EngineStepDone(index=0, notes='{"findings": ["2 open risks"]}', tools_used=("open_risks",)),
        EngineStepFailed(index=1, tools_used=("search_graph_nodes",), out_of_steps=True),
        EngineAnswer(
            text=json.dumps(
                {
                    "answer": ["Payments Pod is amber.", "• 2 open risks", "• No open blockers"],
                    "references": [],
                }
            ),
            trace_id="t",
        ),
    )

    assert answered.view.answer == (
        "Payments Pod is amber.\n"
        "• 2 open risks\n"
        "Not known: I ran out of steps before checking “What blocks Payments Pod?”."
    )
    assert answered.steps[1].error == STEP_OUT_OF_STEPS


async def test_a_step_never_run_is_out_of_steps_and_a_failed_one_could_not_be_looked_up() -> None:
    answered = await _investigate(
        EnginePlan(steps=("What blocks Payments Pod?", "What changed?", "Who owns CHK-103?")),
        EngineStepDone(
            index=0, notes='{"findings": ["CHK-103 blocked 9 days"]}', tools_used=("pod_blockers",)
        ),
        EngineStepFailed(index=1),
        EngineAnswer(
            text=json.dumps(
                {"answer": ["Payments Pod is late because:", "• CHK-103 blocked 9 days"]}
            ),
            trace_id="t",
        ),
    )

    assert [step.status for step in answered.steps] == ["done", "failed", "failed"]
    assert answered.steps[2].error == STEP_OUT_OF_STEPS
    assert answered.view.answer.splitlines()[-1] == (
        "Not known: I ran out of steps before checking “Who owns CHK-103?”; "
        "“What changed?” could not be looked up."
    )


async def test_a_researcher_that_ran_out_says_so_in_its_gaps_and_the_answer_follows() -> None:
    notes = json.dumps(
        {
            "findings": ["2 open risks on aged MRs"],
            "gaps": ["Ran out of steps before checking the open blockers of Payments Pod"],
        }
    )
    answered = await _investigate(
        EnginePlan(steps=("What blocks Payments Pod?",)),
        EngineStepDone(index=0, notes=notes, tools_used=("open_risks",)),
        EngineAnswer(
            text=json.dumps(
                {
                    "answer": [
                        "Payments Pod is amber.",
                        "• 2 open risks on aged MRs",
                        "• Nothing is blocking it",
                    ]
                }
            ),
            trace_id="t",
        ),
    )

    assert answered.view.answer == (
        "Payments Pod is amber.\n"
        "• 2 open risks on aged MRs\n"
        "Not known: I ran out of steps before checking “What blocks Payments Pod?”."
    )


async def test_an_investigation_whose_steps_all_finished_is_left_as_written() -> None:
    answered = await _investigate(
        EnginePlan(steps=("What blocks Payments Pod?",)),
        EngineStepDone(index=0, notes='{"findings": ["none open"]}', tools_used=("pod_blockers",)),
        EngineAnswer(
            text=json.dumps({"answer": ["Payments Pod is green.", "• No open blockers"]}),
            trace_id="t",
        ),
    )

    assert answered.view.answer == "Payments Pod is green.\n• No open blockers"


def test_the_investigation_prompts_say_what_running_out_means() -> None:
    assert "Ran out of steps before checking" in RESEARCH_SYSTEM_PROMPT
    assert "never a negative such as no blockers" in RESEARCH_SYSTEM_PROMPT
    assert "I ran out of steps before checking" in ANSWER_RULES


def test_the_route_reads_its_cap_from_the_setting(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = settings.model_copy(
        update={"dev_principal_roles": "mgr", "llm_provider": "fake", "ask_max_tool_iterations": 1}
    )
    app = create_app(settings=configured, registry=ServiceRegistry(configured))
    llm = _rounds(
        _call(1, "open_risks"),
        _call(2, "pod_blockers"),
        answer=json.dumps({"answer": "Nothing is blocking anything."}),
    )
    monkeypatch.setattr(app.state.registry, "llm_provider", lambda: llm)

    with TestClient(app) as client:
        response = client.post("/ask", json={"question": QUESTION})

    assert response.status_code == 200, response.text
    # One round, the round past the cap, then the answer.
    assert len(llm.requests) == 3
    assert response.json()["answer"] == (
        "I ran out of steps before checking the open blockers of every pod."
    )
