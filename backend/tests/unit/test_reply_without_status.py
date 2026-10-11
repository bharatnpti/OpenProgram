"""A reply without a status never counts as green, read by rules first (V2 B16b).

V2 live: Elena, an exec with no tickets, answered "Thanks, nothing from me today.
No blockers on my side and no open tickets or MRs." The model called it a
status, the coordinator rule (N16) took "No blockers" for team context, and her
day was recorded confirmed: her No-pod tile read green. In V1 her "thanks,
nothing from me" had stayed a reply without a status because the model said so.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from core.application.reply_without_status import depends_on_own_work, reads_without_status
from core.application.status_collector import NON_STATUS_ACK_TEXT, StatusCollector
from core.application.status_parsing import ClarificationDecision, ClarificationEvaluator
from core.application.status_summaries import NON_STATUS_REPLY_SUMMARY
from core.domain.graph import Developer
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CheckIn, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

# Elena's V2 reply, word for word.
_ELENA_V2 = "Thanks, nothing from me today. No blockers on my side and no open tickets or MRs."
# What the model answered for it in V2: a status, which the coordinator rule
# then recorded confirmed ("No progress reported; nothing to update today.").
_V2_READ_AS_STATUS = json.dumps(
    {
        "is_status_update": True,
        "sufficient": True,
        "question": None,
        "signals": {
            "progress_note": "No progress reported; nothing to update today.",
            "blockers": [],
            "eta_change_days": None,
            "blockers_answered": True,
            "eta_answered": False,
        },
    }
)
_QUIET_DAY_STATUS = json.dumps(
    {
        "is_status_update": True,
        "sufficient": True,
        "question": None,
        "signals": {
            "progress_note": "Quiet day on CHK-4, nothing blocking, ETA unchanged.",
            "blockers": [],
            "eta_change_days": None,
            "blockers_answered": True,
            "eta_answered": True,
        },
    }
)
_TENANT = "demo"
_DAY = date(2026, 10, 11)
_ASKED_AT = datetime(2026, 10, 11, 4, 47, tzinfo=UTC)


@pytest.mark.parametrize(
    "reply",
    [
        "thanks, nothing from me",  # Elena, V1
        "thanks, no update from me",  # Elena, R2
        "no updates today",  # Elena, R3 (N22)
        "No update.",
        "Thanks! Nothing to report from my side. Have a nice weekend!",
        "Thanks, no open tickets or MRs on my side.",
        "Thanks, I\u2019ve got nothing to report today.",
        ":+1:",
        "ok",
    ],
)
def test_acknowledgements_and_nothing_from_me_carry_no_status_for_anyone(reply: str) -> None:
    assert reads_without_status(reply, has_own_work=True)
    assert reads_without_status(reply, has_own_work=False)
    assert not depends_on_own_work(reply)


def test_elenas_v2_reply_carries_no_status_from_someone_with_no_work() -> None:
    assert reads_without_status(_ELENA_V2, has_own_work=False)
    # From someone with open work "no blockers" is a status, and an unknown
    # tracker reads as if they had work: the model reads it.
    assert not reads_without_status(_ELENA_V2, has_own_work=True)
    assert not reads_without_status(_ELENA_V2, has_own_work=None)
    assert depends_on_own_work(_ELENA_V2)


@pytest.mark.parametrize(
    "reply",
    [
        # Ira (sm, no tickets of her own), R1, R2 and R5: team context (N16).
        "no tickets; Payments and Identity stand-ups fine, nothing blocking",
        "Quiet day on my end - both Payments and Identity are moving along fine. "
        "No new blockers to report.",
        "Payments and Identity both tracking well, no new blockers. The teams have what they "
        "need to keep moving forward.",
        # Hana, V2.
        "Hi! IDP-9 (Enterprise SSO onboarding guide) is in progress, about half done. No "
        "blockers, ETA hasn't changed. No MR for it, it's docs only.",
        # A quiet day, an answer to a blockers question, team words.
        "Quiet day, nothing blocking.",
        "Nothing is blocking anyone.",
        "No blockers on my side.",
        "Nothing.",
        "all quiet",
        "No update from the team.",
        "No issues",
        "Thanks, on it",
        "Nothing new, still working on the migration",
    ],
)
def test_replies_with_any_status_words_are_left_to_the_model(reply: str) -> None:
    assert not reads_without_status(reply, has_own_work=True)
    assert not reads_without_status(reply, has_own_work=False)


def test_nothing_from_me_with_no_blockers_is_a_status_from_someone_with_tasks() -> None:
    reply = "Thanks, nothing from me today. No blockers."
    assert not reads_without_status(reply, has_own_work=True)
    assert reads_without_status(reply, has_own_work=False)


async def test_the_evaluator_does_not_ask_the_model_about_elenas_reply() -> None:
    llm = SequenceLlmProvider(texts=[_V2_READ_AS_STATUS])
    evaluator = ClarificationEvaluator(llm, model="test-model")

    decision = await evaluator.evaluate(
        tenant_id=_TENANT,
        developer_id="U-elena",
        raw_reply=_ELENA_V2,
        correlation_id="corr-elena",
        has_own_work=False,
    )

    assert decision == ClarificationDecision(sufficient=False, is_status_update=False)
    assert llm.requests == []


async def test_a_short_answer_after_a_status_is_still_the_models_to_read() -> None:
    llm = SequenceLlmProvider(texts=[_QUIET_DAY_STATUS])
    evaluator = ClarificationEvaluator(llm, model="test-model")

    decision = await evaluator.evaluate(
        tenant_id=_TENANT,
        developer_id="U-liam",
        raw_reply="nothing new from me",
        correlation_id="corr-liam",
        checkin_text="CHK-4 is in review, waiting on Noah.\nnothing new from me",
        has_own_work=True,
    )

    assert decision.is_status_update
    assert len(llm.requests) == 1


async def _store(person: str, *, roles: str) -> InMemoryGraphStore:
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
    llm: SequenceLlmProvider,
    tracker: FakeIssueTracker,
) -> StatusCollector:
    return StatusCollector(
        issue_tracker=tracker,
        chat_provider=chat,
        llm_provider=llm,
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
        checkin_ack_enabled=False,
    )


def _message(person: str, text: str) -> InboundMessage:
    return InboundMessage(
        tenant_id=_TENANT,
        user=ChatUserRef(tenant_id=_TENANT, external_id=person),
        text=text,
        thread_id=f"thread-{person}",
        message_id="m1",
        correlation_id=f"corr-{person}",
        received_at=datetime(2026, 10, 11, 4, 48, tzinfo=UTC),
    )


def _in_progress(person: str, key: str) -> FakeIssueTracker:
    return FakeIssueTracker(
        issues={
            key: Issue(
                tenant_id=_TENANT,
                key=key,
                title="Step-up flow",
                state=IssueState.IN_PROGRESS,
                assignee=UserRef(tenant_id=_TENANT, external_id=person),
                updated_at=_ASKED_AT,
            )
        }
    )


async def test_elenas_v2_reply_is_recorded_without_a_status_never_green() -> None:
    person = "U-elena"
    store = await _store(person, roles="exec")
    chat = FakeChatProvider()
    llm = SequenceLlmProvider(texts=[_V2_READ_AS_STATUS])
    collector = _collector(store, chat, llm, FakeIssueTracker())

    outcome = await collector.handle_reply(_message(person, _ELENA_V2))
    closed = await collector.record_non_response(
        tenant_id=_TENANT, developer_id=person, as_of=_DAY, correlation_id=f"corr-{person}"
    )

    assert outcome.kind == "acknowledged"
    assert llm.requests == []
    assert [message.text for message in chat.sent] == [NON_STATUS_ACK_TEXT]
    assert closed.source is StatusSource.UNKNOWN
    assert closed.summary == NON_STATUS_REPLY_SUMMARY
    stored = await store.latest_developer_status(_TENANT, person, _DAY)
    assert stored is not None
    assert stored.source is StatusSource.UNKNOWN


async def test_the_same_words_from_someone_with_tasks_are_read_as_their_quiet_day() -> None:
    person = "U-liam"
    store = await _store(person, roles="dev")
    chat = FakeChatProvider()
    llm = SequenceLlmProvider(texts=[_QUIET_DAY_STATUS])
    collector = _collector(store, chat, llm, _in_progress(person, "CHK-4"))

    outcome = await collector.handle_reply(
        _message(person, "Thanks, nothing from me today. No blockers on my side.")
    )

    assert len(llm.requests) == 1
    assert outcome.kind == "processed"
    assert outcome.status is not None
    assert outcome.status.source is StatusSource.CONFIRMED


async def test_an_honest_quiet_day_from_someone_with_tasks_is_still_a_status() -> None:
    person = "U-liam"
    store = await _store(person, roles="dev")
    llm = SequenceLlmProvider(texts=[_QUIET_DAY_STATUS])
    collector = _collector(store, FakeChatProvider(), llm, _in_progress(person, "CHK-4"))

    outcome = await collector.handle_reply(
        _message(person, "Quiet day on CHK-4, nothing blocking, ETA unchanged.")
    )

    assert len(llm.requests) == 1
    assert outcome.status is not None
    assert outcome.status.source is StatusSource.CONFIRMED


class _UnreadableTracker(FakeIssueTracker):
    async def list_active_for(self, assignee: UserRef) -> list[Issue]:
        raise RuntimeError("tracker down")


async def test_a_tracker_that_cannot_be_read_leaves_no_blockers_to_the_model() -> None:
    person = "U-elena"
    store = await _store(person, roles="dev")
    llm = SequenceLlmProvider(texts=[_QUIET_DAY_STATUS])
    collector = _collector(store, FakeChatProvider(), llm, _UnreadableTracker())

    await collector.handle_reply(_message(person, _ELENA_V2))

    assert len(llm.requests) == 1
