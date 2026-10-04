"""A reply that carries no status gets one ack and a consistent close-out (N22).

R3 live: Elena answered "no updates today" twice. The bot said "I'll keep the
check-in open for your status update", then at the close-out read the replies
again, recorded ``partial`` "after clarification timeout" (no clarification
was ever asked) and sent "Got it, your update is recorded". In R2 the same
reply had ended ``unknown``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from core.application.status_collector import NON_STATUS_ACK_TEXT, StatusCollector
from core.application.status_parsing import StatusParser
from core.application.status_summaries import NON_STATUS_REPLY_SUMMARY
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CheckIn, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

_NON_STATUS = '{"is_status_update":false,"sufficient":false,"question":null,"signals":null}'
# What the close-out's re-reading of "no updates today" twice answered in R3.
_R3_REREAD_AS_STATUS = (
    '{"is_status_update":true,"sufficient":true,"question":null,'
    '"signals":{"progress_note":"No updates today, all quiet.","blockers":[],'
    '"eta_change_days":null,"blockers_answered":false,"eta_answered":false}}'
)
_DAY = date(2026, 10, 4)


async def _open_checkin(store: InMemoryGraphStore) -> None:
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-elena",
            correlation_id="corr-elena",
            asked_at=datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )


def _message(message_id: str, minute: int) -> InboundMessage:
    return InboundMessage(
        tenant_id="demo",
        user=ChatUserRef(tenant_id="demo", external_id="U-elena"),
        text="no updates today",
        thread_id="thread-elena",
        message_id=message_id,
        correlation_id="corr-elena",
        received_at=datetime(2026, 10, 4, 0, minute, tzinfo=UTC),
    )


def _collector(
    store: InMemoryGraphStore, chat: FakeChatProvider, llm: SequenceLlmProvider
) -> StatusCollector:
    return StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=llm,
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        model="test-model",
        parser=StatusParser(SequenceLlmProvider(texts=[]), model="test-model"),
    )


async def test_no_updates_today_gets_one_ack_and_closes_unknown() -> None:
    store = InMemoryGraphStore()
    await _open_checkin(store)
    chat = FakeChatProvider()
    # Two live readings only: a third model call (the close-out reading the
    # replies again, as in R3) would fail the test.
    llm = SequenceLlmProvider(texts=[_NON_STATUS, _NON_STATUS])
    collector = _collector(store, chat, llm)

    first = await collector.handle_reply(_message("msg-1", 5))
    second = await collector.handle_reply(_message("msg-2", 6))
    closed = await collector.record_non_response(
        tenant_id="demo",
        developer_id="dev-elena",
        as_of=_DAY,
        correlation_id="corr-elena",
    )

    assert first.kind == "acknowledged"
    assert second.kind == "acknowledged"
    assert [message.text for message in chat.sent] == [NON_STATUS_ACK_TEXT]
    assert closed.source is StatusSource.UNKNOWN
    assert closed.summary == NON_STATUS_REPLY_SUMMARY
    assert "clarification" not in closed.summary
    assert len(llm.requests) == 2
    checkin = await store.checkin_by_correlation("demo", "corr-elena")
    assert checkin is not None
    assert checkin.replied_at is None
    stored = await store.latest_developer_status("demo", "dev-elena", _DAY)
    assert stored == closed


async def test_close_out_does_not_reread_replies_already_acked_as_non_status() -> None:
    store = InMemoryGraphStore()
    await _open_checkin(store)
    chat = FakeChatProvider()
    llm = SequenceLlmProvider(texts=[_NON_STATUS, _R3_REREAD_AS_STATUS])
    collector = _collector(store, chat, llm)

    await collector.handle_reply(_message("msg-1", 5))
    closed = await collector.record_non_response(
        tenant_id="demo",
        developer_id="dev-elena",
        as_of=_DAY,
        correlation_id="corr-elena",
    )

    assert closed.source is StatusSource.UNKNOWN
    assert closed.summary == NON_STATUS_REPLY_SUMMARY
    # No "Got it, your update is recorded" after "I'll keep the check-in open".
    assert [message.text for message in chat.sent] == [NON_STATUS_ACK_TEXT]
    assert llm.requests[1:] == []


async def test_close_out_reading_no_status_records_the_reply_not_silence() -> None:
    store = InMemoryGraphStore()
    await _open_checkin(store)
    # A reply on record that the live path never acknowledged (it was not
    # read in time): the close-out reads it and finds no status.
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-elena",
            conversation_id="corr-elena",
            conversation_date=_DAY,
            role=ConversationRole.USER,
            content="no updates today",
            correlation_id="corr-elena",
            chat_message_id="msg-1",
            observed_at=datetime(2026, 10, 4, 0, 5, tzinfo=UTC),
        )
    )
    chat = FakeChatProvider()
    collector = _collector(store, chat, SequenceLlmProvider(texts=[_NON_STATUS]))

    closed = await collector.record_non_response(
        tenant_id="demo",
        developer_id="dev-elena",
        as_of=_DAY,
        correlation_id="corr-elena",
    )

    assert closed.source is StatusSource.UNKNOWN
    assert closed.summary == NON_STATUS_REPLY_SUMMARY
    assert chat.sent == []


async def test_status_finalized_at_close_without_a_follow_up_is_not_a_clarification_timeout() -> (
    None
):
    store = InMemoryGraphStore()
    await _open_checkin(store)
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-elena",
            conversation_id="corr-elena",
            conversation_date=_DAY,
            role=ConversationRole.USER,
            content="Budget review done, no blockers, nothing due.",
            correlation_id="corr-elena",
            chat_message_id="msg-1",
            observed_at=datetime(2026, 10, 4, 0, 5, tzinfo=UTC),
        )
    )
    collector = _collector(
        store,
        FakeChatProvider(),
        SequenceLlmProvider(
            texts=[
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"Budget review done.","blockers":[],'
                '"eta_change_days":null,"blockers_answered":true,"eta_answered":true}}'
            ]
        ),
    )

    closed = await collector.record_non_response(
        tenant_id="demo",
        developer_id="dev-elena",
        as_of=_DAY,
        correlation_id="corr-elena",
    )

    assert closed.source is StatusSource.CONFIRMED
    assert "clarification timeout" not in closed.summary
    assert closed.summary == (
        "Budget review done. Finalized from accumulated replies at check-in close."
    )
