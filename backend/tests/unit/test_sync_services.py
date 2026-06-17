from __future__ import annotations

from datetime import UTC, date, datetime

from core.application.sync_services import (
    CalendarReadSyncService,
    IssueReadSyncService,
    VcsReadSyncService,
)
from core.domain.graph import EdgeKind, EntityRef, GraphEdge, NodeKind, Pod, Program
from core.domain.integrations import (
    CalendarEvent,
    Commit,
    Issue,
    IssueState,
    PullRequest,
    SyncCursor,
    UserRef,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeCalendarProvider, FakeIssueTracker, FakeVcsProvider


async def test_issue_read_sync_creates_task_edges_facts_and_cursor() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Program(tenant_id="demo", id="program-1", name="Program"))
    await store.upsert_node(Pod(tenant_id="demo", id="pod-1", name="Pod"))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="program-1",
            to_node_id="pod-1",
            kind=EdgeKind.CONTAINS,
        )
    )
    assignee = UserRef(tenant_id="demo", external_id="dev-1", display_name="Asha")
    updated_at = datetime(2026, 1, 10, 8, 30, tzinfo=UTC)
    tracker = FakeIssueTracker(
        issues={
            "PO-1": Issue(
                tenant_id="demo",
                key="PO-1",
                title="Build status collector",
                state=IssueState.IN_PROGRESS,
                assignee=assignee,
                updated_at=updated_at,
            )
        }
    )
    service = IssueReadSyncService(
        issue_tracker=tracker,
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
    )

    result = await service.sync_project(
        tenant_id="demo",
        project_key="PO",
        container_id="pod-1",
        observed_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
    )

    tree = await store.get_program_tree("demo", "program-1", date(2026, 1, 10))
    facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="PO-1"),
    )
    memberships = await store.active_developer_memberships("demo", "dev-1", date(2026, 1, 10))
    cursor = await store.get_cursor("demo", "issue", "project:PO")

    assert result.items_synced == 1
    assert any(node.id == "PO-1" and node.kind is NodeKind.TASK for node in tree.nodes)
    assert any(
        edge.kind is EdgeKind.ASSIGNED_TO and edge.to_node_id == "PO-1" for edge in memberships
    )
    assert facts[0].payload["state"] == IssueState.IN_PROGRESS.value
    assert facts[0].correlation_id == "issue:demo:PO-1:2026-01-10T08:30:00+00:00"
    assert cursor.updated_at == updated_at
    assert cursor.metadata["last_item_count"] == 1

    await store.record_cursor("demo", "issue", "project:PO", SyncCursor())
    await service.sync_project(
        tenant_id="demo",
        project_key="PO",
        container_id="pod-1",
        observed_at=datetime(2026, 1, 10, 9, 30, tzinfo=UTC),
    )
    assert (
        len(
            await store.list_facts(
                "demo",
                EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="PO-1"),
            )
        )
        == 1
    )


async def test_vcs_read_sync_appends_commit_and_pull_request_facts_and_cursor() -> None:
    store = InMemoryGraphStore()
    author = UserRef(tenant_id="demo", external_id="dev-1")
    commit_time = datetime(2026, 1, 10, 8, 0, tzinfo=UTC)
    pull_request_time = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    provider = FakeVcsProvider(
        commits=[
            Commit(
                tenant_id="demo",
                repo="repo-1",
                sha="abc123",
                message="Add collector",
                author=author,
                committed_at=commit_time,
            )
        ],
        pull_requests=[
            PullRequest(
                tenant_id="demo",
                id="7",
                title="Collector service",
                author=author,
                merged=False,
                metadata={"repo": "repo-1"},
                updated_at=pull_request_time,
            )
        ],
    )
    service = VcsReadSyncService(
        vcs_provider=provider,
        time_series_repository=store,
        cursor_repository=store,
    )

    result = await service.sync_repo(
        tenant_id="demo",
        repo_name="repo-1",
        observed_at=datetime(2026, 1, 10, 10, 0, tzinfo=UTC),
    )

    facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
    )
    cursor = await store.get_cursor("demo", "vcs", "repo:repo-1")

    assert result.items_synced == 2
    assert {fact.source for fact in facts} == {"vcs_commit", "vcs_pull_request"}
    assert {fact.correlation_id for fact in facts} == {
        "vcs:commit:demo:repo-1:abc123",
        "vcs:pull_request:demo:repo-1:7:2026-01-10T09:00:00+00:00",
    }
    assert cursor.updated_at == pull_request_time
    assert cursor.metadata["last_item_count"] == 2

    await store.record_cursor("demo", "vcs", "repo:repo-1", SyncCursor())
    await service.sync_repo(
        tenant_id="demo",
        repo_name="repo-1",
        observed_at=datetime(2026, 1, 10, 10, 30, tzinfo=UTC),
    )
    assert (
        len(
            await store.list_facts(
                "demo",
                EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
            )
        )
        == 2
    )


async def test_calendar_read_sync_appends_event_facts_and_cursor() -> None:
    store = InMemoryGraphStore()
    user = UserRef(tenant_id="demo", external_id="dev-1")
    observed_at = datetime(2026, 1, 10, 10, 0, tzinfo=UTC)
    provider = FakeCalendarProvider(
        events=[
            CalendarEvent(
                tenant_id="demo",
                user=user,
                starts_on=date(2026, 1, 10),
                ends_on=date(2026, 1, 11),
                kind="pto",
                metadata={"timezone": "Europe/Berlin"},
            )
        ]
    )
    service = CalendarReadSyncService(
        calendar_provider=provider,
        time_series_repository=store,
        cursor_repository=store,
    )

    result = await service.sync_user(
        user=user,
        start=date(2026, 1, 10),
        end=date(2026, 1, 11),
        observed_at=observed_at,
    )

    facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
    )
    cursor = await store.get_cursor("demo", "calendar", "user:dev-1")

    assert result.items_synced == 1
    assert facts[0].source == "calendar"
    assert facts[0].payload["timezone"] == "Europe/Berlin"
    assert facts[0].correlation_id == "calendar:demo:dev-1:2026-01-10:2026-01-11:pto"
    assert cursor.value == "2026-01-11"
    assert cursor.metadata["last_item_count"] == 1

    await store.record_cursor("demo", "calendar", "user:dev-1", SyncCursor())
    await service.sync_user(
        user=user,
        start=date(2026, 1, 10),
        end=date(2026, 1, 11),
        observed_at=observed_at,
    )
    assert (
        len(
            await store.list_facts(
                "demo",
                EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
            )
        )
        == 1
    )
