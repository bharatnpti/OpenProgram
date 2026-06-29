from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from core.domain.errors import ProviderUnavailable
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


@dataclass
class FakeIssueTracker:
    tenant_id: str
    projects: list[Project] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    sprints: list[Sprint] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.projects:
            self.projects = [
                Project(
                    tenant_id=self.tenant_id,
                    id="project-pulseops",
                    key="PO",
                    name="PulseOps",
                )
            ]
        if not self.issues:
            self.issues = [
                Issue(
                    tenant_id=self.tenant_id,
                    key="PO-1",
                    title="Build status collector",
                    state=IssueState.IN_PROGRESS,
                    assignee=UserRef(
                        tenant_id=self.tenant_id,
                        external_id="dev-asha",
                        display_name="Asha",
                    ),
                    metadata={"project_key": "PO", "container_id": "pod-runtime"},
                    updated_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
                ),
                Issue(
                    tenant_id=self.tenant_id,
                    key="PO-2",
                    title="Wire read-only integrations",
                    state=IssueState.TODO,
                    assignee=UserRef(
                        tenant_id=self.tenant_id,
                        external_id="dev-liam",
                        display_name="Liam",
                    ),
                    metadata={"project_key": "PO", "container_id": "pod-runtime"},
                    updated_at=datetime(2026, 1, 10, 10, 0, tzinfo=UTC),
                ),
            ]
        if not self.sprints:
            self.sprints = [
                Sprint(
                    tenant_id=self.tenant_id,
                    id="sprint-1",
                    board_id="board-1",
                    name="Phase 1",
                    state="active",
                    starts_at=datetime(2026, 1, 5, tzinfo=UTC),
                    ends_at=datetime(2026, 1, 16, tzinfo=UTC),
                )
            ]

    async def list_projects(self, tenant_id: str) -> list[Project]:
        return [project for project in self.projects if project.tenant_id == tenant_id]

    async def list_issues_updated_since(
        self, tenant_id: str, project_key: str, cursor: SyncCursor
    ) -> list[Issue]:
        return await self.list_issues_for_query(tenant_id, f"project = {project_key}", cursor)

    async def list_issues_for_query(
        self, tenant_id: str, jql: str, cursor: SyncCursor
    ) -> list[Issue]:
        project_key = _project_key_from_jql(jql)
        return [
            issue
            for issue in self.issues
            if issue.tenant_id == tenant_id
            and (project_key is None or _issue_project_key(issue) == project_key)
            and _after_cursor(issue.updated_at, cursor)
        ]

    async def list_sprints(self, tenant_id: str, board_id: str) -> list[Sprint]:
        return [
            sprint
            for sprint in self.sprints
            if sprint.tenant_id == tenant_id and sprint.board_id == board_id
        ]

    async def get_issue(self, tenant_id: str, key: str) -> Issue:
        for issue in self.issues:
            if issue.tenant_id == tenant_id and issue.key == key:
                return issue
        raise ProviderUnavailable(f"issue {key} not found")

    async def list_active_for(self, assignee: UserRef) -> list[Issue]:
        return [
            issue
            for issue in self.issues
            if issue.assignee is not None
            and issue.assignee.tenant_id == assignee.tenant_id
            and issue.assignee.external_id == assignee.external_id
            and issue.state is not IssueState.DONE
        ]

    async def transition(self, tenant_id: str, key: str, to_state: str) -> None:
        raise ProviderUnavailable("fake issue tracker is read-only")

    async def add_comment(self, tenant_id: str, key: str, body: str) -> None:
        raise ProviderUnavailable("fake issue tracker is read-only")


@dataclass
class FakeVcsProvider:
    tenant_id: str
    repos: list[Repo] = field(default_factory=list)
    commits: list[Commit] = field(default_factory=list)
    pull_requests: list[PullRequest] = field(default_factory=list)

    def __post_init__(self) -> None:
        author = UserRef(tenant_id=self.tenant_id, external_id="dev-asha", display_name="Asha")
        if not self.repos:
            self.repos = [
                Repo(
                    tenant_id=self.tenant_id,
                    id="repo-pulseops",
                    name="pulseops",
                    default_branch="main",
                )
            ]
        if not self.commits:
            self.commits = [
                Commit(
                    tenant_id=self.tenant_id,
                    repo="pulseops",
                    sha="abc123",
                    message="Add Phase 1 read sync",
                    author=author,
                    committed_at=datetime(2026, 1, 10, 8, 30, tzinfo=UTC),
                )
            ]
        if not self.pull_requests:
            self.pull_requests = [
                PullRequest(
                    tenant_id=self.tenant_id,
                    id="42",
                    title="Phase 1 integrations",
                    author=author,
                    merged=False,
                    metadata={"repo": "pulseops"},
                    updated_at=datetime(2026, 1, 10, 9, 30, tzinfo=UTC),
                )
            ]

    async def list_repos(self, tenant_id: str) -> list[Repo]:
        return [repo for repo in self.repos if repo.tenant_id == tenant_id]

    async def list_commits(self, tenant_id: str, repo: str, cursor: SyncCursor) -> list[Commit]:
        return [
            commit
            for commit in self.commits
            if commit.tenant_id == tenant_id
            and commit.repo == repo
            and _after_cursor(commit.committed_at, cursor)
        ]

    async def list_pull_requests(
        self, tenant_id: str, repo: str, cursor: SyncCursor | None = None
    ) -> list[PullRequest]:
        return [
            pull_request
            for pull_request in self.pull_requests
            if pull_request.tenant_id == tenant_id
            and pull_request.metadata.get("repo") == repo
            and (cursor is None or _after_cursor(pull_request.updated_at, cursor))
        ]

    async def list_pull_requests_for(self, author: UserRef) -> list[PullRequest]:
        return [
            pull_request for pull_request in self.pull_requests if pull_request.author == author
        ]


@dataclass
class FakeCalendarProvider:
    tenant_id: str
    events: list[CalendarEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.events:
            user = UserRef(tenant_id=self.tenant_id, external_id="dev-asha", display_name="Asha")
            self.events = [
                CalendarEvent(
                    tenant_id=self.tenant_id,
                    user=user,
                    starts_on=date(2026, 1, 12),
                    ends_on=date(2026, 1, 13),
                    kind="out_of_office",
                    metadata={
                        "timezone": "Europe/Berlin",
                        "blocks_availability": True,
                    },
                )
            ]

    async def list_events(self, user: UserRef, start: date, end: date) -> list[CalendarEvent]:
        return [
            event
            for event in self.events
            if event.user.tenant_id == user.tenant_id
            and event.user.external_id == user.external_id
            and event.starts_on >= start
            and event.starts_on < end
        ]


def _issue_project_key(issue: Issue) -> str | None:
    value = issue.metadata.get("project_key")
    if isinstance(value, str):
        return value
    if "-" in issue.key:
        return issue.key.split("-", maxsplit=1)[0]
    return None


def _project_key_from_jql(jql: str) -> str | None:
    normalized = jql.replace("'", '"')
    marker = "project ="
    index = normalized.lower().find(marker)
    if index < 0:
        return None
    value = normalized[index + len(marker) :].strip()
    if value.startswith('"'):
        return value.split('"', maxsplit=2)[1] if value.count('"') >= 2 else None
    return value.split(maxsplit=1)[0].strip("()") or None


def _after_cursor(updated_at: datetime | None, cursor: SyncCursor) -> bool:
    return cursor.updated_at is None or updated_at is None or updated_at > cursor.updated_at
