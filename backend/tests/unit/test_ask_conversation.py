from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.ask_conversation import (
    COMPACT_AT_TURNS,
    KEEP_TURNS,
    MAX_TURNS,
    SUMMARY_SYSTEM_PROMPT,
    AskConversation,
    AskTurn,
    context_for,
)
from core.application.ask_service import AskResponseView, _parse_answer, node_label
from core.domain.graph import Pod, Task
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeLlmProvider


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


@dataclass
class _Summarizer:
    reply: str | Exception = "Checkout Revamp is red: three blockers in Payments Pod."
    requests: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return _response(self.reply)


def _exchanges(count: int) -> tuple[AskTurn, ...]:
    return tuple(
        turn
        for n in range(count)
        for turn in (
            AskTurn(role="user", content=f"Question {n}?"),
            AskTurn(role="assistant", content=f"Answer {n}."),
        )
    )


async def _context(conversation: AskConversation | None, llm: _Summarizer):  # noqa: ANN202
    return await context_for(
        conversation, llm=llm, model="m", tenant_id="demo", correlation_id="ask:test"
    )


async def test_a_first_question_has_no_context_and_asks_for_no_summary() -> None:
    llm = _Summarizer()

    context = await _context(None, llm)

    assert (context.summary, context.turns, context.summarized) == (None, (), 0)
    assert context.messages() == ()
    assert llm.requests == []


async def test_a_short_conversation_goes_as_it_was_said_after_its_summary() -> None:
    llm = _Summarizer()
    turns = _exchanges(2)

    context = await _context(AskConversation(summary="Earlier: Identity is red.", turns=turns), llm)

    assert context.turns == turns
    assert context.summarized == 0
    assert [(m.role, m.content) for m in context.messages()] == [
        ("user", "Earlier in this conversation, in short: Earlier: Identity is red."),
        ("user", "Question 0?"),
        ("assistant", "Answer 0."),
        ("user", "Question 1?"),
        ("assistant", "Answer 1."),
    ]
    assert llm.requests == []


async def test_a_long_conversation_folds_its_older_turns_into_the_summary() -> None:
    llm = _Summarizer()
    turns = _exchanges(COMPACT_AT_TURNS // 2 + 1)

    context = await _context(AskConversation(summary="Before: Identity red.", turns=turns), llm)

    assert context.turns == turns[-KEEP_TURNS:]
    assert context.summarized == len(turns) - KEEP_TURNS
    assert context.summary == "Checkout Revamp is red: three blockers in Payments Pod."
    (request,) = llm.requests
    assert request.system == SUMMARY_SYSTEM_PROMPT
    assert request.metadata["purpose"] == "conversation_summary"
    assert "The summary so far: Before: Identity red." in request.prompt
    assert "Asker: Question 0?" in request.prompt
    assert "Question 6?" not in request.prompt  # a recent turn is kept, not folded


async def test_a_summary_that_fails_keeps_the_recent_turns_and_the_old_summary() -> None:
    llm = _Summarizer(reply=RuntimeError("down"))
    turns = _exchanges(COMPACT_AT_TURNS)

    context = await _context(AskConversation(summary="Old.", turns=turns), llm)

    assert context.summary == "Old."
    assert context.turns == turns[-KEEP_TURNS:]
    assert context.summarized == len(turns) - KEEP_TURNS


async def test_an_answer_whose_question_is_gone_is_not_sent() -> None:
    turns = (AskTurn(role="assistant", content="Orphan."), *_exchanges(1))

    context = await _context(AskConversation(turns=turns), _Summarizer())

    assert context.turns[0] == AskTurn(role="user", content="Question 0?")


def test_follow_ups_are_read_from_the_reply_or_from_what_is_left_of_it() -> None:
    whole = json.dumps(
        {"answer": "Pod is red.", "references": [], "follow_ups": ["Who owns CHK-8?", 4, ""]}
    )
    cut_off = '{"answer": "Pod is red.", "follow_ups": ["What blocks it?", "Since when?"'

    assert _parse_answer(whole).follow_ups == ("Who owns CHK-8?",)
    assert _parse_answer(cut_off).follow_ups == ("What blocks it?", "Since when?")
    assert _parse_answer("Pod is red.").follow_ups == ()


def test_a_task_with_no_key_reads_as_its_name_and_an_issue_id_as_itself() -> None:
    def task(node_id: str, name: str = "Refund edge cases") -> Task:
        return Task(tenant_id="demo", id=node_id, name=name, metadata={})

    assert node_label(task("task-chk-102")) == "Refund edge cases"
    assert node_label(task("CHK-8")) == "CHK-8"
    assert node_label(task("task-x", name=" ")) == "task-x"


def _app(settings: Settings, store: InMemoryGraphStore) -> object:
    role = settings.model_copy(update={"dev_principal_roles": "mgr", "llm_provider": "fake"})
    return create_app(settings=role, registry=ServiceRegistry(role, graph_store=store))


async def test_the_route_sends_the_conversation_and_returns_follow_ups_and_the_summary(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Pod(tenant_id="demo", id="pod-payments", name="Payments Pod"))
    app = _app(settings, store)
    answer = {
        "answer": "Payments Pod is red.",
        "references": ["pod-payments"],
        "follow_ups": ["What is blocking pod-payments?", "a", "b", "c"],
    }
    llm = FakeLlmProvider(responses=[_response("Summary."), _response(json.dumps(answer))])
    monkeypatch.setattr(app.state.registry, "llm_provider", lambda: llm)  # type: ignore[attr-defined]
    turns = [{"role": t.role, "content": t.content} for t in _exchanges(COMPACT_AT_TURNS // 2 + 1)]

    with TestClient(app) as client:  # type: ignore[arg-type]
        response = client.post(
            "/ask",
            json={"question": "And now?", "conversation": {"summary": None, "turns": turns}},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["follow_ups"] == ["What is blocking Payments Pod?", "a", "b"]
    assert (body["summary"], body["summarized_turns"]) == ("Summary.", len(turns) - KEEP_TURNS)
    question = llm.requests[1]
    assert [m.content for m in question.messages][:2] == [
        "Earlier in this conversation, in short: Summary.",
        f"Question {COMPACT_AT_TURNS // 2 + 1 - KEEP_TURNS // 2}?",
    ]
    assert question.messages[-1].content == "And now?"


def test_a_conversation_too_long_to_send_is_refused(settings: Settings) -> None:
    app = _app(settings, InMemoryGraphStore())
    turns = [{"role": "user", "content": "x"}] * (MAX_TURNS + 1)

    with TestClient(app) as client:  # type: ignore[arg-type]
        too_many = client.post("/ask", json={"question": "q", "conversation": {"turns": turns}})
        too_big = client.post(
            "/ask",
            json={
                "question": "q",
                "conversation": {"turns": [{"role": "user", "content": "x" * 5000}]},
            },
        )

    assert too_many.status_code == 422
    assert too_big.status_code == 422


def test_an_answer_view_keeps_no_summary_unless_one_was_made() -> None:
    view = AskResponseView(answer="a", references=(), tools_used=(), trace_id="t")

    assert (view.summary, view.summarized_turns, view.follow_ups) == (None, 0, ())
