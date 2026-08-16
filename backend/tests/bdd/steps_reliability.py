"""Step definitions for the Plan 01 reliability scenarios.

These steps document the two durable inbound-reply guarantees that both the
DBOS and Temporal coalesce workflows rely on:

- a BURST of buffered inbound events for one conversation coalesces into a
  single processed reply (one drain pass, the pipeline runs once), and
- a reply whose processing FAILS stays buffered in ``inbound_chat_events`` and
  is retried on the next drain, finalizing without loss (the Plan 01 "R3"
  lost-reply fix).

They exercise the real ``ReplyIngestionService`` + in-memory
``inbound_chat_events`` buffer through ``world.registry()`` (mirroring the
registry-reaching pattern in ``steps_reply_parsing``), so they run fully
in-memory with no live simulator or workflow engine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pytest_bdd import given, parsers, then, when

from core.application.reply_ingestion import ReplyDrainResult, ReplyIngestionService
from core.domain.inbound import InboundChatEvent
from core.domain.messaging import InboundMessage
from core.ports.reply_processing import ReplyProcessingOutcome
from tests.bdd.fixtures import World

_TENANT = "demo"
_BASE_TIME = datetime(2026, 7, 2, 9, 30, tzinfo=UTC)


@dataclass
class _RecordingProcessor:
    """Records each coalesced reply the pipeline is asked to process."""

    calls: list[InboundMessage] = field(default_factory=list)

    async def process_reply(self, message: InboundMessage) -> ReplyProcessingOutcome:
        self.calls.append(message)
        return ReplyProcessingOutcome(status="processed", message_id=message.message_id)


@dataclass
class _FailOnceProcessor:
    """Fails the first N attempts, then records and succeeds."""

    fail_attempts: int = 1
    attempts: int = 0
    received: list[InboundMessage] = field(default_factory=list)

    async def process_reply(self, message: InboundMessage) -> ReplyProcessingOutcome:
        self.attempts += 1
        if self.attempts <= self.fail_attempts:
            raise RuntimeError("simulated transient reply-processing failure")
        self.received.append(message)
        return ReplyProcessingOutcome(status="processed", message_id=message.message_id)


def _buffer_events(world: World, conversation_key: str, texts: list[str]) -> None:
    repository = world.registry().inbound_chat_event_repository()

    async def _append() -> None:
        for index, text in enumerate(texts):
            await repository.append(
                InboundChatEvent(
                    tenant_id=_TENANT,
                    provider="mock_slack",
                    event_id=f"{conversation_key}-evt-{index}",
                    conversation_key=conversation_key,
                    chat_user_ref="U1001",
                    chat_thread_ref=conversation_key,
                    message_ref=f"{conversation_key}-msg-{index}",
                    text=text,
                    correlation_id=f"{conversation_key}-corr",
                    received_at=_BASE_TIME + timedelta(seconds=index),
                )
            )

    asyncio.run(_append())


# ---------------------------------------------------------------------------
# Burst coalescing
# ---------------------------------------------------------------------------


@when(
    parsers.parse(
        'a burst of {count:d} inbound messages is buffered for conversation "{conversation_key}"'
    )
)
def _when_burst_buffered(world: World, count: int, conversation_key: str) -> None:
    texts = [f"message {index + 1}" for index in range(count)]
    _buffer_events(world, conversation_key, texts)
    world.stash["reliability_key"] = conversation_key
    world.stash["reliability_expected_join"] = "\n".join(texts)
    world.stash["reliability_processor"] = _RecordingProcessor()


@when(parsers.parse('the conversation "{conversation_key}" is drained once'))
def _when_drained_once(world: World, conversation_key: str) -> None:
    repository = world.registry().inbound_chat_event_repository()
    processor = world.stash["reliability_processor"]
    service = ReplyIngestionService(repository=repository, processor=processor)
    result = asyncio.run(
        service.drain_conversation(tenant_id=_TENANT, conversation_key=conversation_key)
    )
    world.stash["reliability_result"] = result


@then(parsers.parse("the drain processes {count:d} buffered events in a single pass"))
def _then_drain_processes(world: World, count: int) -> None:
    result: ReplyDrainResult = world.stash["reliability_result"]
    assert result.processed == count, result
    assert result.passes == 1, result


@then("the reply pipeline runs exactly once for the burst")
def _then_pipeline_runs_once(world: World) -> None:
    processor: _RecordingProcessor = world.stash["reliability_processor"]
    assert len(processor.calls) == 1, processor.calls


@then("the coalesced reply joins all 4 messages oldest to newest")
def _then_coalesced_join(world: World) -> None:
    processor: _RecordingProcessor = world.stash["reliability_processor"]
    assert processor.calls, "pipeline never ran"
    assert processor.calls[0].text == world.stash["reliability_expected_join"]


# ---------------------------------------------------------------------------
# Retry without loss
# ---------------------------------------------------------------------------


@given(parsers.parse('an inbound reply "{text}" is buffered for conversation "{conversation_key}"'))
def _given_reply_buffered(world: World, text: str, conversation_key: str) -> None:
    _buffer_events(world, conversation_key, [text])
    world.stash["reliability_key"] = conversation_key
    world.stash["reliability_text"] = text


@given("the reply pipeline fails on its first attempt then succeeds")
def _given_pipeline_fails_once(world: World) -> None:
    world.stash["reliability_processor"] = _FailOnceProcessor(fail_attempts=1)


@when(parsers.parse('the conversation "{conversation_key}" drain is attempted and fails'))
def _when_drain_attempt_fails(world: World, conversation_key: str) -> None:
    repository = world.registry().inbound_chat_event_repository()
    processor = world.stash["reliability_processor"]
    service = ReplyIngestionService(repository=repository, processor=processor)
    world.stash["reliability_service"] = service
    raised = False
    try:
        asyncio.run(
            service.drain_conversation(tenant_id=_TENANT, conversation_key=conversation_key)
        )
    except RuntimeError:
        raised = True
    assert raised, "expected the first drain attempt to raise"


@then("the buffered reply is still pending after the failed attempt")
def _then_reply_still_pending(world: World) -> None:
    repository = world.registry().inbound_chat_event_repository()
    conversation_key = world.stash["reliability_key"]
    pending = asyncio.run(repository.list_unprocessed_for_conversation(_TENANT, conversation_key))
    assert len(pending) == 1, pending
    assert pending[0].text == world.stash["reliability_text"]


@when(parsers.parse('the conversation "{conversation_key}" is drained again'))
def _when_drained_again(world: World, conversation_key: str) -> None:
    service: ReplyIngestionService = world.stash["reliability_service"]
    result = asyncio.run(
        service.drain_conversation(tenant_id=_TENANT, conversation_key=conversation_key)
    )
    world.stash["reliability_result"] = result


@then("the drain finalizes the reply without loss")
def _then_drain_finalizes(world: World) -> None:
    result: ReplyDrainResult = world.stash["reliability_result"]
    assert result.processed == 1, result
    assert result.status == "processed", result
    repository = world.registry().inbound_chat_event_repository()
    conversation_key = world.stash["reliability_key"]
    pending = asyncio.run(repository.list_unprocessed_for_conversation(_TENANT, conversation_key))
    assert pending == [], pending


@then(parsers.parse('the reply pipeline finally received the reply text "{text}"'))
def _then_pipeline_received(world: World, text: str) -> None:
    processor: _FailOnceProcessor = world.stash["reliability_processor"]
    assert processor.received, "pipeline never succeeded"
    assert processor.received[-1].text == text
