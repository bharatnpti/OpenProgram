from __future__ import annotations

from dataclasses import dataclass, field

from core.application.status_collector import StatusCollector
from core.domain.identity import IdentityLink
from core.domain.integrations import Issue, IssueState, UserRef
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker, FakeLlmProvider


@dataclass
class RecordingIssueTracker(FakeIssueTracker):
    """Fake issue tracker that records the assignee build_context queries by."""

    queried_assignees: list[UserRef] = field(default_factory=list)

    async def list_active_for(self, assignee: UserRef) -> list[Issue]:
        self.queried_assignees.append(assignee)
        return await super().list_active_for(assignee)


def _collector(tracker: FakeIssueTracker, store: InMemoryGraphStore) -> StatusCollector:
    return StatusCollector(
        issue_tracker=tracker,
        chat_provider=FakeChatProvider(),
        llm_provider=FakeLlmProvider(),
        status_repository=store,
        conversation_repository=store,
        time_series_repository=store,
        identity_link_repository=store,
        model="test-model",
    )


def _issue(assignee: UserRef) -> Issue:
    return Issue(
        tenant_id="demo",
        key="PO-1",
        title="Build graph sync",
        state=IssueState.IN_PROGRESS,
        assignee=assignee,
    )


async def test_build_context_queries_jira_by_mapped_account_id() -> None:
    tracker = RecordingIssueTracker(
        issues={"PO-1": _issue(UserRef(tenant_id="demo", external_id="acct-1"))}
    )
    store = InMemoryGraphStore()
    await store.upsert_identity_link(
        IdentityLink(tenant_id="demo", developer_id="dev-1", jira_account_id="acct-1")
    )
    collector = _collector(tracker, store)

    context = await collector.build_context(
        tenant_id="demo", developer_id="dev-1", include_status=False
    )

    # Jira indexes by accountId, so the mapped id -- not the chat/dev id -- is used.
    assert tracker.queried_assignees == [UserRef(tenant_id="demo", external_id="acct-1")]
    assert "PO-1" in context


async def test_build_context_queries_jira_by_developer_id_when_unmapped() -> None:
    tracker = RecordingIssueTracker(
        issues={"PO-1": _issue(UserRef(tenant_id="demo", external_id="dev-1"))}
    )
    store = InMemoryGraphStore()
    collector = _collector(tracker, store)

    context = await collector.build_context(
        tenant_id="demo", developer_id="dev-1", include_status=False
    )

    # No identity link -> fall back to the canonical developer id (prior behaviour).
    assert tracker.queried_assignees == [UserRef(tenant_id="demo", external_id="dev-1")]
    assert "PO-1" in context


async def test_build_context_falls_back_when_link_has_no_jira_account_id() -> None:
    tracker = RecordingIssueTracker(
        issues={"PO-1": _issue(UserRef(tenant_id="demo", external_id="dev-1"))}
    )
    store = InMemoryGraphStore()
    await store.upsert_identity_link(
        IdentityLink(tenant_id="demo", developer_id="dev-1", chat_user_id="U123")
    )
    collector = _collector(tracker, store)

    context = await collector.build_context(
        tenant_id="demo", developer_id="dev-1", include_status=False
    )

    # A link without a jira_account_id must not change the queried assignee.
    assert tracker.queried_assignees == [UserRef(tenant_id="demo", external_id="dev-1")]
    assert "PO-1" in context
