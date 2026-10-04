"""Every follow-up question names the issue it is about (R1-9).

R1 live: Raj, Sofia and Zoe each got "Thanks. What is your ETA to finish it?"
while several of their issues were in play, so none of them knew which one.
"""

from __future__ import annotations

import json

from core.application.status_collector import StatusCollector
from core.domain.integrations import Issue, IssueState, UserRef
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import (
    SequenceLlmProvider,
    _record_open_checkin,
    _reply_message,
)

_DEV = UserRef(tenant_id="demo", external_id="dev-1")


def _issue(key: str, title: str, state: IssueState) -> Issue:
    return Issue(tenant_id="demo", key=key, title=title, state=state, assignee=_DEV)


def _evaluation(
    claims: list[dict[str, object]],
    *,
    sufficient: bool = True,
    question: str | None = None,
    eta_answered: bool = False,
) -> str:
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": sufficient,
            "question": question,
            "signals": {
                "progress_note": "Status from the reply.",
                "blockers": [],
                "eta_change_days": None,
                "blockers_answered": True,
                "eta_answered": eta_answered,
                "issue_updates": claims,
            },
        }
    )


def _claim(key: str, state: str, *, done: bool = False) -> dict[str, object]:
    return {"issue_key": key, "claimed_done": done, "claimed_state": state, "note": ""}


async def _first_follow_up(tracker: FakeIssueTracker, evaluation: str) -> str:
    store = InMemoryGraphStore()
    await _record_open_checkin(store)
    chat = FakeChatProvider()
    collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=chat,
        llm_provider=SequenceLlmProvider(texts=[evaluation]),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )

    outcome = await collector.handle_reply(_reply_message("the reply"))

    assert outcome.kind == "clarifying"
    assert len(chat.sent) == 1
    return chat.sent[0].text


async def test_eta_follow_up_names_the_issue_under_way_not_it() -> None:
    # R1 Sofia: CHK-14 on track (MR open), CHK-15 and IDP-7 not started, no ETA.
    tracker = FakeIssueTracker(
        issues={
            "CHK-14": _issue("CHK-14", "Test plan for guest checkout", IssueState.IN_PROGRESS),
            "CHK-15": _issue("CHK-15", "Guest checkout banner", IssueState.TODO),
            "IDP-7": _issue("IDP-7", "Session audit log", IssueState.TODO),
        }
    )

    question = await _first_follow_up(
        tracker,
        _evaluation(
            [
                _claim("CHK-14", "on track"),
                _claim("CHK-15", "not started"),
                _claim("IDP-7", "not started"),
            ]
        ),
    )

    assert question == "Thanks. What is your ETA to finish CHK-14 (Test plan for guest checkout)?"


async def test_eta_follow_up_names_two_issues_with_titles() -> None:
    tracker = FakeIssueTracker(
        issues={
            "INS-2": _issue("INS-2", "Backfill job", IssueState.IN_PROGRESS),
            "INS-4": _issue("INS-4", "Retention policy for raw events", IssueState.TODO),
        }
    )

    question = await _first_follow_up(
        tracker,
        _evaluation([_claim("INS-2", "in review"), _claim("INS-4", "in progress")]),
    )

    assert question == (
        "Thanks. What is your ETA to finish INS-2 (Backfill job) and "
        "INS-4 (Retention policy for raw events)?"
    )


async def test_follow_up_without_claims_names_the_tracker_issue_under_way() -> None:
    tracker = FakeIssueTracker(
        issues={
            "CHK-8": _issue("CHK-8", "Refund webhook retries", IssueState.IN_PROGRESS),
            "CHK-9": _issue("CHK-9", "Saved cards", IssueState.TODO),
        }
    )

    question = await _first_follow_up(tracker, _evaluation([]))

    assert question == "Thanks. What is your ETA to finish CHK-8 (Refund webhook retries)?"


async def test_model_follow_up_that_says_it_gets_the_issue_key() -> None:
    # R1 Raj: INS-2 in review on insights-pipeline !1; the question said "it".
    tracker = FakeIssueTracker(
        issues={"INS-2": _issue("INS-2", "Backfill job", IssueState.IN_PROGRESS)}
    )

    question = await _first_follow_up(
        tracker,
        _evaluation(
            [_claim("INS-2", "in review")],
            sufficient=False,
            question="Thanks. What is your ETA to finish it?",
            eta_answered=True,
        ),
    )

    assert question == "Thanks. About INS-2 (Backfill job): What is your ETA to finish it?"


async def test_model_follow_up_that_names_a_key_is_left_as_drafted() -> None:
    tracker = FakeIssueTracker(
        issues={"INS-2": _issue("INS-2", "Backfill job", IssueState.IN_PROGRESS)}
    )

    question = await _first_follow_up(
        tracker,
        _evaluation(
            [_claim("INS-2", "in review")],
            sufficient=False,
            question="Jira shows INS-2 In Progress. Is the review requested yet?",
            eta_answered=True,
        ),
    )

    assert question == "Jira shows INS-2 In Progress. Is the review requested yet?"


async def test_follow_up_names_keys_only_when_the_tracker_cannot_be_read() -> None:
    class DownTracker(FakeIssueTracker):
        async def list_active_for(self, assignee: UserRef) -> list[Issue]:
            raise RuntimeError("tracker down")

    question = await _first_follow_up(
        DownTracker(), _evaluation([_claim("IDP-6", "in progress"), _claim("IDP-3", "started")])
    )

    assert question == "Thanks. What is your ETA to finish IDP-6 and IDP-3?"
