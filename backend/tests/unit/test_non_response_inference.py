"""A silent person's status is inferred only from work that is under way (N4).

R1 live: Hana did not answer, and the close-out recorded her as ``inferred``
"from 1 active issue: IDP-9" although IDP-9 was To Do in Jira.
"""

from __future__ import annotations

from datetime import date

from core.application.status_collector import StatusCollector
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

_HANA = UserRef(tenant_id="demo", external_id="dev-hana")


def _issue(key: str, title: str, state: IssueState) -> Issue:
    return Issue(tenant_id="demo", key=key, title=title, state=state, assignee=_HANA)


def _collector(store: InMemoryGraphStore, *issues: Issue) -> StatusCollector:
    return StatusCollector(
        issue_tracker=FakeIssueTracker(issues={issue.key: issue for issue in issues}),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[]),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        model="test-model",
    )


async def test_only_to_do_issue_is_no_active_work_to_infer_from() -> None:
    store = InMemoryGraphStore()
    collector = _collector(store, _issue("IDP-9", "Usage dashboard", IssueState.TODO))

    status = await collector.record_non_response(
        tenant_id="demo", developer_id="dev-hana", as_of=date(2026, 10, 3)
    )

    assert status.source is StatusSource.UNKNOWN
    assert status.summary == (
        "No confirmed check-in after a nudge. Current status is unknown. "
        "No active work to infer from."
    )
    assert "IDP-9" not in status.summary
    assert status.blockers == ("no confirmed reply",)


async def test_inference_names_only_the_issues_under_way() -> None:
    store = InMemoryGraphStore()
    collector = _collector(
        store,
        _issue("IDP-9", "Usage dashboard", IssueState.TODO),
        _issue("IDP-6", "Metadata refresh", IssueState.IN_PROGRESS),
        _issue("IDP-8", "Key rotation", IssueState.BLOCKED),
    )

    status = await collector.record_non_response(
        tenant_id="demo", developer_id="dev-hana", as_of=date(2026, 10, 3)
    )

    assert status.source is StatusSource.INFERRED
    assert status.summary == (
        "No confirmed check-in after a nudge. Inferred from 2 active issues: "
        "IDP-8 Key rotation (blocked) and IDP-6 Metadata refresh."
    )
    assert "IDP-9" not in status.summary


async def test_stale_status_says_there_is_no_active_work_to_infer_from() -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-hana",
            as_of=date(2026, 10, 3),
            source=StatusSource.PARTIAL,
            blockers=(),
            summary="Started the dashboard spike.",
        )
    )
    collector = _collector(store, _issue("IDP-9", "Usage dashboard", IssueState.TODO))

    first = await collector.record_non_response(
        tenant_id="demo", developer_id="dev-hana", as_of=date(2026, 10, 4)
    )
    # The next silent day reuses the stale summary; the note is not repeated.
    second = await collector.record_non_response(
        tenant_id="demo", developer_id="dev-hana", as_of=date(2026, 10, 5)
    )

    expected = (
        "No confirmed check-in after a nudge. Last known partial status on Oct 3: "
        "Started the dashboard spike. No active work to infer from."
    )
    assert first.source is StatusSource.STALE
    assert first.summary == expected
    assert second.summary == expected


async def test_stale_status_drops_the_note_once_the_tracker_cannot_tell() -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-hana",
            as_of=date(2026, 10, 4),
            source=StatusSource.STALE,
            blockers=("no confirmed reply",),
            summary=(
                "No confirmed check-in after a nudge. Last known partial status on Oct 3: "
                "Started the dashboard spike. No active work to infer from."
            ),
        )
    )
    # An empty tracker result may be an unmapped assignee: it is not "no active work".
    collector = _collector(store)

    status = await collector.record_non_response(
        tenant_id="demo", developer_id="dev-hana", as_of=date(2026, 10, 5)
    )

    assert status.source is StatusSource.STALE
    assert status.summary == (
        "No confirmed check-in after a nudge. Last known partial status on Oct 3: "
        "Started the dashboard spike."
    )
