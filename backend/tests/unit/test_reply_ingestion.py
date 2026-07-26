from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from core.application.reply_ingestion import (
    ReplyIngestionService,
    coalesce_inbound_events,
    run_reply_debounce,
)
from core.domain.inbound import InboundChatEvent, conversation_key
from core.domain.messaging import InboundMessage
from core.ports.reply_processing import ReplyProcessingOutcome
from tests.contract.fakes import FakeInboundChatEventRepository


def _event(
    event_id: str,
    *,
    text: str,
    message_ref: str,
    received_at: datetime,
    conv: str = "demo:thread-1",
) -> InboundChatEvent:
    return InboundChatEvent(
        tenant_id="demo",
        provider="slack",
        event_id=event_id,
        conversation_key=conv,
        chat_user_ref="U123",
        message_ref=message_ref,
        text=text,
        correlation_id="corr-1",
        chat_thread_ref="thread-1",
        received_at=received_at,
    )


@dataclass
class _RecordingProcessor:
    outcome: ReplyProcessingOutcome = field(
        default_factory=lambda: ReplyProcessingOutcome(status="processed", message_id="m")
    )
    calls: list[InboundMessage] = field(default_factory=list)

    async def process_reply(self, message: InboundMessage) -> ReplyProcessingOutcome:
        self.calls.append(message)
        return self.outcome


@dataclass
class _FlakyProcessor:
    fail_times: int
    calls: list[InboundMessage] = field(default_factory=list)

    async def process_reply(self, message: InboundMessage) -> ReplyProcessingOutcome:
        self.calls.append(message)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("transient LLM failure")
        return ReplyProcessingOutcome(status="processed", message_id=message.message_id)


def test_coalesce_orders_by_received_at_and_joins_text() -> None:
    # Deliberately built newest-first to prove ordering is by received_at.
    events = [
        replace(
            _event("Ev-3", text="third", message_ref="1700000003.0", received_at=_ts(3)),
            id="row-3",
        ),
        replace(
            _event("Ev-1", text="first", message_ref="1700000001.0", received_at=_ts(1)),
            id="row-1",
        ),
        replace(
            _event("Ev-2", text="second", message_ref="1700000002.0", received_at=_ts(2)),
            id="row-2",
        ),
    ]

    coalesced = coalesce_inbound_events(events)

    assert coalesced is not None
    assert coalesced.message.text == "first\nsecond\nthird"
    # Correlation/message ref come from the latest event; source ids in order.
    assert coalesced.message.message_id == "1700000003.0"
    assert coalesced.message.thread_id == "thread-1"
    assert coalesced.source_event_ids == ("row-1", "row-2", "row-3")


def test_coalesce_returns_none_for_empty() -> None:
    assert coalesce_inbound_events([]) is None


async def test_run_reply_debounce_resets_until_quiet() -> None:
    # Two signals arrive before timeout, then a quiet window ends the loop.
    signals = iter([True, True, False])

    async def received_before_timeout() -> bool:
        return next(signals)

    resets = await run_reply_debounce(received_before_timeout)

    assert resets == 2


async def test_drain_coalesces_burst_into_single_pass() -> None:
    repository = FakeInboundChatEventRepository()
    conv = conversation_key("demo", "thread-1")
    for index in range(3):
        assert await repository.append(
            _event(
                f"Ev-{index}",
                text=f"msg-{index}",
                message_ref=f"1700000000.00000{index}",
                received_at=_ts(index),
                conv=conv,
            )
        )
    processor = _RecordingProcessor()
    service = ReplyIngestionService(repository=repository, processor=processor)

    result = await service.drain_conversation(tenant_id="demo", conversation_key=conv)

    assert result.processed == 3
    # Exactly one processing pass consumed all three buffered messages.
    assert len(processor.calls) == 1
    assert processor.calls[0].text == "msg-0\nmsg-1\nmsg-2"
    assert await repository.list_unprocessed_for_conversation("demo", conv) == []


async def test_drain_leaves_events_buffered_on_failure_then_finalizes_on_retry() -> None:
    repository = FakeInboundChatEventRepository()
    conv = conversation_key("demo", "thread-1")
    assert await repository.append(
        _event("Ev-1", text="blocked on api", message_ref="1700000001.0", received_at=_ts(1))
    )
    processor = _FlakyProcessor(fail_times=1)
    service = ReplyIngestionService(repository=repository, processor=processor)

    # First drain fails inside the pipeline; the event must remain unprocessed
    # (this is the R3 fix: a transient failure never leaves the reply stuck).
    with pytest.raises(RuntimeError):
        await service.drain_conversation(tenant_id="demo", conversation_key=conv)
    assert len(await repository.list_unprocessed_for_conversation("demo", conv)) == 1

    # Retry drains successfully and marks the event processed.
    result = await service.drain_conversation(tenant_id="demo", conversation_key=conv)
    assert result.status == "processed"
    assert result.processed == 1
    assert await repository.list_unprocessed_for_conversation("demo", conv) == []


async def test_drain_empty_conversation_is_a_noop() -> None:
    repository = FakeInboundChatEventRepository()
    processor = _RecordingProcessor()
    service = ReplyIngestionService(repository=repository, processor=processor)

    result = await service.drain_conversation(tenant_id="demo", conversation_key="demo:none")

    assert result.processed == 0
    assert result.status == "empty"
    assert processor.calls == []


def _ts(second: int) -> datetime:
    return datetime(2026, 1, 10, 9, 0, second, tzinfo=UTC)
