"""A follow-up never asks for what an earlier message of the check-in already gave (N29).

R4 live: the evaluator reads the latest message on its own. Mina was asked
"What is your ETA for CHK-10?" after "wrapping up Monday EOD", Sofia "Do you
have any blockers ...?" after "No blockers.", and Asha for her "concrete
progress" on CHK-16 after "agenda and numbers drafted".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from core.application.status_collector import StatusCollector, _follow_up_already_answered
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CheckIn, CheckInSignals, IssueClaim, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

_TENANT = "demo"


def _evaluation(
    *,
    sufficient: bool,
    question: str | None,
    claims: list[dict[str, object]],
    blockers_answered: bool,
    eta_answered: bool,
    note: str,
) -> str:
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": sufficient,
            "question": question,
            "signals": {
                "progress_note": note,
                "blockers": [],
                "eta_change_days": None,
                "blockers_answered": blockers_answered,
                "eta_answered": eta_answered,
                "issue_updates": claims,
            },
        }
    )


async def _open_checkin(store: InMemoryGraphStore, developer: str) -> None:
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=developer,
            correlation_id=f"corr-{developer}",
            asked_at=datetime(2026, 10, 4, 6, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )


def _collector(
    store: InMemoryGraphStore, chat: FakeChatProvider, texts: list[str]
) -> StatusCollector:
    return StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=SequenceLlmProvider(texts=texts),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
        checkin_ack_enabled=False,
    )


def _message(developer: str, text: str, message_id: str, minute: int) -> InboundMessage:
    return InboundMessage(
        tenant_id=_TENANT,
        user=ChatUserRef(tenant_id=_TENANT, external_id=developer),
        text=text,
        thread_id=f"thread-{developer}",
        message_id=message_id,
        correlation_id=f"corr-{developer}",
        received_at=datetime(2026, 10, 4, 6, minute, tzinfo=UTC),
    )


async def test_sofia_is_not_asked_for_blockers_after_no_blockers() -> None:
    store = InMemoryGraphStore()
    await _open_checkin(store, "U-sofia")
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            _evaluation(
                sufficient=False,
                question="What is your ETA for CHK-14?",
                claims=[
                    {"issue_key": "CHK-14", "claimed_state": "in progress", "note": "MR !4 idle"},
                    {"issue_key": "CHK-15", "claimed_state": "not started", "note": ""},
                    {"issue_key": "IDP-7", "claimed_state": "not started", "note": ""},
                ],
                blockers_answered=True,
                eta_answered=False,
                note="CHK-14 still on track, MR !4 is idle. No blockers.",
            ),
            _evaluation(
                sufficient=False,
                question="Do you have any blockers for CHK-14, CHK-15, or IDP-7?",
                claims=[{"issue_key": "CHK-14", "note": "ETA 2-3 days, reviewing with team"}],
                blockers_answered=False,
                eta_answered=True,
                note="2-3 days, reviewing the guest checkout flow with the team.",
            ),
        ],
    )

    first = await collector.handle_reply(
        _message(
            "U-sofia",
            "CHK-14 still on track, MR !4 is idle. CHK-15 and IDP-7 haven't started yet. "
            "No blockers.",
            "m1",
            4,
        )
    )
    second = await collector.handle_reply(
        _message("U-sofia", "2-3 days, we're reviewing the guest checkout flow.", "m2", 5)
    )

    assert first.kind == "clarifying"
    assert [message.text for message in chat.sent] == ["What is your ETA for CHK-14?"]
    assert second.kind == "processed"
    assert second.status is not None
    assert second.status.source is StatusSource.CONFIRMED


async def test_asha_is_not_asked_for_progress_her_first_message_gave() -> None:
    store = InMemoryGraphStore()
    await _open_checkin(store, "U-asha")
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            # No ETA yet: the collector asks for it.
            _evaluation(
                sufficient=True,
                question=None,
                claims=[
                    {
                        "issue_key": "CHK-16",
                        "claimed_state": "on track",
                        "note": "Agenda and numbers drafted.",
                    }
                ],
                blockers_answered=True,
                eta_answered=False,
                note="CHK-16 still on track, agenda and numbers drafted, no blockers.",
            ),
            _evaluation(
                sufficient=False,
                question=(
                    "Could you provide a brief note on your concrete progress for CHK-16 "
                    "since your last update?"
                ),
                claims=[{"issue_key": "CHK-16", "note": "ETA unchanged, final pass this week."}],
                blockers_answered=False,
                eta_answered=True,
                note="CHK-16 ETA unchanged, final pass this week.",
            ),
        ],
    )

    first = await collector.handle_reply(
        _message(
            "U-asha", "CHK-16 still on track, agenda and numbers drafted, no blockers.", "m1", 4
        )
    )
    second = await collector.handle_reply(
        _message("U-asha", "CHK-16 ETA unchanged, final pass this week.", "m2", 5)
    )

    assert first.kind == "clarifying"
    assert len(chat.sent) == 1
    assert "ETA" in chat.sent[0].text
    assert second.kind == "processed"


def test_mina_is_not_asked_for_the_eta_of_her_first_message() -> None:
    # R4 checkins.signals held after Mina's first message.
    earlier = CheckInSignals(
        progress_note="CHK-10 still in review with Asha; wrapping up Monday EOD.",
        blockers_answered=True,
        eta_answered=True,
        issue_updates=(
            IssueClaim(
                issue_key="CHK-10",
                claimed_state="in review",
                note="Aligned on the acceptance criteria outline; wrapping up Monday EOD.",
            ),
        ),
    )

    assert _follow_up_already_answered("What is your ETA for CHK-10?", earlier)


async def test_ira_first_message_progress_question_is_still_asked() -> None:
    # Whether a first message's progress is concrete stays the evaluator's call.
    store = InMemoryGraphStore()
    await _open_checkin(store, "U-ira")
    chat = FakeChatProvider()
    question = (
        "Can you specify what concrete progress was made on CHK-4 or IDP-3 since the last check-in?"
    )
    collector = _collector(
        store,
        chat,
        [
            _evaluation(
                sufficient=False,
                question=question,
                claims=[
                    {"issue_key": "CHK-4", "claimed_state": "ramping up", "note": ""},
                    {"issue_key": "IDP-3", "claimed_state": "in review", "note": ""},
                ],
                blockers_answered=True,
                eta_answered=False,
                note="Payments and Identity both on track.",
            )
        ],
    )

    outcome = await collector.handle_reply(
        _message("U-ira", "Both on track, no blockers. CHK-4 ramping up.", "m1", 4)
    )

    assert outcome.kind == "clarifying"
    assert [message.text for message in chat.sent] == [question]


@pytest.mark.parametrize(
    "question",
    [
        # A contradiction with the tracker is always asked.
        "You said CHK-3 is done, but Jira shows In Progress. Is it merged?",
        # Progress on an issue no earlier message described.
        "What progress did you make on CHK-9?",
        # A question that asks for none of the ETA, blockers or progress.
        "Who is reviewing CHK-10?",
    ],
)
def test_other_follow_ups_are_still_asked(question: str) -> None:
    earlier = CheckInSignals(
        progress_note="CHK-10 in review.",
        blockers_answered=True,
        eta_answered=True,
        issue_updates=(IssueClaim(issue_key="CHK-10", claimed_state="in review"),),
    )

    assert not _follow_up_already_answered(question, earlier)


def test_a_follow_up_for_a_detail_still_missing_is_asked() -> None:
    earlier = CheckInSignals(progress_note="CHK-14 on track.", blockers_answered=True)

    assert not _follow_up_already_answered("What is your ETA for CHK-14?", earlier)
    assert _follow_up_already_answered("Any blockers on CHK-14?", earlier)
