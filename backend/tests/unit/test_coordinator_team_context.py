"""A coordinator's reply that gives team context is confirmed, with no ETA (N16).

R2 live: Ira, a scrum master with no tickets of her own, could never reach
confirmed. Each reply was asked for an ETA she does not have, and her day ended
partial ("ETA was not provided"), which kept two pods, two projects and the
program amber. A scrum master, product owner or exec with no issue of their own
under way now owes no ETA, and a reply that mentions the team's work, issues or
progress, or says plainly that nothing is blocking, records confirmed. A bare
"no update" stays a reply without a status (N22), and silence is never
confirmed.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from core.application.status_collector import NON_STATUS_ACK_TEXT, StatusCollector
from core.application.team_context import gives_team_context
from core.domain.graph import Developer
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CheckIn, CheckInSignals, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

_TENANT = "demo"
_IRA = "U-ira"
_ELENA = "U-elena"
_ASKED_AT = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)
_DAY = date(2026, 10, 3)


def _evaluation(
    *,
    note: str,
    question: str | None = None,
    blockers_answered: bool = False,
    claims: list[dict[str, object]] | None = None,
) -> str:
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": question is None,
            "question": question,
            "signals": {
                "progress_note": note,
                "blockers": [],
                "eta_change_days": None,
                "blockers_answered": blockers_answered,
                "eta_answered": False,
                "issue_updates": claims or [],
            },
        }
    )


_NOT_A_STATUS = json.dumps(
    {"is_status_update": False, "sufficient": False, "question": None, "signals": None}
)


async def _store(*, roles: str = "sm", person: str = _IRA) -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    await store.upsert_node(
        Developer(tenant_id=_TENANT, id=person, name=person, metadata={"app_roles": roles})
    )
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=person,
            correlation_id=f"corr-{person}",
            asked_at=_ASKED_AT,
            replied_at=None,
            raw_reply=None,
            signals=None,
            checkin_date=_DAY,
        )
    )
    return store


def _collector(
    store: InMemoryGraphStore,
    chat: FakeChatProvider,
    texts: list[str],
    tracker: FakeIssueTracker | None = None,
) -> StatusCollector:
    return StatusCollector(
        issue_tracker=tracker or FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=SequenceLlmProvider(texts=texts),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
        checkin_ack_enabled=False,
    )


def _message(text: str, *, person: str = _IRA, message_id: str = "m1") -> InboundMessage:
    return InboundMessage(
        tenant_id=_TENANT,
        user=ChatUserRef(tenant_id=_TENANT, external_id=person),
        text=text,
        thread_id=f"thread-{person}",
        message_id=message_id,
        correlation_id=f"corr-{person}",
        received_at=datetime(2026, 10, 3, 18, 4, tzinfo=UTC),
    )


async def test_iras_sm_team_summary_is_confirmed_without_an_eta() -> None:
    store = await _store()
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            # R2: the model asked her for an ETA she does not have.
            _evaluation(
                note="Quiet day; Payments and Identity moving along fine, no new blockers.",
                question="What is your ETA for the current sprint work?",
                blockers_answered=True,
            )
        ],
    )

    outcome = await collector.handle_reply(
        _message(
            "Quiet day on my end - both Payments and Identity are moving along fine. "
            "No new blockers to report."
        )
    )

    assert chat.sent == []
    assert outcome.kind == "processed"
    assert outcome.status is not None
    assert outcome.status.source is StatusSource.CONFIRMED
    assert "ETA" not in outcome.status.summary
    stored = await store.latest_developer_status(_TENANT, _IRA, _DAY)
    assert stored is not None
    assert stored.source is StatusSource.CONFIRMED


async def test_a_first_team_summary_needs_no_progress_follow_up() -> None:
    """R1: "no tickets; ... stand-ups fine, nothing blocking" drew two follow-ups."""
    store = await _store()
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            _evaluation(
                note="No tickets; Payments and Identity stand-ups fine, nothing blocking.",
                question="Could you share specific progress on the teams' current issues?",
            )
        ],
    )

    outcome = await collector.handle_reply(
        _message("no tickets; Payments and Identity stand-ups fine, nothing blocking")
    )

    assert chat.sent == []
    assert outcome.status is not None
    assert outcome.status.source is StatusSource.CONFIRMED


async def test_a_bare_no_update_stays_a_reply_without_a_status() -> None:
    store = await _store()
    chat = FakeChatProvider()
    collector = _collector(store, chat, [_NOT_A_STATUS])

    outcome = await collector.handle_reply(_message("No update."))

    assert outcome.kind == "acknowledged"
    assert [message.text for message in chat.sent] == [NON_STATUS_ACK_TEXT]
    assert await store.latest_developer_status(_TENANT, _IRA, _DAY) is None


async def test_no_update_read_as_status_is_not_confirmed_and_asks_no_eta() -> None:
    """Even when the model calls it a status, "no update" gives no team context."""
    store = await _store()
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            _evaluation(
                note="No update from the team.",
                question="What is your ETA, and are there any blockers?",
            )
        ],
    )

    outcome = await collector.handle_reply(_message("No update from the team."))

    assert outcome.kind == "clarifying"
    assert [message.text for message in chat.sent] == ["Thanks. Is anything blocking your team?"]
    stored = await store.latest_developer_status(_TENANT, _IRA, _DAY)
    assert stored is not None
    assert stored.source is StatusSource.PARTIAL
    assert "ETA" not in stored.summary


async def test_an_exec_answering_the_blockers_question_is_confirmed() -> None:
    store = await _store(roles="exec", person=_ELENA)
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            _evaluation(note="All quiet.", question="Any blockers or ETA changes?"),
            _evaluation(note="Nothing blocking.", blockers_answered=True),
        ],
    )

    first = await collector.handle_reply(_message("all quiet", person=_ELENA))
    second = await collector.handle_reply(
        _message("Nothing is blocking anyone.", person=_ELENA, message_id="m2")
    )

    assert first.kind == "clarifying"
    assert [message.text for message in chat.sent] == ["Thanks. Is anything blocking your team?"]
    assert second.kind == "processed"
    assert second.status is not None
    assert second.status.source is StatusSource.CONFIRMED


async def test_a_coordinator_with_an_issue_under_way_still_owes_its_eta() -> None:
    """Role alone decides nothing (837f0818): her own in-progress issue needs an ETA."""
    store = await _store()
    chat = FakeChatProvider()
    tracker = FakeIssueTracker(
        issues={
            "OPS-1": Issue(
                tenant_id=_TENANT,
                key="OPS-1",
                title="Sprint review deck",
                state=IssueState.IN_PROGRESS,
                assignee=UserRef(tenant_id=_TENANT, external_id=_IRA),
                updated_at=_ASKED_AT,
            )
        }
    )
    collector = _collector(
        store,
        chat,
        [
            _evaluation(
                note="OPS-1 on track, no blockers.",
                blockers_answered=True,
                claims=[{"issue_key": "OPS-1", "claimed_state": "on track", "note": ""}],
            )
        ],
        tracker=tracker,
    )

    outcome = await collector.handle_reply(_message("OPS-1 on track, no blockers."))

    assert outcome.kind == "clarifying"
    assert [message.text for message in chat.sent] == [
        "Thanks. What is your ETA to finish OPS-1 (Sprint review deck)?"
    ]


async def test_a_developer_is_still_asked_for_an_eta() -> None:
    store = await _store(roles="dev")
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [_evaluation(note="Both pods moving along fine, no blockers.", blockers_answered=True)],
    )

    outcome = await collector.handle_reply(_message("Both pods moving along fine, no blockers."))

    assert outcome.kind == "clarifying"
    assert [message.text for message in chat.sent] == ["Thanks. What is your ETA to finish it?"]


async def test_a_silent_coordinator_is_never_confirmed() -> None:
    store = await _store()
    collector = _collector(store, FakeChatProvider(), [])

    closed = await collector.record_non_response(
        tenant_id=_TENANT,
        developer_id=_IRA,
        as_of=_DAY,
        correlation_id=f"corr-{_IRA}",
    )

    assert closed.source is not StatusSource.CONFIRMED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Payments on track; CHK-4 ramping up.", True),
        ("Both teams are making good progress on the sprint.", True),
        ("Stand-ups fine.", True),
        ("Nothing is blocking either pod.", True),
        ("No blockers from my end.", True),
        ("No update from the team.", False),
        ("No updates today.", False),
        ("Nothing new.", False),
        ("all quiet", False),
        ("I'm travelling, will check in tomorrow.", False),
    ],
)
def test_what_gives_team_context(text: str, expected: bool) -> None:
    assert gives_team_context(CheckInSignals(progress_note=text), text) is expected


def test_issue_claims_and_stated_blockers_give_team_context() -> None:
    claims = CheckInSignals(progress_note="", issue_updates=())
    assert not gives_team_context(claims, "")
    assert gives_team_context(CheckInSignals(progress_note="", blockers=("CI is down",)), "")
    assert gives_team_context(CheckInSignals(progress_note="", blockers_answered=True), "")
