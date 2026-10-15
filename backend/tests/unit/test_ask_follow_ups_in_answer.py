"""A follow-ups list the model wrote into the answer never reaches the reader as text.

In a QA run Ora's third answer ended on the raw line
'follow_ups: ["What is Noah Weber's workload?", ...]' inside the bubble: the
reply's answer field held the list, the reply had no follow_ups field, and the
parser cut a "References:" line but not this one. The console then offered its
generic questions instead of the three the model wrote. These are the shapes
such a reply comes in.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.ask_investigation import (
    ANSWER_REMINDER,
    ANSWER_RULES,
    AnswerEvent,
    InvestigationService,
)
from core.application.ask_service import (
    ASK_SYSTEM_PROMPT,
    OWN_FIELDS_RULE,
    _parse_answer,
    _prompt,
)
from core.domain.auth import Principal, Role
from core.domain.graph import Pod, Task
from core.domain.llm import LlmResponse, TokenUsage
from core.ports.investigation import EngineAnswer, EnginePlan, EngineStepDone
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeLlmProvider
from tests.unit.test_ask_investigation import _ask_service, _Engine, _Llm

ASKED = ("What is Noah Weber's workload?", "How many tasks are open?", "Is CHK-8 at risk?")
_INLINE = 'follow_ups: ["What is Noah Weber\'s workload?","How many tasks are open?",' + (
    '"Is CHK-8 at risk?"]'
)
_VERDICT = 'CHK-8 "Refund edge cases" has an early due date because:'
_BULLETS = (
    "• The latest team ETA is 14 Oct, earlier than the project's date\n"
    "• It is in progress under Payments Pod"
)
_SHOWN = f"{_VERDICT}\n{_BULLETS}\nNo other status or risk signals found for it."


def _with(lines: str) -> str:
    return f"{_SHOWN}\n\n{lines}"


@pytest.mark.parametrize(
    ("reply", "references"),
    [
        # As seen: the list is the answer field's last line, and there is no field.
        (
            json.dumps({"answer": _with(_INLINE), "references": ["CHK-8", "pod-payments"]}),
            ("CHK-8", "pod-payments"),
        ),
        # Prose, the list above the references line.
        (f"{_with(_INLINE)}\nReferences: [CHK-8, pod-payments]", ()),
        # YAML-ish: a bare heading and one question to a line, quoted or not.
        (
            json.dumps(
                {
                    "answer": _with(
                        "follow_ups:\n- What is Noah Weber's workload?\n"
                        '- "How many tasks are open?"\n  - Is CHK-8 at risk?'
                    ),
                    "references": ["CHK-8"],
                }
            ),
            ("CHK-8",),
        ),
        # A JSON array over several lines, as a model pretty-prints it.
        (
            _with(
                'follow_ups: [\n  "What is Noah Weber\'s workload?",\n'
                '  "How many tasks are open?",\n  "Is CHK-8 at risk?"\n]'
            ),
            (),
        ),
        # A JSON key in prose, and the list under a bare heading.
        (_with('"follow_ups":\n' + json.dumps(list(ASKED))), ()),
        # At the end of the answer's last line.
        (
            json.dumps(
                {
                    "answer": f"{_SHOWN} {_INLINE}",
                    "references": ["CHK-8"],
                }
            ),
            ("CHK-8",),
        ),
        # Investigate's answer is an array of lines: the list as one of them.
        (
            json.dumps(
                {
                    "answer": [_VERDICT, *_BULLETS.split("\n"), _SHOWN.split("\n")[-1], _INLINE],
                    "references": ["CHK-8"],
                }
            ),
            ("CHK-8",),
        ),
        # In words, numbered: every item is a question, so it is the list.
        (
            _with(
                "**Follow-up questions:**\n1. What is Noah Weber's workload?\n"
                "2. How many tasks are open?\n3. Is CHK-8 at risk?"
            ),
            (),
        ),
        # Beside the object, after it.
        (
            json.dumps({"answer": _SHOWN, "references": ["CHK-8"]}) + "\n" + _INLINE,
            ("CHK-8",),
        ),
        # A camel-cased key, Python's quotes.
        (_with(f"followUps = {list(ASKED)!r}"), ()),
    ],
)
def test_a_follow_ups_list_in_the_answer_is_cut_and_becomes_the_follow_ups(
    reply: str, references: tuple[str, ...]
) -> None:
    parsed = _parse_answer(reply)

    assert parsed.answer == _SHOWN
    assert "follow" not in parsed.answer.lower()
    assert parsed.follow_ups == ASKED
    assert parsed.references == references


def test_the_follow_ups_field_wins_and_a_list_restated_in_the_answer_still_goes() -> None:
    reply = json.dumps(
        {"answer": _with(_INLINE), "references": [], "follow_ups": ["Who owns CHK-8?"]}
    )

    parsed = _parse_answer(reply)

    assert (parsed.answer, parsed.follow_ups) == (_SHOWN, ("Who owns CHK-8?",))


def test_a_list_cut_off_part_way_keeps_the_questions_it_finished() -> None:
    parsed = _parse_answer(_with('follow_ups: ["What is Noah Weber\'s workload?", "How ma'))

    assert (parsed.answer, parsed.follow_ups) == (_SHOWN, ASKED[:1])


@pytest.mark.parametrize(
    "line",
    [
        "Follow-ups: 2 open, the oldest on CHK-8 (5 days)",
        "Follow up: Noah Weber reviews checkout-api !3",
        "• Follow-up questions to Asha Rao are still open",
        "Next questions: none from the team",
    ],
)
def test_an_answer_line_that_only_reads_like_a_heading_stays(line: str) -> None:
    reply = json.dumps({"answer": f"{_VERDICT}\n{line}", "references": []})

    parsed = _parse_answer(reply)

    assert parsed.answer == f"{_VERDICT}\n{line}"
    assert parsed.follow_ups == ()


def test_the_prompts_name_follow_ups_a_field_of_their_own() -> None:
    assert OWN_FIELDS_RULE in ASK_SYSTEM_PROMPT
    assert OWN_FIELDS_RULE in ANSWER_RULES
    assert "Do not restate references inside answer" not in ASK_SYSTEM_PROMPT
    # Said again right before the question, where a rule holds best.
    assert "follow_ups, never inside answer" in _prompt("Why is CHK-8 early?", date(2026, 10, 11))
    assert "follow_ups, never inside answer" in ANSWER_REMINDER


def _response(text: str) -> LlmResponse:
    return LlmResponse(
        tenant_id="demo",
        text=text,
        model="m",
        usage=TokenUsage(
            prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0.0, latency_ms=1
        ),
        trace_id="t",
    )


async def test_the_ask_route_answers_without_the_list_and_offers_its_questions(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Pod(tenant_id="demo", id="pod-payments", name="Payments Pod"))
    await store.upsert_node(
        Task(tenant_id="demo", id="CHK-8", name="Refund edge cases", metadata={"key": "CHK-8"})
    )
    role = settings.model_copy(update={"dev_principal_roles": "exec", "llm_provider": "fake"})
    app = create_app(settings=role, registry=ServiceRegistry(role, graph_store=store))
    reply = json.dumps({"answer": _with(_INLINE), "references": ["CHK-8"]})
    llm = FakeLlmProvider(responses=[_response(reply)])
    monkeypatch.setattr(app.state.registry, "llm_provider", lambda: llm)

    with TestClient(app) as client:
        response = client.post("/ask", json={"question": "Why is CHK-8 early?"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == _SHOWN
    assert body["follow_ups"] == list(ASKED)


async def test_an_investigation_answers_without_the_list_and_offers_its_questions() -> None:
    answer = json.dumps(
        {"answer": [*_SHOWN.split("\n"), _INLINE], "references": ["CHK-8", "pod-payments"]}
    )
    engine = _Engine(
        [
            EnginePlan(steps=("Why is CHK-8 due early?",)),
            EngineStepDone(index=0, notes='{"findings": ["ETA 14 Oct"]}', tools_used=()),
            EngineAnswer(text=answer, trace_id="trace"),
        ]
    )
    service = InvestigationService(
        ask_service=await _ask_service(), llm_provider=_Llm(), engine=engine, model="m"
    )

    events = [
        event
        async for event in service.investigate(
            principal=Principal(tenant_id="demo", subject="U1001", roles=frozenset({Role.MGR})),
            question="Why is CHK-8 due early?",
            correlation_id="c",
            as_of=date(2026, 10, 9),
        )
    ]

    (answered,) = [event for event in events if isinstance(event, AnswerEvent)]
    assert answered.view.answer == _SHOWN
    assert answered.view.follow_ups == ASKED
