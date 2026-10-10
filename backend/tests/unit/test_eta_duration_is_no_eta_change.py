"""A duration from now is an ETA window, not an ETA change (N45).

R5 live: the bot asked Sofia "What is your ETA to finish CHK-14 (Test plan:
guest checkout)?" and she replied "2-3 days to have the test plan reviewed and
finalized." The model gave eta_change_days 2, her status stored it, and the
Signals feed read "eta change +2d", as if CHK-14 had slipped two days.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from core.application.portfolio_feed_service import PortfolioFeedService
from core.application.status_collector import StatusCollector
from core.domain.graph import Developer, EdgeKind, GraphEdge, Project, Task
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CheckIn, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

_TENANT = "qa"
_SOFIA = "dev-sofia"
_DAY = date(2026, 10, 4)  # R5, a Sunday
_REPLY = "2-3 days to have the test plan reviewed and finalized."


def _evaluation(reply_note: str, *, eta_change_days: int | None) -> str:
    """The evaluator's reading of the answer, with the model's eta_change_days."""
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": True,
            "question": None,
            "signals": {
                "progress_note": "CHK-14 MR !4 is under review and still on track.",
                "blockers": [],
                "eta_change_days": eta_change_days,
                "blockers_answered": True,
                "eta_answered": True,
                "issue_updates": [
                    {
                        "issue_key": "CHK-14",
                        "claimed_done": False,
                        "claimed_state": "in review",
                        "note": reply_note,
                    }
                ],
            },
        }
    )


async def _seed(store: InMemoryGraphStore) -> None:
    await store.upsert_node(Project(tenant_id=_TENANT, id="proj-chk", name="Checkout"))
    await store.upsert_node(
        Task(
            tenant_id=_TENANT,
            id="CHK-14",
            name="Test plan: guest checkout",
            metadata={"key": "CHK-14", "state": "in_progress", "status": "In Progress"},
        )
    )
    await store.upsert_node(Developer(tenant_id=_TENANT, id=_SOFIA, name="Sofia Bergmann"))
    await store.add_edge(
        GraphEdge(
            tenant_id=_TENANT, from_node_id="proj-chk", to_node_id="CHK-14", kind=EdgeKind.CONTAINS
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id=_TENANT, from_node_id=_SOFIA, to_node_id="CHK-14", kind=EdgeKind.ASSIGNED_TO
        )
    )
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_SOFIA,
            correlation_id="corr-sofia",
            asked_at=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )


async def _answer(store: InMemoryGraphStore, text: str, evaluation: str) -> None:
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[evaluation]),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
        checkin_ack_enabled=False,
    )
    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id=_TENANT,
            user=ChatUserRef(tenant_id=_TENANT, external_id=_SOFIA),
            text=text,
            thread_id="thread-sofia",
            message_id="msg-sofia",
            correlation_id="corr-sofia",
            received_at=datetime(2026, 10, 4, 12, 7, 55, tzinfo=UTC),
        )
    )
    assert outcome.kind == "processed"


async def _feed_summaries(store: InMemoryGraphStore) -> list[str]:
    feed = await PortfolioFeedService(store).feed(
        _TENANT, since=datetime(2026, 10, 4, tzinfo=UTC), sources=("checkin",)
    )
    return [item.summary for item in feed.items]


async def test_sofias_duration_is_recorded_as_a_window_and_no_eta_change() -> None:
    store = InMemoryGraphStore()
    await _seed(store)

    await _answer(
        store,
        _REPLY,
        _evaluation("2-3 days to have the test plan reviewed and finalized", eta_change_days=2),
    )

    status = await store.latest_developer_status(_TENANT, _SOFIA, _DAY)
    assert status is not None
    assert status.eta_change_days is None
    # The ETA is answered, so the check-in is confirmed and not asked again.
    assert status.source is StatusSource.CONFIRMED
    etas = [
        fact.payload
        for fact in await store.list_recent_facts(
            _TENANT, since=datetime(2026, 10, 4, tzinfo=UTC), sources=("checkin_drift",)
        )
        if fact.payload.get("kind") == "eta_stated"
    ]
    assert [(eta["issue_key"], eta["eta_start"], eta["eta_date"]) for eta in etas] == [
        ("CHK-14", "2026-10-06", "2026-10-07")
    ]
    assert await _feed_summaries(store) == [
        "Check-in updated for Sofia Bergmann: confirmed, 0 blockers"
    ]


async def test_an_eta_pushed_by_two_days_is_still_an_eta_change() -> None:
    store = InMemoryGraphStore()
    await _seed(store)

    await _answer(
        store, "CHK-14 pushed by 2 days", _evaluation("Pushed by 2 days", eta_change_days=2)
    )

    status = await store.latest_developer_status(_TENANT, _SOFIA, _DAY)
    assert status is not None and status.eta_change_days == 2
    assert await _feed_summaries(store) == [
        "Check-in updated for Sofia Bergmann: confirmed, 0 blockers, eta change +2d"
    ]
