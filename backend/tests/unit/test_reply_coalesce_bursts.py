"""One DBOS reply-coalesce workflow per buffered burst (known gap G2).

DBOS dedupes a workflow id forever. With one id per conversation, the first
burst's finished workflow swallowed every later start, so each later message
waited for the sweeper. These tests drive the real arm path and the real
workflow body against a fake engine that keeps DBOS's id contract: starting a
known id returns the existing workflow and runs nothing.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from config.settings import Settings
from core.application.reply_ingestion import ReplyIngestionService, reply_burst_key
from core.domain.inbound import InboundChatEvent
from core.domain.messaging import InboundMessage
from core.domain.workflows import ReplyCoalesceInput, ReplyCoalesceResult
from core.ports.reply_processing import ReplyProcessingOutcome
from infra.adapters.workflows import dbos as dbos_workflows
from infra.adapters.workflows.fake import FakeWorkflowScheduler
from infra.registry import ServiceRegistry

TENANT = "demo"
CONVERSATION = "demo:D-dm-1"
T0 = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

_current_workflow: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "fake_dbos_current_workflow", default=None
)


class _SetWorkflowId:
    """Stand-in for ``SetWorkflowID``: scopes the id to the calling task."""

    def __init__(self, workflow_id: str) -> None:
        self._workflow_id = workflow_id
        self._token: contextvars.Token[str | None] | None = None

    def __enter__(self) -> None:
        self._token = _current_workflow.set(self._workflow_id)

    def __exit__(self, *args: object) -> None:
        if self._token is not None:
            _current_workflow.reset(self._token)


@dataclass
class _FakeDbos:
    """DBOS's workflow-id contract, nothing more."""

    inputs: dict[str, ReplyCoalesceInput] = field(default_factory=dict)
    started: list[str] = field(default_factory=list)
    finished: set[str] = field(default_factory=set)
    pings: list[str] = field(default_factory=list)
    inbox: dict[str, list[str]] = field(default_factory=dict)

    async def start_workflow_async(self, workflow: object, payload: ReplyCoalesceInput) -> None:
        workflow_id = _current_workflow.get()
        assert workflow_id is not None, "start_workflow_async ran without SetWorkflowID"
        await asyncio.sleep(0)  # let concurrent arms interleave before the id check
        if workflow_id in self.inputs:
            return None  # a known id, running or finished, starts nothing
        self.inputs[workflow_id] = payload
        self.started.append(workflow_id)
        return None

    async def send_async(self, destination_id: str, message: str, topic: str) -> None:
        assert destination_id in self.inputs, "send to a workflow that was never started"
        assert topic == dbos_workflows.DBOS_REPLY_TOPIC
        self.pings.append(destination_id)
        if destination_id not in self.finished:
            self.inbox.setdefault(destination_id, []).append(message)

    async def recv_async(self, topic: str, timeout_seconds: int) -> str | None:
        workflow_id = _current_workflow.get()
        assert workflow_id is not None
        queue = self.inbox.get(workflow_id, [])
        return queue.pop(0) if queue else None  # None: the quiet window elapsed

    def pending(self) -> list[str]:
        return [workflow_id for workflow_id in self.started if workflow_id not in self.finished]


@dataclass
class _RecordingProcessor:
    calls: list[InboundMessage] = field(default_factory=list)
    # Runs inside the first processing call: a message landing mid-drain.
    during_first_call: Callable[[], Awaitable[object]] | None = None

    async def process_reply(self, message: InboundMessage) -> ReplyProcessingOutcome:
        self.calls.append(message)
        if self.during_first_call is not None and len(self.calls) == 1:
            await self.during_first_call()
        return ReplyProcessingOutcome(status="processed", message_id=message.message_id)


@dataclass
class _Harness:
    registry: ServiceRegistry
    engine: _FakeDbos
    processor: _RecordingProcessor
    _seq: int = 0

    async def receive(self, text: str, *, at: datetime | None = None) -> InboundChatEvent:
        """Buffer one inbound message and arm, as fast-ack intake does."""
        self._seq += 1
        received_at = at or T0 + timedelta(seconds=self._seq)
        event = InboundChatEvent(
            tenant_id=TENANT,
            provider="slack",
            event_id=f"Ev{self._seq}",
            conversation_key=CONVERSATION,
            chat_user_ref="U1",
            chat_thread_ref="D-dm-1",
            message_ref=f"{received_at.timestamp():.6f}",
            correlation_id=f"corr-{self._seq}",
            text=text,
            received_at=received_at,
        )
        repository = self.registry.inbound_chat_event_repository()
        assert await repository.append(event)
        stored = await repository.list_unprocessed_for_conversation(TENANT, CONVERSATION)
        await self.registry.arm_reply_coalesce(TENANT, CONVERSATION)
        return next(row for row in stored if row.event_id == event.event_id)

    async def run(self, workflow_id: str) -> ReplyCoalesceResult:
        """Run the real coalesce workflow body for one started id to completion."""
        token = _current_workflow.set(workflow_id)
        try:
            body = inspect.unwrap(dbos_workflows.dbos_reply_coalesce_workflow)
            result: ReplyCoalesceResult = await body(self.engine.inputs[workflow_id])
        finally:
            _current_workflow.reset(token)
        self.engine.finished.add(workflow_id)
        return result


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    settings = Settings(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        runtime_mode="memory",
    )
    registry = ServiceRegistry(settings)
    engine = _FakeDbos()
    processor = _RecordingProcessor()
    service = ReplyIngestionService(
        repository=registry.inbound_chat_event_repository(),
        processor=processor,
    )
    scheduler = dbos_workflows.DbosWorkflowScheduler(
        app_name="openprogram-test",
        system_database_url="postgresql://unused",
        schedule_id="heartbeat-test",
        tenant_id=TENANT,
        heartbeat_cron="0 * * * * *",
    )

    async def drain_step(payload: ReplyCoalesceInput) -> ReplyCoalesceResult:
        result = await registry.drain_inbound_conversation(
            payload.tenant_id, payload.conversation_key
        )
        return ReplyCoalesceResult(
            tenant_id=payload.tenant_id,
            conversation_key=payload.conversation_key,
            processed=result.processed,
            passes=result.passes,
        )

    monkeypatch.setattr(registry, "reply_ingestion_service", lambda: service)
    monkeypatch.setattr(registry, "workflow_scheduler", lambda: scheduler)
    monkeypatch.setattr(dbos_workflows, "_ensure_dbos_runtime", lambda config: False)
    monkeypatch.setattr(dbos_workflows, "SetWorkflowID", _SetWorkflowId)
    monkeypatch.setattr(
        dbos_workflows.DBOS, "start_workflow_async", staticmethod(engine.start_workflow_async)
    )
    monkeypatch.setattr(dbos_workflows.DBOS, "send_async", staticmethod(engine.send_async))
    monkeypatch.setattr(dbos_workflows.DBOS, "recv_async", staticmethod(engine.recv_async))
    monkeypatch.setattr(dbos_workflows, "dbos_drain_inbound_conversation_step", drain_step)
    return _Harness(registry=registry, engine=engine, processor=processor)


def _burst_id(event: InboundChatEvent) -> str:
    assert event.id is not None
    return dbos_workflows.reply_coalesce_workflow_id(TENANT, CONVERSATION, event.id)


async def test_the_first_message_starts_a_workflow_named_after_it(harness: _Harness) -> None:
    first = await harness.receive("first")

    assert harness.engine.started == [_burst_id(first)]
    assert harness.engine.pings == [_burst_id(first)]

    result = await harness.run(_burst_id(first))

    assert result.processed == 1
    assert [call.text for call in harness.processor.calls] == ["first"]


async def test_a_message_during_the_burst_joins_the_pending_workflow(harness: _Harness) -> None:
    first = await harness.receive("first")
    await harness.receive("second")

    # One workflow, signalled twice: the second ping reset its debounce window.
    assert harness.engine.started == [_burst_id(first)]
    assert harness.engine.pings == [_burst_id(first), _burst_id(first)]

    result = await harness.run(_burst_id(first))

    assert result.processed == 2
    assert [call.text for call in harness.processor.calls] == ["first\nsecond"]


async def test_a_message_after_the_burst_finished_starts_a_new_workflow(
    harness: _Harness,
) -> None:
    first = await harness.receive("first")
    await harness.run(_burst_id(first))

    third = await harness.receive("third")

    assert _burst_id(third) != _burst_id(first)
    assert harness.engine.started == [_burst_id(first), _burst_id(third)]
    assert harness.engine.pending() == [_burst_id(third)]

    result = await harness.run(_burst_id(third))

    assert result.processed == 1
    assert [call.text for call in harness.processor.calls] == ["first", "third"]


async def test_every_later_burst_in_the_same_conversation_gets_its_own_workflow(
    harness: _Harness,
) -> None:
    # The QA stack reuses one DM channel every round, so this is the normal case.
    for round_number in range(3):
        event = await harness.receive(f"round {round_number}")
        assert harness.engine.pending() == [_burst_id(event)]
        await harness.run(_burst_id(event))

    assert len(set(harness.engine.started)) == 3
    assert [call.text for call in harness.processor.calls] == ["round 0", "round 1", "round 2"]


async def test_a_message_landing_mid_drain_is_taken_by_that_drain(harness: _Harness) -> None:
    # The burst's events are still unprocessed while its drain runs, so the arm
    # resolves to the running workflow and the drain's re-check picks it up.
    harness.processor.during_first_call = lambda: harness.receive("during drain")
    first = await harness.receive("first")

    result = await harness.run(_burst_id(first))

    assert harness.engine.started == [_burst_id(first)]
    assert result.processed == 2
    assert [call.text for call in harness.processor.calls] == ["first", "during drain"]
    assert harness.engine.pending() == []


async def test_concurrent_arms_for_one_burst_start_one_workflow(harness: _Harness) -> None:
    first = await harness.receive("first")
    repository = harness.registry.inbound_chat_event_repository()
    for number in (2, 3):
        await repository.append(
            InboundChatEvent(
                tenant_id=TENANT,
                provider="slack",
                event_id=f"Ev-concurrent-{number}",
                conversation_key=CONVERSATION,
                chat_user_ref="U1",
                chat_thread_ref="D-dm-1",
                message_ref=f"m-{number}",
                correlation_id=f"corr-concurrent-{number}",
                text=f"concurrent {number}",
                received_at=T0 + timedelta(seconds=10 + number),
            )
        )

    await asyncio.gather(
        harness.registry.arm_reply_coalesce(TENANT, CONVERSATION),
        harness.registry.arm_reply_coalesce(TENANT, CONVERSATION),
        harness.registry.arm_reply_coalesce(TENANT, CONVERSATION),
    )

    assert harness.engine.started == [_burst_id(first)]
    assert harness.engine.pings == [_burst_id(first)] * 4

    await harness.run(_burst_id(first))

    assert [call.text for call in harness.processor.calls] == ["first\nconcurrent 2\nconcurrent 3"]


async def test_the_sweeper_does_not_reprocess_a_burst_the_workflow_handled(
    harness: _Harness,
) -> None:
    first = await harness.receive("first")
    await harness.receive("second")
    await harness.run(_burst_id(first))
    assert len(harness.processor.calls) == 1

    swept = await harness.registry.sweep_inbound_events(
        TENANT, grace_seconds=0, now=T0 + timedelta(hours=1)
    )

    assert swept.rearmed == 0
    assert swept.conversation_keys == []
    assert len(harness.processor.calls) == 1


async def test_the_sweeper_drains_a_stranded_burst_once_and_the_next_message_starts_fresh(
    harness: _Harness,
) -> None:
    # A burst whose workflow never ran (crash, lost start) is the sweeper's job.
    stranded = await harness.receive("stranded")
    swept = await harness.registry.sweep_inbound_events(
        TENANT, grace_seconds=0, now=T0 + timedelta(hours=1)
    )
    assert swept.conversation_keys == [CONVERSATION]
    assert [call.text for call in harness.processor.calls] == ["stranded"]

    # Its workflow then finds nothing left to drain.
    late = await harness.run(_burst_id(stranded))
    assert late.processed == 0
    assert len(harness.processor.calls) == 1

    # And the next message opens a burst of its own rather than the stale id.
    after = await harness.receive("after")
    assert harness.engine.pending() == [_burst_id(after)]
    await harness.run(_burst_id(after))
    assert [call.text for call in harness.processor.calls] == ["stranded", "after"]


async def test_nothing_is_armed_when_a_drain_already_took_the_message(
    harness: _Harness,
) -> None:
    await harness.registry.arm_reply_coalesce(TENANT, CONVERSATION)

    assert harness.engine.started == []
    assert harness.engine.pings == []


def test_the_burst_key_is_the_earliest_buffered_event_in_drain_order() -> None:
    def event(row_id: str, seconds: int, message_ref: str) -> InboundChatEvent:
        return InboundChatEvent(
            id=row_id,
            tenant_id=TENANT,
            provider="slack",
            event_id=f"Ev-{row_id}",
            conversation_key=CONVERSATION,
            chat_user_ref="U1",
            message_ref=message_ref,
            text="x",
            correlation_id="corr",
            received_at=T0 + timedelta(seconds=seconds),
        )

    later = event("row-b", 5, "m-1")
    earliest = event("row-c", 1, "m-2")
    tie_loser = event("row-a", 1, "m-3")

    assert reply_burst_key([later, tie_loser, earliest]) == "row-c"
    assert reply_burst_key([]) is None


def test_the_workflow_id_names_the_tenant_conversation_and_burst() -> None:
    workflow_id = dbos_workflows.reply_coalesce_workflow_id(
        TENANT, CONVERSATION, "6f1c0a52-0c7e-4f8e-9d43-2b8f0f3f9a11"
    )

    assert workflow_id == ("reply-coalesce-demo-demo-D-dm-1-6f1c0a52-0c7e-4f8e-9d43-2b8f0f3f9a11")


async def test_the_fake_scheduler_accepts_a_burst_key() -> None:
    scheduler = FakeWorkflowScheduler(schedule_id="heartbeat-test")
    await scheduler.arm_reply_coalesce(CONVERSATION, TENANT, burst_key="row-1")
