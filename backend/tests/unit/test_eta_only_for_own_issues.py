"""An ETA is asked only for the person's own issues (N33).

R4 live: Asha was asked the ETA "to finish CHK-16 ... and CHK-17", but CHK-17
is Omar's (she was only merging it). Ira, a scrum master, was asked the ETA to
finish CHK-4 (Liam's) and IDP-3 (Noah's).
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.application.status_collector import StatusCollector
from core.domain.graph import Developer, EdgeKind, GraphEdge, Task
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.status import StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_answered_follow_ups import _evaluation, _message, _open_checkin
from tests.unit.test_status_collector import SequenceLlmProvider

_TENANT = "demo"
_ASHA = "U-asha"
_IRA = "U-ira"
_OWNERS = {
    "CHK-16": ("Q4 checkout roadmap review", _ASHA),
    "CHK-17": ("Upgrade shared HTTP client", "U-omar"),
    "CHK-4": ("Step-up flow", "U-liam"),
    "IDP-3": ("Passkey enrolment", "U-noah"),
}


async def _store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for person in (_ASHA, _IRA, "U-omar", "U-liam", "U-noah"):
        await store.upsert_node(Developer(tenant_id=_TENANT, id=person, name=person))
    for key, (title, owner) in _OWNERS.items():
        await store.upsert_node(
            Task(tenant_id=_TENANT, id=key, name=title, metadata={"key": key, "state": "todo"})
        )
        await store.add_edge(
            GraphEdge(
                tenant_id=_TENANT, from_node_id=owner, to_node_id=key, kind=EdgeKind.ASSIGNED_TO
            )
        )
    for person in (_ASHA, _IRA):
        await _open_checkin(store, person)
    return store


def _tracker() -> FakeIssueTracker:
    return FakeIssueTracker(
        issues={
            key: Issue(
                tenant_id=_TENANT,
                key=key,
                title=title,
                state=IssueState.TODO if key == "CHK-16" else IssueState.IN_PROGRESS,
                assignee=UserRef(tenant_id=_TENANT, external_id=owner),
                updated_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
            )
            for key, (title, owner) in _OWNERS.items()
        }
    )


def _collector(
    store: InMemoryGraphStore, chat: FakeChatProvider, texts: list[str]
) -> StatusCollector:
    return StatusCollector(
        issue_tracker=_tracker(),
        chat_provider=chat,
        llm_provider=SequenceLlmProvider(texts=texts),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
        checkin_ack_enabled=False,
    )


async def test_asha_is_asked_the_eta_of_her_issue_not_omars() -> None:
    store = await _store()
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            _evaluation(
                sufficient=True,
                question=None,
                claims=[
                    {"issue_key": "CHK-16", "claimed_state": "on track", "note": "Drafted."},
                    {"issue_key": "CHK-17", "claimed_state": "merging", "note": "Omar's MR."},
                ],
                blockers_answered=True,
                eta_answered=False,
                note="CHK-16 on track; merging platform-libs !1 (CHK-17).",
            )
        ],
    )

    outcome = await collector.handle_reply(
        _message(_ASHA, "CHK-16 on track, no blockers. Merging platform-libs !1 (CHK-17).", "m1", 4)
    )

    assert outcome.kind == "clarifying"
    assert [message.text for message in chat.sent] == [
        "Thanks. What is your ETA to finish CHK-16 (Q4 checkout roadmap review)?"
    ]


async def test_ira_is_not_asked_etas_for_her_teams_issues() -> None:
    store = await _store()
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            _evaluation(
                sufficient=True,
                question=None,
                claims=[
                    {
                        "issue_key": "CHK-4",
                        "claimed_state": "planning",
                        "note": "Design ramping up.",
                    },
                    {"issue_key": "IDP-3", "claimed_state": "in review", "note": "Comments in."},
                ],
                blockers_answered=True,
                eta_answered=False,
                note="Payments and Identity both on track, no blockers.",
            )
        ],
    )

    outcome = await collector.handle_reply(
        _message(_IRA, "Payments and Identity on track, no blockers from my end.", "m1", 4)
    )

    assert chat.sent == []
    assert outcome.kind == "processed"
    assert outcome.status is not None
    assert outcome.status.source is StatusSource.CONFIRMED
    assert "ETA was not provided" not in outcome.status.summary


async def test_a_model_eta_question_about_others_issues_is_not_asked() -> None:
    store = await _store()
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            _evaluation(
                sufficient=False,
                question="What is your ETA to finish CHK-4 and IDP-3?",
                claims=[
                    {"issue_key": "CHK-4", "claimed_state": "planning", "note": ""},
                    {"issue_key": "IDP-3", "claimed_state": "in review", "note": ""},
                ],
                blockers_answered=True,
                eta_answered=False,
                note="Both pods on track.",
            )
        ],
    )

    outcome = await collector.handle_reply(_message(_IRA, "Both pods on track.", "m1", 4))

    assert chat.sent == []
    assert outcome.kind == "processed"


async def test_blockers_are_still_asked_about_the_teams_issues() -> None:
    store = await _store()
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            _evaluation(
                sufficient=True,
                question=None,
                claims=[{"issue_key": "CHK-4", "claimed_state": "planning", "note": ""}],
                blockers_answered=False,
                eta_answered=False,
                note="CHK-4 moved to planning.",
            )
        ],
    )

    await collector.handle_reply(_message(_IRA, "CHK-4 moved to planning.", "m1", 4))

    # No ETA is due from her; the blocker question still names the team's issue.
    assert [message.text for message in chat.sent] == ["Thanks. Any blockers on CHK-4?"]
