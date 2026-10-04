"""A tracker update OpenProgram applies in a check-in is marked in its summary (N48).

R5: Omar said CHK-17's code was merged but the ticket not closed, answered yes
to closing it, and OpenProgram closed it in Jira in the same check-in. His
stored summary still read "ticket not closed", so Ask and the digests
contradicted Jira. Each summary sentence naming an issue the check-in moved now
says what OpenProgram did, the way a cleared blocker's does (N34). The ids here
are made up.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from core.application.status_collector import StatusCollector
from core.application.status_summaries import TrackerUpdate, summary_with_tracker_updates
from core.application.writeback_service import WriteBackService
from core.domain.graph import Developer
from core.domain.identity import IdentityLink
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import (
    CheckIn,
    CheckInCorrelation,
    CheckInPreference,
    StatusSource,
    WriteBackConsent,
)
from core.domain.writeback import WriteBackStatus
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

_TENANT = "demo"
_OMAR = "U-omar"
_CORRELATION = "corr-omar-r5"
_DAY = date(2026, 10, 4)
_ASKED_AT = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
_SUMMARY = "CHK-17 code is merged but ticket not closed. Next up is the session audit log."
_EVALUATION = json.dumps(
    {
        "is_status_update": True,
        "sufficient": True,
        "question": None,
        "signals": {
            "progress_note": _SUMMARY,
            "blockers": [],
            "eta_change_days": None,
            "blockers_answered": True,
            "eta_answered": True,
            "issue_updates": [
                {
                    "issue_key": "CHK-17",
                    "claimed_done": True,
                    "claimed_state": "merged",
                    "note": "Code is merged, the ticket is still open.",
                }
            ],
        },
    }
)


async def _omar(
    consent: WriteBackConsent, *, tracker_name: str | None = None
) -> tuple[StatusCollector, FakeIssueTracker, InMemoryGraphStore]:
    """Omar with CHK-17 his and In Progress, and a check-in asked on Sunday."""
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id=_TENANT, id=_OMAR, name="Omar Haddad"))
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_OMAR,
            correlation_id=_CORRELATION,
            asked_at=_ASKED_AT,
            replied_at=None,
            raw_reply=None,
            signals=None,
            checkin_date=_DAY,
        )
    )
    await store.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id=_TENANT,
            developer_id=_OMAR,
            correlation_id=_CORRELATION,
            chat_user_ref=_OMAR,
            chat_thread_ref="dm-omar",
            outbound_message_id="msg-question-omar",
            asked_at=_ASKED_AT,
        )
    )
    await store.record_checkin_preference(
        CheckInPreference(tenant_id=_TENANT, developer_id=_OMAR, write_back_consent=consent)
    )
    await store.upsert_identity_link(
        IdentityLink(tenant_id=_TENANT, developer_id=_OMAR, jira_account_id="acct-omar")
    )
    await store.set_writeback_enabled(_TENANT, True)
    tracker = FakeIssueTracker(
        issues={
            "CHK-17": Issue(
                tenant_id=_TENANT,
                key="CHK-17",
                title="Session cleanup",
                state=IssueState.IN_PROGRESS,
                assignee=UserRef(tenant_id=_TENANT, external_id="acct-omar"),
            )
        }
    )
    named = {"issue_tracker_name": tracker_name} if tracker_name is not None else {}
    collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[_EVALUATION]),
        status_repository=store,
        conversation_repository=store,
        identity_link_repository=store,
        write_back_service=WriteBackService(
            issue_tracker=tracker,
            audit_repository=store,
            config_repository=store,
            status_repository=store,
            identity_link_repository=store,
            time_series_repository=store,
            graph_repository=store,
        ),
        model="test-model",
        **named,
    )
    return collector, tracker, store


def _omar_says(text: str, message_id: str, at: datetime) -> InboundMessage:
    return InboundMessage(
        tenant_id=_TENANT,
        user=ChatUserRef(tenant_id=_TENANT, external_id=_OMAR),
        text=text,
        thread_id="dm-omar",
        message_id=message_id,
        correlation_id=_CORRELATION,
        received_at=at,
    )


_STATUS = _omar_says(
    "CHK-17 code is merged, ticket not closed yet",
    "msg-omar-status",
    datetime(2026, 10, 4, 12, 8, 39, tzinfo=UTC),
)
_YES = _omar_says("yes", "msg-omar-yes", datetime(2026, 10, 4, 12, 9, 12, tzinfo=UTC))


async def test_omars_yes_closes_chk_17_and_his_summary_says_so() -> None:
    collector, tracker, store = await _omar(WriteBackConsent.ALWAYS_ASK, tracker_name="Jira")

    asked = await collector.handle_reply(_STATUS)

    # Nothing is written until he answers, and the summary is his own words.
    assert asked.kind == "clarifying"
    assert tracker.transitions == []
    waiting = await store.latest_developer_status(_TENANT, _OMAR, _DAY)
    assert waiting is not None and waiting.summary == _SUMMARY

    answered = await collector.handle_reply(_YES)

    assert answered.kind == "processed"
    assert tracker.transitions == [(_TENANT, "CHK-17", "done")]
    rows = await store.list_writeback_by_correlation(_TENANT, _CORRELATION)
    latest = max(rows, key=lambda row: row.created_at)
    assert (latest.status, latest.source) == (WriteBackStatus.APPLIED, "consent_reply")
    expected = (
        "CHK-17 code is merged but ticket not closed (closed in Jira by OpenProgram after "
        "confirmation). Next up is the session audit log."
    )
    stored = await store.latest_developer_status(_TENANT, _OMAR, _DAY)
    assert stored is not None
    assert stored.summary == expected
    assert stored.source is StatusSource.CONFIRMED
    assert answered.status is not None and answered.status.summary == expected


async def test_an_update_made_without_asking_is_marked_without_a_confirmation() -> None:
    collector, tracker, store = await _omar(WriteBackConsent.AUTO_APPLY)

    outcome = await collector.handle_reply(_STATUS)

    assert outcome.kind == "processed"
    assert tracker.transitions == [(_TENANT, "CHK-17", "done")]
    stored = await store.latest_developer_status(_TENANT, _OMAR, _DAY)
    assert stored is not None
    assert stored.summary == (
        "CHK-17 code is merged but ticket not closed (closed in the issue tracker by "
        "OpenProgram). Next up is the session audit log."
    )


async def test_a_yes_that_reaches_a_recorded_check_in_marks_its_status() -> None:
    # The question still open on a check-in already recorded: the yes reaches
    # it as a late consent answer, and that check-in's status is marked too.
    collector, tracker, store = await _omar(WriteBackConsent.ALWAYS_ASK, tracker_name="Jira")
    await collector.handle_reply(_STATUS)
    held = await store.checkin_by_correlation(_TENANT, _CORRELATION)
    assert held is not None and held.signals is not None
    await store.record_checkin_reply_once(
        replace(held, replied_at=_STATUS.received_at, raw_reply=_STATUS.text)
    )

    outcome = await collector.handle_reply(_YES)

    assert outcome.kind == "acknowledged"
    assert tracker.transitions == [(_TENANT, "CHK-17", "done")]
    stored = await store.latest_developer_status(_TENANT, _OMAR, _DAY)
    assert stored is not None
    assert stored.summary == (
        "CHK-17 code is merged but ticket not closed (closed in Jira by OpenProgram after "
        "confirmation). Next up is the session audit log."
    )


async def test_a_late_update_that_closes_the_issue_is_marked_too() -> None:
    collector, tracker, store = await _omar(WriteBackConsent.AUTO_APPLY)
    # The check-in closed unanswered before the reply came (G9).
    await store.consume_checkin_correlation(
        _TENANT, _CORRELATION, datetime(2026, 10, 4, 12, 5, tzinfo=UTC)
    )

    await collector.handle_reply(_STATUS)

    assert tracker.transitions == [(_TENANT, "CHK-17", "done")]
    stored = await store.latest_developer_status(_TENANT, _OMAR, _DAY)
    assert stored is not None
    assert stored.summary.startswith("Late update 12:08 UTC: ")
    assert stored.summary.endswith(
        "CHK-17 code is merged but ticket not closed (closed in the issue tracker by "
        "OpenProgram). Next up is the session audit log."
    )


async def test_a_no_leaves_the_summary_as_said() -> None:
    collector, tracker, store = await _omar(WriteBackConsent.ALWAYS_ASK, tracker_name="Jira")
    await collector.handle_reply(_STATUS)

    await collector.handle_reply(
        _omar_says("no", "msg-omar-no", datetime(2026, 10, 4, 12, 9, 12, tzinfo=UTC))
    )

    assert tracker.transitions == []
    stored = await store.latest_developer_status(_TENANT, _OMAR, _DAY)
    assert stored is not None and stored.summary == _SUMMARY


_CLOSED = TrackerUpdate(issue_key="CHK-17", change="closed", tracker="Jira", confirmed=True)
_STARTED = TrackerUpdate(
    issue_key="CHK-12", change="moved to In Progress", tracker="Jira", confirmed=False
)


@pytest.mark.parametrize(
    ("summary", "updates", "expected"),
    [
        # The R5 sentence, without its full stop too.
        (
            "CHK-17 code is merged but ticket not closed",
            [_CLOSED],
            "CHK-17 code is merged but ticket not closed (closed in Jira by OpenProgram "
            "after confirmation)",
        ),
        # A sentence that names other issues as well names the one updated.
        (
            "CHK-12 started; CHK-17 merged but not closed. No blockers.",
            [_CLOSED],
            "CHK-12 started; CHK-17 merged but not closed (CHK-17 closed in Jira by "
            "OpenProgram after confirmation). No blockers.",
        ),
        (
            "CHK-12 started; CHK-17 merged but not closed.",
            [_STARTED, _CLOSED],
            "CHK-12 started; CHK-17 merged but not closed (CHK-12 moved to In Progress in "
            "Jira by OpenProgram) (CHK-17 closed in Jira by OpenProgram after confirmation).",
        ),
        # Each sentence naming it is marked; a sentence naming none keeps its words.
        (
            "Merged CHK-17 on checkout-api !3. Pairing on the audit log. CHK-17 needs closing.",
            [_CLOSED],
            "Merged CHK-17 on checkout-api !3 (closed in Jira by OpenProgram after "
            "confirmation). Pairing on the audit log. CHK-17 needs closing (closed in Jira by "
            "OpenProgram after confirmation).",
        ),
        # An update for an issue the summary does not name changes nothing.
        ("Pairing on the audit log.", [_CLOSED], "Pairing on the audit log."),
        ("CHK-17 merged.", [], "CHK-17 merged."),
    ],
)
def test_summary_sentences_naming_an_updated_issue_are_marked(
    summary: str, updates: list[TrackerUpdate], expected: str
) -> None:
    assert summary_with_tracker_updates(summary, updates) == expected


def test_a_mark_is_added_once() -> None:
    once = summary_with_tracker_updates("CHK-17 merged, ticket not closed.", [_CLOSED])

    assert summary_with_tracker_updates(once, [_CLOSED]) == once
