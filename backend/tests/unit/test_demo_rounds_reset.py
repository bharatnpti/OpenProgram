"""DEMO ONLY, never commit: demo check-in rounds reset the day's status (N10, N18).

With ``demo_checkin_rounds`` on, every fan-out asks everyone again, so several
rounds share a date. Each ask resets the person's status for the date to a
partial "Awaiting this round's reply." placeholder: the previous round's final
is no longer what is shown and rolled up, and this round's replies or silence
are judged on their own. This file belongs to the uncommitted demo hack and
goes with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from core.application.status_collector import StatusCollector
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.escalation import EscalationPolicy
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CheckInDefaults, DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.workflows import daily_checkin, nudge
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker, FakeLlmProvider

DAY = date(2026, 1, 12)  # a Monday, inside the default check-in days
AWAITING = "Awaiting this round's reply."


@dataclass
class _Settings:
    demo_checkin_rounds: bool = True
    tenant_default_timezone: str = "UTC"

    def checkin_defaults(self) -> CheckInDefaults:
        return CheckInDefaults(reply_wait_seconds=900, final_reply_wait_seconds=1800)

    def escalation_policy(self) -> EscalationPolicy:
        return EscalationPolicy(steps=())


class _Registry:
    def __init__(
        self, store: InMemoryGraphStore, collector: StatusCollector, settings: _Settings
    ) -> None:
        self.settings = settings
        self._store = store
        self._collector = collector

    def status_repository(self) -> InMemoryGraphStore:
        return self._store

    def status_collector(self) -> StatusCollector:
        return self._collector

    async def close(self) -> None:
        return None


@dataclass
class _World:
    store: InMemoryGraphStore
    llm: FakeLlmProvider
    collector: StatusCollector


def _world(monkeypatch: pytest.MonkeyPatch, *, demo: bool = True, busy: bool = False) -> _World:
    """One member, dev-1; ``busy`` gives them an issue in progress to infer from."""
    tracker = FakeIssueTracker()
    if busy:
        tracker.issues["CHK-14"] = Issue(
            tenant_id="demo",
            key="CHK-14",
            title="Guest checkout test plan",
            state=IssueState.IN_PROGRESS,
            assignee=UserRef(tenant_id="demo", external_id="dev-1"),
        )
    store = InMemoryGraphStore()
    llm = FakeLlmProvider()
    collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=FakeChatProvider(),
        llm_provider=llm,
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        model="test-model",
    )
    registry = _Registry(store, collector, _Settings(demo_checkin_rounds=demo))
    monkeypatch.setattr(daily_checkin, "_service_registry", lambda: registry)
    monkeypatch.setattr(nudge, "_service_registry", lambda: registry)
    return _World(store=store, llm=llm, collector=collector)


async def _ask(correlation_id: str, hour: int) -> daily_checkin.DailyCheckinResult:
    return await daily_checkin.start_daily_checkin_activity(
        daily_checkin.DailyCheckinInput(
            tenant_id="demo",
            developer_id="dev-1",
            developer_name="Sofia",
            chat_external_id="U1",
            correlation_id=correlation_id,
            asked_at=datetime(2026, 1, 12, hour, 0, tzinfo=UTC).isoformat(),
            checkin_date=DAY.isoformat(),
        )
    )


async def _close(correlation_id: str) -> nudge.NudgeResult:
    return await nudge.close_checkin_non_response_activity(
        nudge.NudgeInput(
            tenant_id="demo",
            correlation_id=correlation_id,
            as_of=DAY.isoformat(),
            chat_external_id="U1",
        )
    )


def _final(source: StatusSource, summary: str) -> DeveloperStatus:
    """What a round left as the day's status: a reply's final or its close-out."""
    return DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=DAY,
        source=source,
        blockers=(),
        summary=summary,
    )


def _awaiting(*blockers: str) -> DeveloperStatus:
    return DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=DAY,
        source=StatusSource.PARTIAL,
        blockers=blockers,
        summary=AWAITING,
    )


async def test_a_new_round_resets_the_days_status_to_awaiting_its_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch)
    blocker = "waiting on Noah's review of storefront-web !4"
    await world.store.record_developer_blockers(
        "demo",
        [
            DeveloperBlocker(
                tenant_id="demo",
                blocker_id="blk-1",
                developer_id="dev-1",
                description=blocker,
                normalized_key=normalize_blocker_key(blocker),
                source=BlockerSource.CHECKIN,
                first_seen_on=DAY,
                last_seen_on=DAY,
            )
        ],
    )

    first = await _ask("r1", 12)
    assert first.status == "sent"
    assert await world.store.latest_developer_status("demo", "dev-1", DAY) == _awaiting(blocker)
    # Round 1 ends green: the person confirmed.
    await world.store.record_developer_status(_final(StatusSource.CONFIRMED, "CHK-14 on track."))

    second = await _ask("r2", 18)

    assert second.status == "sent"
    assert second.already_recorded is False
    # Shown and rolled up for the date until this round's reply or close-out:
    # partial, so never green, with the person's open blocker still listed.
    assert await world.store.latest_developer_status("demo", "dev-1", DAY) == _awaiting(blocker)
    # The earlier round stays on record; only its question was closed.
    assert await world.store.checkin_by_correlation("demo", "r1") is not None
    assert await world.store.unconsumed_checkin_correlations_for_user("demo", "U1", DAY) == [
        await world.store.checkin_correlation_by_id("demo", "r2")
    ]
    still_open = await world.store.open_blockers("demo", "dev-1", DAY)
    assert [row.blocker_id for row in still_open] == ["blk-1"]
    # The reset comes after the ask: round 2's question read round 1's status.
    prompt = next(
        request.prompt
        for request in world.llm.requests
        if request.correlation_id == "r2" and request.metadata.get("purpose") == "compose_checkin"
    )
    assert "Last confirmed status from 2026-01-12: CHK-14 on track." in prompt
    assert AWAITING not in prompt


@pytest.mark.parametrize(
    "earlier",
    [
        _final(StatusSource.UNKNOWN, "Replied without a status update. Current status is unknown."),
        _final(StatusSource.INFERRED, "No confirmed check-in after a nudge. Inferred from R1."),
        _final(StatusSource.STALE, "No confirmed check-in after a nudge. Last known R1."),
    ],
    ids=["unknown", "inferred", "stale"],
)
async def test_silence_in_a_new_round_is_closed_on_its_own(
    monkeypatch: pytest.MonkeyPatch, earlier: DeveloperStatus
) -> None:
    # N18: an earlier round's inferred, stale or unknown final for the date
    # used to make this round's close-out return already_closed and keep it.
    world = _world(monkeypatch, busy=True)
    await _ask("r1", 12)
    await world.store.record_developer_status(earlier)
    await _ask("r2", 18)

    closed = await _close("r2")

    assert closed.status == "closed"
    assert closed.terminal_source == "inferred"
    status = await world.store.latest_developer_status("demo", "dev-1", DAY)
    assert status is not None
    assert status.source is StatusSource.INFERRED
    assert status.summary.startswith("No confirmed check-in after a nudge. Inferred from 1 active")
    assert "CHK-14" in status.summary


async def test_a_reply_in_the_new_round_replaces_the_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch)
    await _ask("r1", 12)
    await world.store.record_developer_status(_final(StatusSource.CONFIRMED, "CHK-14 on track."))
    await _ask("r2", 18)

    outcome = await world.collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U1"),
            text="Test plan reviewed, no blockers, ETA unchanged.",
            thread_id="thread-U1",
            message_id="msg-reply-r2",
            correlation_id="r2",
            received_at=datetime(2026, 1, 12, 18, 5, tzinfo=UTC),
        )
    )

    assert outcome.kind == "processed"
    status = await world.store.latest_developer_status("demo", "dev-1", DAY)
    assert status is not None
    assert status.source is StatusSource.CONFIRMED
    assert status.summary != AWAITING
    assert (await _close("r2")).status == "already_replied"


async def test_with_demo_rounds_off_nothing_is_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch, demo=False)
    friday = DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 9),
        source=StatusSource.CONFIRMED,
        blockers=(),
        summary="previous check-in",
    )
    await world.store.record_developer_status(friday)

    first = await _ask("r1", 12)
    again = await _ask("r2", 18)

    assert first.status == "sent"
    # The committed per-date rule: one check-in per person per date.
    assert again.already_recorded is True
    assert again.correlation_id == "r1"
    assert await world.store.latest_developer_status("demo", "dev-1", DAY) == friday
