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
    Project,
    PullRequest,
    Repo,
    Sprint,
    SyncCursor,
    UserRef,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeIssueTracker, FakeVcsProvider


class RaisingCalendarProvider:
    def __init__(self) -> None:
        self.called = False

    async def list_events(
        self,
        user: UserRef,
        start: date,
        end: date,
    ) -> list[CalendarEvent]:
        self.called = True
        raise AssertionError("calendar read-sync should not fetch events")


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
        projects=[
            Project(tenant_id="demo", id="10000", key="PO", name="PulseOps"),
        ],
        sprints=[
            Sprint(
                tenant_id="demo",
                id="sprint-1",
                board_id="board-1",
                name="Sprint 1",
                state="active",
            )
        ],
        issues={
            "PO-1": Issue(
                tenant_id="demo",
                key="PO-1",
                title="Build status collector",
                state=IssueState.IN_PROGRESS,
                assignee=assignee,
                metadata={"sprint_id": "sprint-1"},
                updated_at=updated_at,
            )
        },
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
        board_id="board-1",
        observed_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
    )

    tree = await store.get_program_tree("demo", "program-1", date(2026, 1, 10))
    tree_edges = {(edge.from_node_id, edge.to_node_id, edge.kind) for edge in tree.edges}
    facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="PO-1"),
    )
    memberships = await store.active_developer_memberships("demo", "dev-1", date(2026, 1, 10))
    cursor = await store.get_cursor("demo", "issue", "project:PO")

    assert result.items_synced == 1
    assert any(node.id == "PO" and node.kind is NodeKind.PROJECT for node in tree.nodes)
    assert any(node.id == "sprint-1" and node.kind is NodeKind.SPRINT for node in tree.nodes)
    assert any(node.id == "PO-1" and node.kind is NodeKind.TASK for node in tree.nodes)
    assert ("pod-1", "PO", EdgeKind.CONTAINS) in tree_edges
    assert ("PO", "sprint-1", EdgeKind.CONTAINS) in tree_edges
    assert ("sprint-1", "PO-1", EdgeKind.CONTAINS) in tree_edges
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
        board_id="board-1",
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
        repos=[
            Repo(tenant_id="demo", id="repo-external-1", name="repo-1", default_branch="main"),
        ],
        commits=[
            Commit(
                tenant_id="demo",
                repo="repo-1",
                sha="abc123",
                message="Add collector",
                author=author,
                committed_at=commit_time,
            ),
            Commit(
                tenant_id="demo",
                repo="repo-1",
                sha="def456",
                message="Automated merge",
                author=None,
                committed_at=datetime(2026, 1, 10, 8, 15, tzinfo=UTC),
            ),
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
        graph_repository=store,
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
    repo_facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.REPO, id="repo-1"),
    )
    repo_tree = await store.get_program_tree("demo", "repo-1", date(2026, 1, 10))
    developer_tree = await store.get_program_tree("demo", "dev-1", date(2026, 1, 10))
    cursor = await store.get_cursor("demo", "vcs", "repo:repo-1")

    assert result.items_synced == 3
    assert repo_tree.root.kind is NodeKind.REPO
    assert developer_tree.root.kind is NodeKind.DEVELOPER
    assert {fact.source for fact in facts} == {"vcs_commit", "vcs_pull_request"}
    assert {fact.source for fact in repo_facts} == {"vcs_commit"}
    assert {fact.correlation_id for fact in facts} == {
        "vcs:commit:demo:repo-1:abc123",
        "vcs:pull_request:demo:repo-1:7:2026-01-10T09:00:00+00:00",
    }
    assert repo_facts[0].correlation_id == "vcs:commit:demo:repo-1:def456"
    assert cursor.updated_at == pull_request_time
    assert cursor.metadata["last_item_count"] == 3

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
    assert (
        len(
            await store.list_facts(
                "demo",
                EntityRef(tenant_id="demo", kind=NodeKind.REPO, id="repo-1"),
            )
        )
        == 1
    )


async def test_calendar_read_sync_noops_without_provider_facts_or_cursor() -> None:
    store = InMemoryGraphStore()
    user = UserRef(tenant_id="demo", external_id="dev-1")
    observed_at = datetime(2026, 1, 10, 10, 0, tzinfo=UTC)
    provider = RaisingCalendarProvider()
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

    assert provider.called is False
    assert result.connector == "calendar"
    assert result.scope == "user:dev-1"
    assert result.items_synced == 0
    assert result.cursor == SyncCursor()
    assert facts == []
    assert cursor == SyncCursor()


async def test_issue_sync_uses_fallback_project_and_sprint_name_matching() -> None:
    store = InMemoryGraphStore()
    observed_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    tracker = FakeIssueTracker(
        sprints=[
            Sprint(
                tenant_id="demo",
                id="sprint-current",
                board_id="board-1",
                name="Current Sprint",
                state="active",
                starts_at=datetime(2026, 1, 6, 9, 0, tzinfo=UTC),
                ends_at=datetime(2026, 1, 17, 18, 0, tzinfo=UTC),
                metadata={"velocity": 21},
            )
        ],
        issues={
            "OPS-1": Issue(
                tenant_id="demo",
                key="OPS-1",
                title="Unassigned sprint task",
                state=IssueState.TODO,
                assignee=None,
                metadata={
                    "project_key": "OPS",
                    "sprint_name": "Current Sprint",
                    "nested": ("ignored",),
                },
                updated_at=None,
            ),
            "OPS-2": Issue(
                tenant_id="demo",
                key="OPS-2",
                title="Fallback parent task",
                state=IssueState.BLOCKED,
                assignee=None,
                metadata={"project_key": "OPS", "sprint": "Missing Sprint"},
                updated_at=datetime(2026, 1, 10, 8, 0, tzinfo=UTC),
            ),
        },
    )
    service = IssueReadSyncService(
        issue_tracker=tracker,
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
    )

    result = await service.sync_project(
        tenant_id="demo",
        project_key="OPS",
        board_id="board-1",
        observed_at=observed_at,
    )

    tree = await store.get_program_tree("demo", "OPS", date(2026, 1, 10))
    edges = {(edge.from_node_id, edge.to_node_id, edge.kind) for edge in tree.edges}
    task_nodes = {node.id: node for node in tree.nodes if node.kind is NodeKind.TASK}
    facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="OPS-1"),
    )
    cursor = await store.get_cursor("demo", "issue", "project:OPS")

    assert result.items_synced == 2
    assert ("OPS", "sprint-current", EdgeKind.CONTAINS) in edges
    assert ("sprint-current", "OPS-1", EdgeKind.CONTAINS) in edges
    assert ("OPS", "OPS-2", EdgeKind.CONTAINS) in edges
    assert "nested" not in task_nodes["OPS-1"].metadata
    assert facts[0].observed_at == observed_at
    assert facts[0].payload["assignee_id"] is None
    assert cursor.updated_at == observed_at


async def test_vcs_sync_matches_repo_id_and_normalizes_fallback_timestamps() -> None:
    store = InMemoryGraphStore()
    observed_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    author = UserRef(tenant_id="demo", external_id="dev-1")
    provider = FakeVcsProvider(
        repos=[
            Repo(
                tenant_id="demo",
                id="repo-external",
                name="oneai/service",
                default_branch="main",
                metadata={"stars": 2, "labels": ("ignored",)},
            )
        ],
        commits=[
            Commit(
                tenant_id="demo",
                repo="repo-external",
                sha="abc123",
                message="Automated merge",
                author=None,
                committed_at=datetime(2026, 1, 10, 8, 0),
            )
        ],
        pull_requests=[
            PullRequest(
                tenant_id="demo",
                id="9",
                title="Open API updates",
                author=author,
                merged=True,
                metadata={"repo": "repo-external"},
                updated_at=None,
            )
        ],
    )
    service = VcsReadSyncService(
        vcs_provider=provider,
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
    )

    result = await service.sync_repo(
        tenant_id="demo",
        repo_name="repo-external",
        observed_at=observed_at,
    )

    repo_tree = await store.get_program_tree("demo", "oneai/service", date(2026, 1, 10))
    repo_facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.REPO, id="oneai/service"),
    )
    developer_facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-1"),
    )
    cursor = await store.get_cursor("demo", "vcs", "repo:repo-external")

    assert result.items_synced == 2
    assert repo_tree.root.metadata["external_id"] == "repo-external"
    assert repo_tree.root.metadata["default_branch"] == "main"
    assert "labels" not in repo_tree.root.metadata
    assert repo_facts[0].observed_at == datetime(2026, 1, 10, 8, 0, tzinfo=UTC)
    assert developer_facts[0].observed_at == observed_at
    assert cursor.updated_at == observed_at


async def test_vcs_sync_creates_placeholder_repo_when_provider_has_no_match() -> None:
    store = InMemoryGraphStore()
    service = VcsReadSyncService(
        vcs_provider=FakeVcsProvider(),
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
    )

    result = await service.sync_repo(
        tenant_id="demo",
        repo_name="missing-repo",
        observed_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
    )

    repo_tree = await store.get_program_tree("demo", "missing-repo", date(2026, 1, 10))
    cursor = await store.get_cursor("demo", "vcs", "repo:missing-repo")

    assert result.items_synced == 0
    assert repo_tree.root.name == "missing-repo"
    assert cursor.value is None
    assert cursor.updated_at is None
    assert cursor.metadata["last_item_count"] == 0
