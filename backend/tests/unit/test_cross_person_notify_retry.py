"""Retrying a counterpart DM that failed to send.

A failed DM used to be logged and never sent: the person asked never heard
about the request unless someone noticed. Each attempt is now claimed and
counted before it is sent, and a scheduled pass re-sends the same DM once the
next attempt is due, until the attempts run out.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from config.settings import Settings
from core.application.cross_person_service import CrossPersonRequestService
from core.domain.cross_person import (
    CrossPersonDelivery,
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestResolution,
    CrossPersonRequestStatus,
)
from core.domain.directory import DirectoryUser
from core.domain.errors import ProviderUnavailable
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.status import CrossPersonMention
from core.domain.workflows import (
    CrossPersonNotifyRetryInput,
    CrossPersonNotifyRetryResult,
    CrossPersonNotifyRetryScheduleConfig,
)
from core.ports.chat import ChatProvider
from infra.adapters.workflows import dbos as dbos_workflows
from infra.adapters.workflows import temporal as temporal_workflows
from infra.adapters.workflows.fake import FakeWorkflowScheduler
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.persistence.postgres_cross_person import PostgresCrossPersonRequestRepository
from infra.registry import ServiceRegistry
from infra.workflows import cross_person_notify_retry

T0 = datetime(2026, 1, 10, 9, 10, tzinfo=UTC)

_REQUESTER = DirectoryUser(
    tenant_id="demo", external_id="U-dev", display_name="Dana Ortiz", email="dana@example.com"
)
_ALICE = DirectoryUser(
    tenant_id="demo", external_id="U-alice", display_name="Alice Chen", email="alice@example.com"
)
_BOB = DirectoryUser(tenant_id="demo", external_id="U-bob", display_name="Bob Lee")


# --- the service --------------------------------------------------------------


async def test_a_failed_dm_is_retried_once_due_and_then_sent() -> None:
    chat = _FlakyChat(failures=1)
    clock = _Clock(T0)
    service, store = await _service(chat, clock)

    created = (await _record(service))[0]

    assert created.notify_message_id is None
    assert created.notify_attempts == 1
    assert created.notify_next_attempt_at == T0 + timedelta(seconds=300)
    assert created.delivery is CrossPersonDelivery.RETRYING

    early = await service.retry_failed_notifications("demo", now=T0 + timedelta(seconds=299))
    assert early.due == 0
    assert len(chat.attempts) == 1

    retried = await service.retry_failed_notifications("demo", now=T0 + timedelta(seconds=300))

    assert (retried.due, retried.sent, retried.failed) == (1, 1, 0)
    stored = await store.get("demo", created.id)
    assert stored is not None
    assert stored.notify_message_id == "msg-U-alice-1"
    assert stored.notify_correlation_id == f"xreq-{created.id}"
    assert stored.notify_attempts == 2
    assert stored.notify_next_attempt_at is None
    assert stored.delivery is CrossPersonDelivery.SENT
    assert stored.status is CrossPersonRequestStatus.OPEN

    # Sent is sent: no later pass picks it up again.
    later = await service.retry_failed_notifications("demo", now=T0 + timedelta(days=3))
    assert later.due == 0
    assert len(chat.delivered) == 1


async def test_the_retried_dm_is_the_same_message_as_the_first_attempt() -> None:
    chat = _FlakyChat(failures=2)
    clock = _Clock(T0)
    service, _ = await _service(chat, clock)
    created = (await _record(service))[0]

    await service.retry_failed_notifications("demo", now=T0 + timedelta(seconds=300))
    await service.retry_failed_notifications("demo", now=T0 + timedelta(seconds=900))

    assert len(chat.attempts) == 3
    texts = {message.text for message in chat.attempts}
    assert texts == {
        "Dana Ortiz asked for your review: API schema review\n"
        "Reply in this thread to acknowledge, or say when it is done."
    }
    assert {message.correlation_id for message in chat.attempts} == {f"xreq-{created.id}"}
    assert {message.metadata["idempotency_key"] for message in chat.attempts} == {
        f"xreq-notify:{created.id}"
    }
    assert {user for user, _ in chat.recipients} == {"U-alice"}
    assert chat.delivered == [chat.attempts[-1]]


async def test_retries_stop_at_the_limit_with_doubling_waits() -> None:
    chat = _FlakyChat(failures=100)
    clock = _Clock(T0)
    service, store = await _service(chat, clock, max_attempts=3, backoff_seconds=60)
    created = (await _record(service))[0]
    assert created.notify_next_attempt_at == T0 + timedelta(seconds=60)

    second = await service.retry_failed_notifications("demo", now=T0 + timedelta(seconds=60))
    assert (second.due, second.failed, second.given_up) == (1, 1, 0)
    after_second = await store.get("demo", created.id)
    assert after_second is not None
    # The wait doubles: 60 s after the first attempt, 120 s after the second.
    assert after_second.notify_next_attempt_at == T0 + timedelta(seconds=180)

    not_yet = await service.retry_failed_notifications("demo", now=T0 + timedelta(seconds=179))
    assert not_yet.due == 0

    last = await service.retry_failed_notifications("demo", now=T0 + timedelta(seconds=180))
    assert (last.due, last.failed, last.given_up) == (1, 1, 1)

    stored = await store.get("demo", created.id)
    assert stored is not None
    assert stored.notify_attempts == 3
    assert stored.notify_next_attempt_at is None
    assert stored.delivery is CrossPersonDelivery.NOT_DELIVERED
    assert stored.status is CrossPersonRequestStatus.OPEN

    nothing = await service.retry_failed_notifications("demo", now=T0 + timedelta(days=30))
    assert nothing.due == 0
    # Recording the same reply again does not get round the limit either.
    await _record(service)
    assert len(chat.attempts) == 3


async def test_a_request_notified_after_it_was_listed_is_not_sent_again() -> None:
    store = _NotifiedElsewhereStore()
    chat = _FlakyChat(failures=1)
    service, _ = await _service(chat, _Clock(T0), store=store)
    created = (await _record(service))[0]

    result = await service.retry_failed_notifications("demo", now=T0 + timedelta(hours=1))

    assert (result.due, result.sent, result.skipped) == (1, 0, 1)
    assert len(chat.attempts) == 1
    stored = await store.get("demo", created.id)
    assert stored is not None
    assert stored.notify_message_id == "msg-sent-elsewhere"
    assert stored.notify_attempts == 1


async def test_concurrent_retry_passes_send_each_dm_once() -> None:
    store = _ListTogetherStore()
    chat = _FlakyChat(failures=2)
    service, _ = await _service(chat, _Clock(T0), store=store)
    created = await _record(
        service,
        _resolution(),
        _resolution(name="Bob Lee", counterpart_id="U-bob", note="deploy key"),
    )
    assert [request.notify_attempts for request in created] == [1, 1]
    chat.yield_on_send = True
    store.barrier = asyncio.Barrier(2)

    # Both passes list the same two requests before either claims one.
    first, second = await asyncio.gather(
        service.retry_failed_notifications("demo", now=T0 + timedelta(hours=1)),
        service.retry_failed_notifications("demo", now=T0 + timedelta(hours=1)),
    )

    assert first.due == second.due == 2
    assert first.sent + second.sent == 2
    assert first.skipped + second.skipped == 2
    assert sorted(user for user, _ in chat.recipients[2:]) == ["U-alice", "U-bob"]
    for request in created:
        stored = await store.get("demo", request.id)
        assert stored is not None
        assert stored.notify_attempts == 2
        assert stored.delivery is CrossPersonDelivery.SENT


async def test_the_retry_pass_is_a_no_op_with_auto_notify_off() -> None:
    chat = _FlakyChat(failures=1)
    service, store = await _service(chat, _Clock(T0))
    created = (await _record(service))[0]
    switched_off = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=InMemoryDirectoryUserRepository(store),
        auto_notify=False,
    )

    result = await switched_off.retry_failed_notifications("demo", now=T0 + timedelta(days=1))

    assert (result.due, result.sent, result.failed, result.skipped) == (0, 0, 0, 0)
    assert len(chat.attempts) == 1
    assert await store.get("demo", created.id) == created


async def test_a_request_recorded_while_notification_was_off_is_never_retried() -> None:
    """Switching notification on later must not DM people about old asks."""
    chat = _FlakyChat(failures=0)
    service, store = await _service(chat, _Clock(T0), auto_notify=False)
    created = (await _record(service))[0]
    assert created.notify_attempts == 0
    assert created.delivery is None
    switched_on = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=InMemoryDirectoryUserRepository(store),
    )

    result = await switched_on.retry_failed_notifications("demo", now=T0 + timedelta(days=1))

    assert result.due == 0
    assert chat.attempts == []


async def test_a_retry_is_not_sent_once_the_request_is_closed() -> None:
    chat = _FlakyChat(failures=1)
    service, store = await _service(chat, _Clock(T0))
    created = (await _record(service))[0]
    await service.update_status("demo", created.id, CrossPersonRequestStatus.RESOLVED)

    result = await service.retry_failed_notifications("demo", now=T0 + timedelta(hours=1))

    assert result.due == 0
    stored = await store.get("demo", created.id)
    assert stored is not None
    assert stored.delivery is CrossPersonDelivery.NOT_DELIVERED
    purposes = [message.metadata["purpose"] for message in chat.attempts]
    assert purposes.count("cross_person_request") == 1


async def test_nobody_to_ask_means_no_attempt_and_no_delivery_state() -> None:
    service, store = await _service(_FlakyChat(failures=0), _Clock(T0))
    unmatched = CrossPersonRequestResolution(
        mention=CrossPersonMention(raw_name="Zed", kind="review", note="the deploy key"),
        status=CrossPersonRequestStatus.NEEDS_RESOLUTION,
    )

    created = await _record(
        service,
        unmatched,
        _resolution(name="Dana Ortiz", counterpart_id="U-dev", note="my own ask"),
    )

    assert [request.notify_attempts for request in created] == [0, 0]
    assert [request.delivery for request in created] == [None, None]
    assert (await service.retry_failed_notifications("demo", now=T0 + timedelta(days=1))).due == 0


def test_the_service_and_the_settings_agree_on_the_retry_limits() -> None:
    defaults = _settings()
    service = CrossPersonRequestService(
        repository=InMemoryGraphStore(),
        chat_provider=_FlakyChat(failures=0),
        directory_repository=InMemoryDirectoryUserRepository(InMemoryGraphStore()),
    )
    configured = _ChatRegistry(
        _settings(
            cross_person_notify_max_attempts=3,
            cross_person_notify_retry_backoff_seconds=60,
        ),
        InMemoryGraphStore(),
        _FlakyChat(failures=0),
    ).cross_person_request_service()

    assert service.notify_max_attempts == defaults.cross_person_notify_max_attempts
    assert service.notify_retry_backoff_seconds == (
        defaults.cross_person_notify_retry_backoff_seconds
    )
    assert (configured.notify_max_attempts, configured.notify_retry_backoff_seconds) == (3, 60)


# --- the scheduled activity ---------------------------------------------------


async def test_retry_activity_runs_one_pass_through_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    registry = _ChatRegistry(_settings(), store, _FlakyChat(failures=1))
    await registry.directory_user_repository().upsert_users([_ALICE, _REQUESTER])
    created = await registry.cross_person_request_service().record_from_checkin(
        tenant_id="demo",
        requester_id="dev-1",
        requester_chat_ref="U-dev",
        source_correlation_id="corr-1",
        resolutions=(_resolution(),),
        observed_at=T0,
    )
    assert created[0].delivery is CrossPersonDelivery.RETRYING
    monkeypatch.setattr(cross_person_notify_retry, "_service_registry", lambda: registry)

    # The scheduled time is long past the first attempt's backoff.
    scheduled = datetime.now(tz=UTC) + timedelta(hours=1)
    result = await cross_person_notify_retry.retry_cross_person_notifications_activity(
        CrossPersonNotifyRetryInput(tenant_id="demo", now=scheduled.isoformat())
    )

    assert result == CrossPersonNotifyRetryResult(tenant_id="demo", status="ran", due=1, sent=1)
    stored = await store.get("demo", created[0].id)
    assert stored is not None
    assert stored.delivery is CrossPersonDelivery.SENT
    assert stored.notify_last_attempt_at == scheduled


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"cross_person_auto_notify": False}, id="auto-notify-off"),
        pytest.param({"cross_person_notify_retry_enabled": False}, id="retry-off"),
    ],
)
async def test_retry_activity_sends_nothing_when_switched_off(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    store = InMemoryGraphStore()
    await store.create(_failed_request(next_attempt_at=T0))
    chat = _FlakyChat(failures=0)
    registry = _ChatRegistry(_settings(**overrides), store, chat)
    monkeypatch.setattr(cross_person_notify_retry, "_service_registry", lambda: registry)

    result = await cross_person_notify_retry.retry_cross_person_notifications_activity(
        CrossPersonNotifyRetryInput(tenant_id="demo", now=T0.isoformat())
    )

    assert result == CrossPersonNotifyRetryResult(tenant_id="demo", status="disabled")
    assert chat.attempts == []


def test_retry_activity_never_stamps_an_attempt_before_the_wall_clock() -> None:
    before = datetime.now(tz=UTC)
    reference = cross_person_notify_retry._reference_time("2020-01-01T00:00:00")
    assert reference >= before
    future = datetime.now(tz=UTC) + timedelta(days=1)
    assert cross_person_notify_retry._reference_time(future.isoformat()) == future


# --- workflow runtimes --------------------------------------------------------


async def test_temporal_retry_workflow_runs_the_retry_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, object]] = []

    async def execute_activity(activity: object, payload: object, **_: object) -> object:
        calls.append((activity, payload))
        return CrossPersonNotifyRetryResult(tenant_id="demo", status="ran")

    monkeypatch.setattr(temporal_workflows.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(temporal_workflows.workflow, "now", lambda: T0)

    result = await temporal_workflows.ScheduledCrossPersonNotifyRetryWorkflow().run(_config())

    assert result.status == "ran"
    assert calls == [
        (
            temporal_workflows.retry_cross_person_notifications_activity,
            CrossPersonNotifyRetryInput(tenant_id="demo", now=T0.isoformat()),
        )
    ]


async def test_temporal_retry_activity_delegates_to_the_shared_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = CrossPersonNotifyRetryInput(tenant_id="demo", now=T0.isoformat())
    expected = CrossPersonNotifyRetryResult(tenant_id="demo", status="ran", sent=2)
    seen: list[CrossPersonNotifyRetryInput] = []

    async def shared(value: CrossPersonNotifyRetryInput) -> CrossPersonNotifyRetryResult:
        seen.append(value)
        return expected

    monkeypatch.setattr(
        cross_person_notify_retry, "retry_cross_person_notifications_activity", shared
    )

    assert await temporal_workflows.retry_cross_person_notifications_activity(payload) == expected
    assert seen == [payload]


def test_temporal_retry_workflow_is_registered_and_scheduled() -> None:
    worker_source = temporal_workflows.TemporalWorkflowWorker.run.__code__.co_names
    ensure = temporal_workflows.TemporalWorkflowScheduler.ensure_cross_person_notify_retry_schedule

    assert "ScheduledCrossPersonNotifyRetryWorkflow" in worker_source
    assert "retry_cross_person_notifications_activity" in worker_source
    assert "ScheduledCrossPersonNotifyRetryWorkflow" in ensure.__code__.co_names


async def test_dbos_scheduled_retry_workflow_runs_the_retry_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[CrossPersonNotifyRetryInput] = []

    async def step(payload: CrossPersonNotifyRetryInput) -> CrossPersonNotifyRetryResult:
        calls.append(payload)
        return CrossPersonNotifyRetryResult(tenant_id=payload.tenant_id, status="ran", sent=1)

    monkeypatch.setattr(dbos_workflows, "dbos_retry_cross_person_notifications_step", step)
    body = inspect.unwrap(dbos_workflows.dbos_scheduled_cross_person_notify_retry_workflow)

    result = await body(T0, {"schedule_id": "retry", "tenant_id": "demo"})

    assert result.sent == 1
    assert calls == [CrossPersonNotifyRetryInput(tenant_id="demo", now=T0.isoformat())]


def test_dbos_retry_schedule_input_runs_the_scheduled_workflow_without_backfill() -> None:
    schedule_input = dbos_workflows._cross_person_notify_retry_schedule_input(_config())

    assert schedule_input["schedule_name"] == "cross-person-notify-retry"
    assert schedule_input["schedule"] == "*/5 * * * *"
    assert schedule_input["context"] == {
        "schedule_id": "cross-person-notify-retry",
        "tenant_id": "demo",
    }
    assert schedule_input["automatic_backfill"] is False
    workflow_fn: object = schedule_input["workflow_fn"]
    assert workflow_fn is dbos_workflows.dbos_scheduled_cross_person_notify_retry_workflow


async def test_dbos_scheduler_applies_the_retry_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[object] = []
    monkeypatch.setattr(dbos_workflows, "_ensure_dbos_runtime", lambda config: False)
    monkeypatch.setattr("infra.adapters.workflows.dbos.DBOS.apply_schedules", applied.append)
    scheduler = dbos_workflows.DbosWorkflowScheduler(
        app_name="openprogram-test",
        system_database_url="postgresql://openprogram:openprogram@localhost:5432/openprogram",
        schedule_id="heartbeat-test",
        tenant_id="demo",
        heartbeat_cron="0 * * * * *",
    )

    result = await scheduler.ensure_cross_person_notify_retry_schedule(_config())

    assert result.schedule_id == "cross-person-notify-retry"
    assert result.status == "configured"
    assert applied == [[dbos_workflows._cross_person_notify_retry_schedule_input(_config())]]


async def test_fake_scheduler_accepts_the_retry_schedule() -> None:
    result = await FakeWorkflowScheduler(
        schedule_id="heartbeat"
    ).ensure_cross_person_notify_retry_schedule(_config())

    assert (result.schedule_id, result.status) == ("cross-person-notify-retry", "ready")


# --- Postgres repository ------------------------------------------------------


async def test_postgres_claim_is_one_conditional_update() -> None:
    executor = _RecordingExecutor(rows=[_row(notify_attempts=2)])
    repository = PostgresCrossPersonRequestRepository(executor)

    claimed = await repository.claim_notification_attempt(
        "demo",
        "xreq-1",
        expected_attempts=1,
        attempted_at=T0,
        next_attempt_at=T0 + timedelta(seconds=600),
    )

    assert claimed is not None
    assert claimed.notify_attempts == 2
    [(query, params)] = executor.calls
    compact = " ".join(query.split())
    assert compact.startswith(
        "UPDATE cross_person_requests SET notify_attempts = notify_attempts + 1"
    )
    for condition in (
        "status = %s",
        "counterpart_id IS NOT NULL",
        "notify_message_id IS NULL",
        "notify_correlation_id IS NULL",
        "notify_attempts = %s",
    ):
        assert condition in compact
    assert params == (T0, T0 + timedelta(seconds=600), "demo", "xreq-1", "open", 1)


async def test_postgres_claim_that_loses_returns_none() -> None:
    repository = PostgresCrossPersonRequestRepository(_RecordingExecutor(rows=[]))

    claimed = await repository.claim_notification_attempt(
        "demo", "xreq-1", expected_attempts=1, attempted_at=T0, next_attempt_at=None
    )

    assert claimed is None


async def test_postgres_lists_only_due_unsent_open_requests_under_the_limit() -> None:
    executor = _RecordingExecutor(rows=[_row(notify_attempts=1, notify_next_attempt_at=T0)])
    repository = PostgresCrossPersonRequestRepository(executor)

    due = await repository.list_notification_retries_due(
        "demo", due_at=T0, max_attempts=5, limit=50
    )

    assert [request.id for request in due] == ["xreq-1"]
    assert due[0].notify_next_attempt_at == T0
    [(query, params)] = executor.calls
    compact = " ".join(query.split())
    for condition in (
        "notify_attempts > 0",
        "notify_attempts < %s",
        "notify_next_attempt_at <= %s",
        "notify_correlation_id IS NULL",
        "ORDER BY notify_next_attempt_at ASC, id ASC",
        "LIMIT %s",
    ):
        assert condition in compact
    assert params == ("demo", "open", 5, T0, 50)


async def test_postgres_recording_the_dm_clears_the_next_attempt() -> None:
    executor = _RecordingExecutor(rows=[_row(notify_message_id="m-1", notify_attempts=2)])
    repository = PostgresCrossPersonRequestRepository(executor)

    updated = await repository.record_notification(
        "demo",
        "xreq-1",
        notify_message_id="m-1",
        notify_correlation_id="xreq-xreq-1",
        updated_at=T0,
    )

    assert updated is not None
    assert updated.delivery is CrossPersonDelivery.SENT
    assert "notify_next_attempt_at = NULL" in executor.calls[0][0]


async def test_postgres_create_writes_the_attempt_columns() -> None:
    executor = _RecordingExecutor(rows=[_row()])
    repository = PostgresCrossPersonRequestRepository(executor)

    await repository.create(_failed_request(next_attempt_at=T0))

    query, params = executor.calls[0]
    assert "notify_attempts, notify_last_attempt_at, notify_next_attempt_at" in query
    assert params[-3:] == (1, T0, T0)


def test_postgres_row_without_attempt_columns_reads_as_no_attempts() -> None:
    row = _row()
    for column in ("notify_attempts", "notify_last_attempt_at", "notify_next_attempt_at"):
        row.pop(column)

    request = asyncio.run(
        PostgresCrossPersonRequestRepository(_RecordingExecutor(rows=[row])).get("demo", "xreq-1")
    )

    assert request is not None
    assert (request.notify_attempts, request.delivery) == (0, None)


# --- helpers ------------------------------------------------------------------


@dataclass
class _Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


@dataclass
class _FlakyChat:
    """Fails the first ``failures`` sends, then delivers; records every attempt."""

    failures: int
    yield_on_send: bool = False
    attempts: list[OutboundMessage] = field(default_factory=list)
    recipients: list[tuple[str, str]] = field(default_factory=list)
    delivered: list[OutboundMessage] = field(default_factory=list)

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        self.attempts.append(message)
        self.recipients.append((user.external_id, message.text))
        if self.yield_on_send:
            # Let a concurrent pass run while this send is in flight.
            await asyncio.sleep(0)
        if self.failures > 0:
            self.failures -= 1
            raise ProviderUnavailable("chat is down")
        self.delivered.append(message)
        return f"msg-{user.external_id}-{len(self.delivered)}"

    async def open_thread(self, user: ChatUserRef) -> str:
        return f"thread-{user.external_id}"

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        return None


class _NotifiedElsewhereStore(InMemoryGraphStore):
    """Another sender records the DM between the pass listing and claiming."""

    async def list_notification_retries_due(
        self,
        tenant_id: str,
        *,
        due_at: datetime,
        max_attempts: int,
        limit: int,
    ) -> list[CrossPersonRequest]:
        due = await super().list_notification_retries_due(
            tenant_id, due_at=due_at, max_attempts=max_attempts, limit=limit
        )
        for request in due:
            await self.record_notification(
                tenant_id,
                request.id,
                notify_message_id="msg-sent-elsewhere",
                notify_correlation_id=f"xreq-{request.id}",
                updated_at=due_at,
            )
        return due


class _ListTogetherStore(InMemoryGraphStore):
    """Holds each pass after listing until every pass has listed."""

    barrier: asyncio.Barrier | None = None

    async def list_notification_retries_due(
        self,
        tenant_id: str,
        *,
        due_at: datetime,
        max_attempts: int,
        limit: int,
    ) -> list[CrossPersonRequest]:
        due = await super().list_notification_retries_due(
            tenant_id, due_at=due_at, max_attempts=max_attempts, limit=limit
        )
        if self.barrier is not None:
            await self.barrier.wait()
        return due


class _ChatRegistry(ServiceRegistry):
    def __init__(self, settings: Settings, store: InMemoryGraphStore, chat: _FlakyChat) -> None:
        super().__init__(settings, graph_store=store)
        self.chat = chat

    def chat_provider(self) -> ChatProvider:
        return self.chat


@dataclass
class _RecordingExecutor:
    rows: list[dict[str, object]]
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        self.calls.append((query, tuple(params)))
        return None

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        self.calls.append((query, tuple(params)))
        return self.rows


async def _service(
    chat: _FlakyChat,
    clock: _Clock,
    *,
    store: InMemoryGraphStore | None = None,
    auto_notify: bool = True,
    max_attempts: int = 5,
    backoff_seconds: int = 300,
) -> tuple[CrossPersonRequestService, InMemoryGraphStore]:
    store = store if store is not None else InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users([_ALICE, _BOB, _REQUESTER])
    service = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=directory,
        time_series_repository=store,
        auto_notify=auto_notify,
        notify_max_attempts=max_attempts,
        notify_retry_backoff_seconds=backoff_seconds,
        clock=clock,
    )
    return service, store


async def _record(
    service: CrossPersonRequestService,
    *resolutions: CrossPersonRequestResolution,
) -> list[CrossPersonRequest]:
    return await service.record_from_checkin(
        tenant_id="demo",
        requester_id="dev-1",
        requester_chat_ref="U-dev",
        source_correlation_id="corr-1",
        resolutions=resolutions or (_resolution(),),
        observed_at=T0,
    )


def _resolution(
    *,
    name: str = "Alice Chen",
    counterpart_id: str = "U-alice",
    note: str = "API schema review",
) -> CrossPersonRequestResolution:
    return CrossPersonRequestResolution(
        mention=CrossPersonMention(raw_name=name, kind="review", note=note),
        status=CrossPersonRequestStatus.OPEN,
        counterpart_id=counterpart_id,
        counterpart_display_name=name,
    )


def _failed_request(*, next_attempt_at: datetime | None) -> CrossPersonRequest:
    return CrossPersonRequest(
        tenant_id="demo",
        id="xreq-1",
        requester_id="dev-1",
        requester_chat_ref="U-dev",
        counterpart_id="U-alice",
        kind=CrossPersonRequestKind.REVIEW,
        note="API schema review",
        source_correlation_id="corr-1",
        status=CrossPersonRequestStatus.OPEN,
        created_at=T0,
        updated_at=T0,
        counterpart_display_name="Alice Chen",
        notify_attempts=1,
        notify_last_attempt_at=T0,
        notify_next_attempt_at=next_attempt_at,
    )


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "tenant_id": "demo",
        "id": "xreq-1",
        "requester_id": "dev-1",
        "requester_chat_ref": "U-dev",
        "counterpart_id": "U-alice",
        "kind": "review",
        "note": "API schema review",
        "task_kind": None,
        "task_id": None,
        "source_correlation_id": "corr-1",
        "status": "open",
        "created_at": T0,
        "updated_at": T0,
        "raw_name": "Alice Chen",
        "email": None,
        "counterpart_display_name": "Alice Chen",
        "counterpart_email": None,
        "notify_message_id": None,
        "notify_correlation_id": None,
        "notify_attempts": 0,
        "notify_last_attempt_at": None,
        "notify_next_attempt_at": None,
    }
    row.update(overrides)
    return row


def _config() -> CrossPersonNotifyRetryScheduleConfig:
    return CrossPersonNotifyRetryScheduleConfig(
        schedule_id="cross-person-notify-retry",
        tenant_id="demo",
        cron="*/5 * * * *",
    )


def _settings(**overrides: object) -> Settings:
    values = {
        "secret_key": "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        "runtime_mode": "memory",
        "chat_provider": "fake",
        "directory_provider": "fake",
        "issue_tracker_provider": "fake",
        "vcs_provider": "fake",
        "calendar_provider": "fake",
        "llm_provider": "fake",
        "workflow_provider": "fake",
        **overrides,
    }
    return Settings.model_validate(values)
