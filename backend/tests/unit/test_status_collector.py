from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from inspect import Parameter, signature

import pytest

from core.application import status_collector as status_collector_module
from core.application.agents.tool_loop import ToolCallingAgent
from core.application.status_collector import StatusCollector
from core.application.status_parsing import StatusParser
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.cross_person import CrossPersonRequestStatus
from core.domain.directory import DirectoryUser
from core.domain.graph import EntityRef, FactEvent, NodeKind
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.llm import LlmRequest, LlmResponse, LlmToolCall, TokenUsage
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import (
    CheckIn,
    CheckInClarification,
    CheckInCorrelation,
    CheckInPreference,
    CheckInScheduleRun,
    DeveloperStatus,
    StatusSource,
)
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker, FakeLlmProvider


@dataclass
class SequenceLlmProvider:
    texts: list[str]
    requests: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        text = self.texts.pop(0)
        return LlmResponse(
            tenant_id=request.tenant_id,
            text=text,
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost_usd=0.0,
                latency_ms=1.0,
            ),
            trace_id=f"trace-{len(self.requests)}",
        )


def _llm_response(
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
        trace_id="trace-scripted",
        tool_calls=tool_calls,
        finish_reason=finish_reason,
    )


@dataclass
class CapturingLogger:
    events: list[dict[str, object]] = field(default_factory=list)

    def info(self, event: str, **values: object) -> None:
        self.events.append({"event": event, **values})


async def _record_open_checkin(store: InMemoryGraphStore) -> None:
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )


def _reply_message(text: str) -> InboundMessage:
    return InboundMessage(
        tenant_id="demo",
        user=ChatUserRef(tenant_id="demo", external_id="U-dev"),
        text=text,
        thread_id="thread-1",
        message_id="msg-1",
        correlation_id="corr-1",
        received_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
    )


def test_status_collector_requires_conversation_repository() -> None:
    parameter = signature(StatusCollector).parameters["conversation_repository"]

    assert parameter.default is Parameter.empty


async def test_status_collector_graph_sends_dm_and_records_checkin() -> None:
    assignee = UserRef(tenant_id="demo", external_id="dev-1")
    tracker = FakeIssueTracker(
        issues={
            "PO-1": Issue(
                tenant_id="demo",
                key="PO-1",
                title="Build graph sync",
                state=IssueState.IN_PROGRESS,
                assignee=assignee,
            )
        }
    )
    store = InMemoryGraphStore()
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="unit",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="PO-1"),
            payload={"risk": "ancient dependency"},
            observed_at=datetime.now(tz=UTC) - timedelta(days=31),
            correlation_id="fact-old",
        )
    )
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="unit",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="PO-1"),
            payload={"risk": "schema review"},
            observed_at=datetime.now(tz=UTC),
            correlation_id="fact-1",
        )
    )
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="previous-corr",
            conversation_date=date(2026, 1, 9),
            role=ConversationRole.USER,
            content="Yesterday I was waiting on schema review.",
            correlation_id="previous-corr",
            chat_message_id="previous-msg",
            observed_at=datetime(2026, 1, 9, 17, 0, tzinfo=UTC),
        )
    )
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="old-corr",
            conversation_date=date(2026, 1, 9),
            role=ConversationRole.AGENT,
            content="This context is outside the 24-hour lookback.",
            correlation_id="old-corr",
            chat_message_id="old-msg",
            observed_at=datetime(2026, 1, 9, 8, 59, tzinfo=UTC),
        )
    )
    chat = FakeChatProvider()
    llm = SequenceLlmProvider(texts=["Can you share progress, blockers, and ETA changes?"])
    collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=chat,
        llm_provider=llm,
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        model="test-model",
    )

    checkin = await collector.start_checkin(
        tenant_id="demo",
        developer_id="dev-1",
        developer_name="Asha",
        chat_external_id="U123",
        correlation_id="corr-1",
        asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
    )

    assert checkin == await store.checkin_by_correlation("demo", "corr-1")
    assert checkin.raw_reply is None
    correlation = await store.checkin_correlation_by_id("demo", "corr-1")
    assert correlation is not None
    assert correlation.chat_user_ref == "U123"
    assert correlation.chat_thread_ref == "thread-U123"
    assert correlation.outbound_message_id == "msg-U123-1"
    assert chat.sent[0].correlation_id == "corr-1"
    assert chat.sent[0].metadata == {"purpose": "status_checkin"}
    assert "Build graph sync" in llm.requests[0].prompt
    assert "schema review" in llm.requests[0].prompt
    assert "ancient dependency" not in llm.requests[0].prompt
    assert llm.requests[0].system is not None
    assert [(message.role, message.content) for message in llm.requests[0].messages] == [
        ("user", "Yesterday I was waiting on schema review.")
    ]
    turns = await store.list_turns_for_day("demo", "dev-1", date(2026, 1, 10))
    assert turns == [
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-1",
            conversation_date=date(2026, 1, 10),
            role=ConversationRole.AGENT,
            content="Can you share progress, blockers, and ETA changes?",
            correlation_id="corr-1",
            chat_message_id="msg-U123-1",
            observed_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
        )
    ]


async def test_status_collector_handles_reply_by_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-1",
            conversation_date=date(2026, 1, 10),
            role=ConversationRole.AGENT,
            content="Can you share progress, blockers, and ETA changes?",
            correlation_id="corr-1",
            chat_message_id="msg-outbound-1",
            observed_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
        )
    )
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    parser_llm = SequenceLlmProvider(
        texts=[
            '{"progress_note":"Graph sync is in review",'
            '"blockers":["schema review"],"eta_change_days":1}'
        ]
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=["unused"]),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        model="test-model",
        parser=StatusParser(parser_llm, model="test-model"),
    )
    logger = CapturingLogger()
    monkeypatch.setattr(status_collector_module, "_logger", logger)

    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Graph sync is in review, blocked on schema review.",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 7, tzinfo=UTC),
        )
    )

    assert outcome.kind == "processed"
    status = outcome.status
    assert status is not None
    updated = await store.checkin_by_correlation("demo", "corr-1")
    assert updated is not None
    assert updated.raw_reply == "Graph sync is in review, blocked on schema review."
    assert logger.events[0]["event"] == "status_reply_received"
    assert logger.events[0]["raw_reply"] == "Graph sync is in review, blocked on schema review."
    assert status.source is StatusSource.CONFIRMED
    assert status.blockers == ("schema review",)
    assert status.eta_change_days == 1
    assert await store.latest_developer_status("demo", "dev-1", date(2026, 1, 10)) == status
    assert "Graph sync is in review" not in parser_llm.requests[0].metadata.values()
    assert [message.content for message in parser_llm.requests[0].messages] == [
        "Can you share progress, blockers, and ETA changes?"
    ]
    assert all(
        message.content != "Graph sync is in review, blocked on schema review."
        for message in parser_llm.requests[0].messages
    )
    facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
    )
    assert facts[0].source == "checkin"
    assert facts[0].payload == {
        "status_source": "confirmed",
        "blocker_count": 1,
        "has_eta_change": True,
        "eta_change_days": 1,
        "raw_reply": "Graph sync is in review, blocked on schema review.",
    }
    turns = await store.list_turns_for_day("demo", "dev-1", date(2026, 1, 10))
    assert turns[-1].role is ConversationRole.USER
    assert turns[-1].content == "Graph sync is in review, blocked on schema review."
    assert turns[-1].chat_message_id == "msg-1"

    duplicate = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="A different duplicate reply must not overwrite anything.",
            thread_id="thread-1",
            message_id="msg-2",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
        )
    )

    duplicate_checkin = await store.checkin_by_correlation("demo", "corr-1")
    duplicate_facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
    )
    assert duplicate.kind == "processed"
    assert duplicate.status == status
    assert duplicate_checkin == updated
    assert len(parser_llm.requests) == 1
    assert duplicate_facts == facts


async def test_status_collector_records_scheduled_checkin_status_on_schedule_date() -> None:
    store = InMemoryGraphStore()
    asked_at = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-scheduled",
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.record_checkin_schedule_run(
        CheckInScheduleRun(
            tenant_id="demo",
            developer_id="dev-1",
            checkin_date=date(2026, 9, 21),
            correlation_id="corr-scheduled",
            status="sent",
            scheduled_at=asked_at,
        )
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(
            texts=[
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"Future-date reply","blockers":[],'
                '"eta_change_days":null}}',
            ]
        ),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )

    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Future-date reply.",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-scheduled",
            received_at=datetime(2026, 6, 28, 9, 5, tzinfo=UTC),
        )
    )

    assert outcome.kind == "processed"
    assert outcome.status is not None
    assert outcome.status.as_of == date(2026, 9, 21)
    assert await store.latest_developer_status("demo", "dev-1", date(2026, 6, 28)) is None
    assert await store.latest_developer_status("demo", "dev-1", date(2026, 9, 21)) == (
        outcome.status
    )


async def test_status_collector_records_only_first_rapid_final_reply() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    second_may_persist = asyncio.Event()
    second_is_waiting = asyncio.Event()
    original_record_once = store.record_checkin_reply_once

    async def delayed_record_once(checkin: CheckIn) -> bool:
        if checkin.raw_reply == "Second final reply.":
            second_is_waiting.set()
            await second_may_persist.wait()
        return await original_record_once(checkin)

    store.record_checkin_reply_once = delayed_record_once
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(
            texts=[
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"second","blockers":[],"eta_change_days":null,'
                '"eta_change_days":null}}',
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"first","blockers":[],"eta_change_days":null,'
                '"eta_change_days":null}}',
            ]
        ),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        model="test-model",
    )

    second_task = asyncio.create_task(
        collector.handle_reply(
            InboundMessage(
                tenant_id="demo",
                user=ChatUserRef(tenant_id="demo", external_id="U123"),
                text="Second final reply.",
                thread_id="thread-1",
                message_id="msg-2",
                correlation_id="corr-1",
                received_at=datetime(2026, 1, 10, 9, 8, tzinfo=UTC),
            )
        )
    )
    await second_is_waiting.wait()
    first = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="First final reply.",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 7, tzinfo=UTC),
        )
    )
    second_may_persist.set()
    second = await second_task

    checkin = await store.checkin_by_correlation("demo", "corr-1")
    facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
    )
    statuses = [outcome.status.summary for outcome in (first, second) if outcome.status is not None]

    assert checkin is not None
    assert checkin.raw_reply == "First final reply."
    assert statuses == ["first", "first"]
    assert len(facts) == 1
    assert facts[0].payload["raw_reply"] == "First final reply."


async def test_status_collector_sends_clarification_and_keeps_checkin_open() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    chat = FakeChatProvider()
    llm = SequenceLlmProvider(
        texts=['{"sufficient":false,"question":"What blocker should I note?","signals":null}']
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=llm,
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )

    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Still working on it.",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 7, tzinfo=UTC),
        )
    )

    checkin = await store.checkin_by_correlation("demo", "corr-1")
    assert outcome.kind == "clarifying"
    assert outcome.status is None
    assert checkin is not None
    assert checkin.replied_at is None
    assert len(chat.sent) == 1
    assert chat.sent[0].text == "What blocker should I note?"
    assert chat.sent[0].metadata == {
        "purpose": "status_clarification",
        "clarification_number": 1,
        "idempotency_key": "clarification:corr-1:1",
    }
    assert await store.checkin_clarification_count("demo", "corr-1") == 1
    turns = await store.list_recent_turns("demo", "dev-1", limit=2)
    assert [turn.role for turn in turns] == [ConversationRole.USER, ConversationRole.AGENT]


async def test_status_collector_finalizes_when_clarification_cap_reached() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.record_checkin_clarification(
        CheckInClarification(
            tenant_id="demo",
            correlation_id="corr-1",
            clarification_number=1,
            question="What blocker should I note?",
            sent_at=datetime(2026, 1, 10, 9, 8, tzinfo=UTC),
            outbound_message_id="msg-clarify",
        )
    )
    chat = FakeChatProvider()
    llm = SequenceLlmProvider(
        texts=[
            '{"sufficient":false,"question":"Any ETA change?",'
            '"signals":{"progress_note":"Cache work is still in progress",'
            '"blockers":[],"eta_change_days":null}}'
        ]
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=llm,
        status_repository=store,
        conversation_repository=store,
        model="test-model",
        checkin_max_clarifications=1,
    )

    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Cache work is still in progress.",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 15, tzinfo=UTC),
        )
    )

    checkin = await store.checkin_by_correlation("demo", "corr-1")
    assert outcome.kind == "processed"
    status = outcome.status
    assert status is not None
    assert status.source is StatusSource.CONFIRMED
    assert "Clarification cap reached" in status.summary
    assert checkin is not None
    assert checkin.replied_at == datetime(2026, 1, 10, 9, 15, tzinfo=UTC)
    assert chat.sent == []


async def test_status_collector_resolves_cross_person_request_by_single_match() -> None:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U-alice",
                display_name="Alice Chen",
                email="alice@example.com",
            )
        ]
    )
    await _record_open_checkin(store)
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(
            texts=[
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"Blocked on schema review",'
                '"blockers":["schema review"],"eta_change_days":null,'
                '"requests":[{"name":"Alice Chen","kind":"review",'
                '"note":"API schema review","email":null}]}}'
            ]
        ),
        status_repository=store,
        conversation_repository=store,
        directory_repository=directory,
        model="test-model",
    )

    outcome = await collector.handle_reply(_reply_message("Waiting on Alice Chen for review."))

    assert outcome.kind == "processed"
    assert len(outcome.cross_person_requests) == 1
    request = outcome.cross_person_requests[0]
    assert request.status is CrossPersonRequestStatus.OPEN
    assert request.counterpart_id == "U-alice"
    assert request.counterpart_display_name == "Alice Chen"
    assert request.counterpart_email == "alice@example.com"


async def test_status_collector_clarifies_ambiguous_cross_person_name() -> None:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U-alex-chen",
                display_name="Alex Chen",
                email="alex.chen@example.com",
            ),
            DirectoryUser(
                tenant_id="demo",
                external_id="U-alexa-roy",
                display_name="Alexa Roy",
                email="alexa.roy@example.com",
            ),
        ]
    )
    await _record_open_checkin(store)
    chat = FakeChatProvider()
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=SequenceLlmProvider(
            texts=[
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"Blocked on schema input",'
                '"blockers":["schema input"],"eta_change_days":null,'
                '"requests":[{"name":"Alex","kind":"input",'
                '"note":"schema confirmation","email":null}]}}'
            ]
        ),
        status_repository=store,
        conversation_repository=store,
        directory_repository=directory,
        model="test-model",
    )

    outcome = await collector.handle_reply(_reply_message("Waiting on Alex for schema input."))

    checkin = await store.checkin_by_correlation("demo", "corr-1")
    assert outcome.kind == "clarifying"
    assert outcome.cross_person_requests == ()
    assert checkin is not None
    assert checkin.replied_at is None
    assert len(chat.sent) == 1
    assert "Alex Chen (alex.chen@example.com)" in chat.sent[0].text
    assert "Alexa Roy (alexa.roy@example.com)" in chat.sent[0].text
    assert await store.checkin_clarification_count("demo", "corr-1") == 1


async def test_status_collector_resolves_ambiguous_name_with_email_reply() -> None:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U-alex-chen",
                display_name="Alex Chen",
                email="alex.chen@example.com",
            ),
            DirectoryUser(
                tenant_id="demo",
                external_id="U-alexa-roy",
                display_name="Alexa Roy",
                email="alexa.roy@example.com",
            ),
        ]
    )
    await _record_open_checkin(store)
    await store.record_checkin_clarification(
        CheckInClarification(
            tenant_id="demo",
            correlation_id="corr-1",
            clarification_number=1,
            question="Did you mean Alex Chen or Alexa Roy?",
            sent_at=datetime(2026, 1, 10, 9, 8, tzinfo=UTC),
            outbound_message_id="msg-clarify",
        )
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(
            texts=[
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"Blocked on schema input",'
                '"blockers":["schema input"],"eta_change_days":null,'
                '"requests":[{"name":"Alex","kind":"input",'
                '"note":"schema confirmation","email":"alexa.roy@example.com"}]}}'
            ]
        ),
        status_repository=store,
        conversation_repository=store,
        directory_repository=directory,
        model="test-model",
        checkin_max_clarifications=2,
    )

    outcome = await collector.handle_reply(
        _reply_message("I meant alexa.roy@example.com for the schema confirmation.")
    )

    assert outcome.kind == "processed"
    assert len(outcome.cross_person_requests) == 1
    request = outcome.cross_person_requests[0]
    assert request.status is CrossPersonRequestStatus.OPEN
    assert request.counterpart_id == "U-alexa-roy"
    assert request.counterpart_email == "alexa.roy@example.com"


async def test_status_collector_marks_unresolved_request_when_cap_reached() -> None:
    store = InMemoryGraphStore()
    await _record_open_checkin(store)
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(
            texts=[
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"Blocked on data contract",'
                '"blockers":["data contract"],"eta_change_days":null,'
                '"requests":[{"name":"Unknown Alex","kind":"dependency",'
                '"note":"data contract approval","email":null}]}}'
            ]
        ),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
        checkin_max_clarifications=0,
    )

    outcome = await collector.handle_reply(_reply_message("Waiting on Unknown Alex."))

    assert outcome.kind == "processed"
    assert len(outcome.cross_person_requests) == 1
    request = outcome.cross_person_requests[0]
    assert request.status is CrossPersonRequestStatus.NEEDS_RESOLUTION
    assert request.counterpart_id is None


async def test_status_collector_tool_agent_fetches_history_and_finalizes_reply() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-prior",
            conversation_date=date(2026, 1, 9),
            role=ConversationRole.USER,
            content="Previous deploy context.",
            correlation_id="corr-prior",
            chat_message_id="msg-prior",
            observed_at=datetime(2026, 1, 9, 16, 0, tzinfo=UTC),
        )
    )
    llm = FakeLlmProvider(
        responses=[
            _llm_response(
                tool_calls=(
                    LlmToolCall(
                        id="call-history",
                        name="fetch_conversation_history",
                        arguments={"on": "2026-01-09"},
                    ),
                ),
                finish_reason="tool_calls",
            ),
            _llm_response(
                text=(
                    '{"sufficient":true,"question":null,'
                    '"signals":{"progress_note":"Current work is ready",'
                    '"blockers":[],"eta_change_days":0}}'
                ),
                finish_reason="stop",
            ),
        ]
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=llm,
        status_repository=store,
        conversation_repository=store,
        model="test-model",
        tool_agent=ToolCallingAgent(llm, max_tool_iterations=1),
    )

    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Ready now.",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 7, tzinfo=UTC),
        )
    )

    assert outcome.kind == "processed"
    assert outcome.status is not None
    assert outcome.status.summary == "Current work is ready"
    assert len(llm.requests) == 2
    assert llm.requests[0].tools[0].name == "fetch_conversation_history"
    assert llm.requests[1].tool_calls[0].id == "call-history"
    assert "Previous deploy context." in llm.requests[1].tool_results[0].content
    checkin = await store.checkin_by_correlation("demo", "corr-1")
    assert checkin is not None
    assert checkin.raw_reply == "Ready now."


async def test_status_collector_ignores_duplicate_message_id_while_open() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-1",
            conversation_date=date(2026, 1, 10),
            role=ConversationRole.USER,
            content="Already recorded.",
            correlation_id="corr-1",
            chat_message_id="msg-1",
            observed_at=datetime(2026, 1, 10, 9, 7, tzinfo=UTC),
        )
    )
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-1",
            conversation_date=date(2026, 1, 11),
            role=ConversationRole.AGENT,
            content="What blocker should I note?",
            correlation_id="corr-1",
            chat_message_id="msg-clarify",
            observed_at=datetime(2026, 1, 11, 9, 8, tzinfo=UTC),
        )
    )
    chat = FakeChatProvider()
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=SequenceLlmProvider(texts=[]),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )

    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Redelivered text.",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 8, tzinfo=UTC),
        )
    )

    turns = await store.list_turns_for_day("demo", "dev-1", date(2026, 1, 10))
    assert outcome.kind == "ignored"
    assert outcome.status is None
    assert len(turns) == 1
    assert chat.sent == []


async def test_status_collector_timeout_finalizes_accumulated_clarification_reply() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-1",
            conversation_date=date(2026, 1, 10),
            role=ConversationRole.USER,
            content="Cache work is partly done.",
            correlation_id="corr-1",
            chat_message_id="msg-1",
            observed_at=datetime(2026, 1, 10, 9, 7, tzinfo=UTC),
        )
    )
    parser_llm = SequenceLlmProvider(
        texts=[
            '{"progress_note":"Cache work is partly done",'
            '"blockers":[],"eta_change_days":null}'
        ]
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(
            texts=['{"is_status_update":true,"sufficient":true,"question":null,"signals":null}']
        ),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
        parser=StatusParser(parser_llm, model="test-model"),
    )

    status = await collector.record_non_response(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 12),
        correlation_id="corr-1",
    )

    checkin = await store.checkin_by_correlation("demo", "corr-1")
    assert status.source is StatusSource.CONFIRMED
    assert "clarification timeout" in status.summary
    assert checkin is not None
    assert checkin.raw_reply == "Cache work is partly done."
    assert checkin.replied_at == datetime(2026, 1, 10, 9, 7, tzinfo=UTC)


async def test_status_collector_timeout_does_not_confirm_non_status_turn() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-1",
            conversation_date=date(2026, 1, 10),
            role=ConversationRole.USER,
            content="Thanks!",
            correlation_id="corr-1",
            chat_message_id="msg-1",
            observed_at=datetime(2026, 1, 10, 9, 7, tzinfo=UTC),
        )
    )
    parser_llm = SequenceLlmProvider(texts=[])
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(
            texts=['{"is_status_update":false,"sufficient":false,"question":null,"signals":null}']
        ),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
        parser=StatusParser(parser_llm, model="test-model"),
    )

    terminal = await collector.record_non_response(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 12),
        correlation_id="corr-1",
    )

    checkin = await store.checkin_by_correlation("demo", "corr-1")
    assert terminal.source is StatusSource.UNKNOWN
    assert checkin is not None
    assert checkin.replied_at is None
    assert await store.latest_developer_status("demo", "dev-1", date(2026, 1, 10)) is None
    assert parser_llm.requests == []


async def test_status_collector_resolves_replies_by_thread_then_user_day() -> None:
    store = InMemoryGraphStore()
    chat = FakeChatProvider()
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=SequenceLlmProvider(
            texts=[
                "Share a status?",
                "Share another status?",
            ]
        ),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )
    asked_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    await collector.start_checkin(
        tenant_id="demo",
        developer_id="dev-1",
        chat_external_id="U123",
        correlation_id="corr-thread",
        asked_at=asked_at,
    )
    await collector.start_checkin(
        tenant_id="demo",
        developer_id="dev-2",
        chat_external_id="U999",
        correlation_id="corr-user",
        asked_at=asked_at,
    )

    by_thread = await collector.resolve_reply_correlation(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="someone-else"),
            text="reply",
            thread_id="thread-U123",
            message_id="msg-thread",
            correlation_id="request-corr",
            received_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
        )
    )
    by_user = await collector.resolve_reply_correlation(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U999"),
            text="reply",
            thread_id="unknown-thread",
            message_id="msg-user",
            correlation_id="request-corr-2",
            received_at=datetime(2026, 1, 10, 9, 6, tzinfo=UTC),
        )
    )

    assert by_thread == "corr-thread"
    assert by_user == "corr-user"


async def test_status_collector_resolves_user_fallback_by_developer_local_date() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin_preference(
        CheckInPreference(
            tenant_id="demo",
            developer_id="dev-1",
            timezone="America/Los_Angeles",
        )
    )
    asked_at = datetime(2026, 1, 10, 23, 30, tzinfo=UTC)
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-late",
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-late",
            chat_user_ref="U123",
            chat_thread_ref="thread-original",
            outbound_message_id="msg-out",
            asked_at=asked_at,
        )
    )
    llm = SequenceLlmProvider(
        texts=[
            '{"sufficient":true,"question":null,'
            '"signals":{"progress_note":"Finished the rollout",'
            '"blockers":[],"eta_change_days":0}}'
        ]
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=llm,
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )
    received_at = datetime(2026, 1, 11, 1, 0, tzinfo=UTC)

    resolved = await collector.resolve_reply_correlation(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Finished the rollout.",
            thread_id="unknown-thread",
            message_id="msg-late",
            correlation_id="request-corr",
            received_at=received_at,
        )
    )
    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Finished the rollout.",
            thread_id="unknown-thread",
            message_id="msg-late",
            correlation_id="corr-late",
            received_at=received_at,
        )
    )

    assert resolved == "corr-late"
    assert outcome.kind == "processed"
    local_turns = await store.list_turns_for_day("demo", "dev-1", date(2026, 1, 10))
    assert local_turns[-1].chat_message_id == "msg-late"
    assert await store.list_turns_for_day("demo", "dev-1", date(2026, 1, 11)) == []


async def test_status_collector_does_not_guess_ambiguous_user_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    for developer_id, correlation_id in (("dev-1", "corr-1"), ("dev-2", "corr-2")):
        asked_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
        await store.record_checkin_correlation(
            CheckInCorrelation(
                tenant_id="demo",
                developer_id=developer_id,
                correlation_id=correlation_id,
                chat_user_ref="U123",
                chat_thread_ref=f"thread-{developer_id}",
                outbound_message_id=f"msg-{developer_id}",
                asked_at=asked_at,
            )
        )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[]),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )
    logger = CapturingLogger()
    monkeypatch.setattr(status_collector_module, "_logger", logger)

    resolved = await collector.resolve_reply_correlation(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="reply",
            thread_id="unknown-thread",
            message_id="msg-ambiguous",
            correlation_id="request-corr",
            received_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
        )
    )

    assert resolved is None
    assert logger.events == [
        {
            "event": "reply_correlation_ambiguous",
            "tenant_id": "demo",
            "chat_user_ref": "U123",
            "message_id": "msg-ambiguous",
            "correlation_ids": ["corr-1", "corr-2"],
        }
    ]


async def test_status_collector_does_not_guess_ambiguous_thread_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    for developer_id, correlation_id in (("dev-1", "corr-1"), ("dev-2", "corr-2")):
        await store.record_checkin_correlation(
            CheckInCorrelation(
                tenant_id="demo",
                developer_id=developer_id,
                correlation_id=correlation_id,
                chat_user_ref="U123",
                chat_thread_ref="thread-shared",
                outbound_message_id=f"msg-{developer_id}",
                asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            )
        )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[]),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )
    logger = CapturingLogger()
    monkeypatch.setattr(status_collector_module, "_logger", logger)

    resolved = await collector.resolve_reply_correlation(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="reply",
            thread_id="thread-shared",
            message_id="msg-ambiguous-thread",
            correlation_id="request-corr",
            received_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
        )
    )

    assert resolved is None
    assert logger.events == [
        {
            "event": "reply_correlation_ambiguous",
            "tenant_id": "demo",
            "chat_user_ref": "U123",
            "chat_thread_ref": "thread-shared",
            "message_id": "msg-ambiguous-thread",
            "correlation_ids": ["corr-1", "corr-2"],
        }
    ]


async def test_status_collector_prioritizes_blocked_and_critical_issues() -> None:
    assignee = UserRef(tenant_id="demo", external_id="dev-1")
    tracker = FakeIssueTracker(
        issues={
            f"PO-{index}": Issue(
                tenant_id="demo",
                key=f"PO-{index}",
                title=f"Routine task {index}",
                state=IssueState.IN_PROGRESS,
                assignee=assignee,
                updated_at=datetime(2026, 1, 10, 10 + index, tzinfo=UTC),
            )
            for index in range(1, 6)
        }
        | {
            "PO-BLOCKED": Issue(
                tenant_id="demo",
                key="PO-BLOCKED",
                title="Blocked release gate",
                state=IssueState.BLOCKED,
                assignee=assignee,
                metadata={"critical_path": True},
                updated_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
            )
        }
    )
    collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[]),
        status_repository=InMemoryGraphStore(),
        conversation_repository=InMemoryGraphStore(),
        model="test-model",
    )

    context = await collector.build_context(tenant_id="demo", developer_id="dev-1")

    assert "PO-BLOCKED" in context
    assert "PO-1" not in context


async def test_status_collector_carries_forward_unresolved_prior_blockers() -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 9),
            source=StatusSource.CONFIRMED,
            blockers=("release gate",),
            summary="Blocked on release gate.",
        )
    )
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    llm = SequenceLlmProvider(
        texts=[
            '{"sufficient":true,"question":null,'
            '"signals":{"progress_note":"Same as yesterday",'
            '"blockers":[],"eta_change_days":null}}'
        ]
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=llm,
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )

    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Same as yesterday.",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
        )
    )

    assert outcome.status is not None
    assert outcome.status.blockers == ("release gate",)
    assert "Prior blockers carried forward" in outcome.status.summary
    assert "Previously open blockers" in llm.requests[0].prompt
    assert "release gate" in llm.requests[0].prompt


async def test_status_collector_does_not_clear_prior_blocker_on_negated_resolution() -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 9),
            source=StatusSource.CONFIRMED,
            blockers=("release gate",),
            summary="Blocked on release gate.",
        )
    )
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    llm = SequenceLlmProvider(
        texts=[
            '{"sufficient":true,"question":null,'
            '"signals":{"progress_note":"Release gate is not resolved",'
            '"blockers":[],"eta_change_days":null}}'
        ]
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=llm,
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )

    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Release gate is not resolved.",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
        )
    )

    assert outcome.status is not None
    assert outcome.status.blockers == ("release gate",)
    assert "Prior blockers carried forward" in outcome.status.summary


async def test_status_collector_acknowledges_non_status_reply_without_finalizing() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    chat = FakeChatProvider()
    llm = SequenceLlmProvider(
        texts=['{"is_status_update":false,"sufficient":false,"question":null,"signals":null}']
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=llm,
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )

    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Thanks!",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
        )
    )
    checkin = await store.checkin_by_correlation("demo", "corr-1")

    assert outcome.kind == "acknowledged"
    assert outcome.status is None
    assert checkin is not None
    assert checkin.replied_at is None
    assert await store.latest_developer_status("demo", "dev-1", date(2026, 1, 10)) is None
    assert chat.sent[0].text == "Thanks. I'll keep the check-in open for your status update."


async def test_status_collector_nudges_once_and_records_stale_non_response() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 9),
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Yesterday was on track.",
        )
    )
    chat = FakeChatProvider()
    llm = SequenceLlmProvider(texts=["Quick follow-up on your status update."])
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=llm,
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )

    nudge_message_id = await collector.send_nudge(
        tenant_id="demo",
        correlation_id="corr-1",
        developer_name="Asha",
        chat_external_id="U123",
    )
    terminal_status = await collector.record_non_response(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 10),
        developer_name="Asha",
    )

    assert nudge_message_id == "msg-U123-1"
    assert len(chat.sent) == 1
    assert chat.sent[0].metadata == {
        "purpose": "status_nudge",
        "nudge_number": 1,
        "idempotency_key": "nudge:corr-1:1",
    }
    turns = await store.list_recent_turns("demo", "dev-1", limit=1)
    assert len(turns) == 1
    assert turns[0].conversation_id == "corr-1"
    assert turns[0].conversation_date == turns[0].observed_at.date()
    assert turns[0].role is ConversationRole.AGENT
    assert turns[0].content == "Quick follow-up on your status update."
    assert turns[0].chat_message_id == "msg-U123-1"
    assert llm.requests[0].system is not None
    assert terminal_status.source is StatusSource.STALE
    assert terminal_status.blockers == ("no confirmed reply",)
    assert "Yesterday was on track" in terminal_status.summary


async def test_status_collector_records_inferred_and_unknown_non_response() -> None:
    assignee = UserRef(tenant_id="demo", external_id="dev-1")
    tracker = FakeIssueTracker(
        issues={
            "PO-1": Issue(
                tenant_id="demo",
                key="PO-1",
                title="Recent implementation work",
                state=IssueState.IN_PROGRESS,
                assignee=assignee,
            )
        }
    )
    inferred_store = InMemoryGraphStore()
    inferred_collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[]),
        status_repository=inferred_store,
        conversation_repository=inferred_store,
        model="test-model",
    )

    inferred = await inferred_collector.record_non_response(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 10),
    )

    unknown_store = InMemoryGraphStore()
    unknown_collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[]),
        status_repository=unknown_store,
        conversation_repository=unknown_store,
        model="test-model",
    )

    unknown = await unknown_collector.record_non_response(
        tenant_id="demo",
        developer_id="dev-2",
        as_of=date(2026, 1, 10),
    )

    assert inferred.source is StatusSource.INFERRED
    assert "Recent implementation work" in inferred.summary
    assert unknown.source is StatusSource.UNKNOWN
    assert unknown.blockers == ("no confirmed reply",)
