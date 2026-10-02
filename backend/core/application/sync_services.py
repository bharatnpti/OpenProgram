from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from core.application.sync_recording import recording_sync_failure, succeeded_cursor
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    JsonScalar,
    NodeKind,
    RepoNode,
    SprintNode,
    Task,
)
from core.domain.graph import (
    Project as GraphProject,
)
from core.domain.integrations import (
    Commit,
    Issue,
    Project,
    PullRequest,
    Repo,
    SyncCursor,
    UserRef,
)
from core.ports.calendar import CalendarProvider
from core.ports.issue_tracker import IssueTracker
from core.ports.repositories import (
    GraphRepository,
    IdentityLinkRepository,
    SyncCursorRepository,
    TimeSeriesRepository,
)
from core.ports.vcs import VcsProvider


@dataclass(frozen=True, kw_only=True)
class SyncRunResult:
    connector: str
    scope: str
    items_synced: int
    cursor: SyncCursor


class IssueReadSyncService:
    connector = "issue"

    def __init__(
        self,
        *,
        issue_tracker: IssueTracker,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
        cursor_repository: SyncCursorRepository,
        identity_link_repository: IdentityLinkRepository | None = None,
    ) -> None:
        self._issue_tracker = issue_tracker
        self._graph_repository = graph_repository
        self._time_series_repository = time_series_repository
        self._cursor_repository = cursor_repository
        self._identity_link_repository = identity_link_repository

    async def sync_project(
        self,
        *,
        tenant_id: str,
        project_key: str,
        container_id: str | None = None,
        board_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> SyncRunResult:
        observed = _timestamp(observed_at)
        scope = f"project:{project_key}"
        cursor = await self._cursor_repository.get_cursor(tenant_id, self.connector, scope)
        async with self._recording_failure(tenant_id, scope, cursor, observed):
            project = await self._sync_project_node(tenant_id, project_key, container_id)
            sprints = (
                await self._sync_sprint_nodes(tenant_id, project.id, board_id)
                if board_id is not None
                else []
            )
            issues = await self._issue_tracker.list_issues_updated_since(
                tenant_id,
                project_key,
                cursor,
            )

            developers = await self._developers_by_tracker_account(tenant_id)
            for issue in issues:
                await self._sync_issue(issue, project, sprints, observed, developers)

            next_cursor = _next_cursor(
                cursor, (_issue_updated_at(issue, observed) for issue in issues)
            )
            recorded_cursor = succeeded_cursor(next_cursor, observed, len(issues))
            await self._cursor_repository.record_cursor(
                tenant_id,
                self.connector,
                scope,
                recorded_cursor,
            )
        return SyncRunResult(
            connector=self.connector,
            scope=scope,
            items_synced=len(issues),
            cursor=recorded_cursor,
        )

    async def sync_query(
        self,
        *,
        tenant_id: str,
        jql: str,
        target_node_id: str,
        target_node_kind: NodeKind,
        cursor_scope: str,
        board_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> SyncRunResult:
        observed = _timestamp(observed_at)
        cursor = await self._cursor_repository.get_cursor(tenant_id, self.connector, cursor_scope)
        async with self._recording_failure(tenant_id, cursor_scope, cursor, observed):
            target = await self._sync_target_node(tenant_id, target_node_id, target_node_kind)
            sprints = (
                await self._sync_sprint_nodes(tenant_id, target.id, board_id)
                if board_id is not None and target.kind is NodeKind.PROJECT
                else []
            )
            issues = await self._issue_tracker.list_issues_for_query(tenant_id, jql, cursor)

            developers = await self._developers_by_tracker_account(tenant_id)
            for issue in issues:
                await self._sync_issue(issue, target, sprints, observed, developers)

            next_cursor = _next_cursor(
                cursor, (_issue_updated_at(issue, observed) for issue in issues)
            )
            recorded_cursor = succeeded_cursor(next_cursor, observed, len(issues))
            await self._cursor_repository.record_cursor(
                tenant_id,
                self.connector,
                cursor_scope,
                recorded_cursor,
            )
        return SyncRunResult(
            connector=self.connector,
            scope=cursor_scope,
            items_synced=len(issues),
            cursor=recorded_cursor,
        )

    def _recording_failure(
        self, tenant_id: str, scope: str, cursor: SyncCursor, attempted_at: datetime
    ) -> AbstractAsyncContextManager[None]:
        return recording_sync_failure(
            self._cursor_repository,
            tenant_id=tenant_id,
            connector=self.connector,
            scope=scope,
            cursor=cursor,
            attempted_at=attempted_at,
        )

    async def _sync_project_node(
        self,
        tenant_id: str,
        project_key: str,
        container_id: str | None,
    ) -> GraphProject:
        projects = await self._issue_tracker.list_projects(tenant_id)
        project = _project_for_key(projects, project_key)
        node = GraphProject(
            tenant_id=tenant_id,
            id=project.key,
            name=project.name,
            metadata={
                **_scalar_mapping(project.metadata),
                "key": project.key,
                "external_id": project.id,
            },
        )
        await self._graph_repository.upsert_node(node)
        if container_id:
            await self._graph_repository.add_edge(
                GraphEdge(
                    tenant_id=tenant_id,
                    from_node_id=container_id,
                    to_node_id=node.id,
                    kind=EdgeKind.CONTAINS,
                )
            )
        return node

    async def _sync_target_node(
        self,
        tenant_id: str,
        target_node_id: str,
        target_node_kind: NodeKind,
    ) -> GraphNode:
        node = await self._graph_repository.get_node(tenant_id, target_node_id)
        if node is None:
            node = GraphNode(
                tenant_id=tenant_id,
                id=target_node_id,
                kind=target_node_kind,
                name=target_node_id,
            )
            await self._graph_repository.upsert_node(node)
        return node

    async def _sync_sprint_nodes(
        self,
        tenant_id: str,
        project_id: str,
        board_id: str,
    ) -> list[SprintNode]:
        sprint_nodes: list[SprintNode] = []
        for sprint in await self._issue_tracker.list_sprints(tenant_id, board_id):
            node = SprintNode(
                tenant_id=sprint.tenant_id,
                id=sprint.id,
                name=sprint.name,
                metadata={
                    **_scalar_mapping(sprint.metadata),
                    "board_id": sprint.board_id,
                    "state": sprint.state,
                    "starts_at": _datetime_iso(sprint.starts_at),
                    "ends_at": _datetime_iso(sprint.ends_at),
                },
            )
            await self._graph_repository.upsert_node(node)
            await self._graph_repository.add_edge(
                GraphEdge(
                    tenant_id=tenant_id,
                    from_node_id=project_id,
                    to_node_id=node.id,
                    kind=EdgeKind.CONTAINS,
                )
            )
            sprint_nodes.append(node)
        return sprint_nodes

    async def _sync_issue(
        self,
        issue: Issue,
        parent: GraphNode,
        sprints: list[SprintNode],
        observed_at: datetime,
        developers: Mapping[str, str],
    ) -> None:
        metadata = {
            **_scalar_mapping(issue.metadata),
            "key": issue.key,
            "state": issue.state.value,
            "project_key": _project_key_for_issue(issue, parent.id),
        }
        await self._graph_repository.upsert_node(
            Task(
                tenant_id=issue.tenant_id,
                id=issue.key,
                name=issue.title,
                metadata=metadata,
            )
        )
        await self._graph_repository.add_edge(
            GraphEdge(
                tenant_id=issue.tenant_id,
                from_node_id=_issue_parent_id(issue, parent.id, sprints),
                to_node_id=issue.key,
                kind=EdgeKind.CONTAINS,
            )
        )
        if issue.assignee is not None:
            developer_id = developers.get(issue.assignee.external_id)
            if developer_id is None:
                # Nobody is linked to this tracker account yet: keep the work
                # attributable by recording the account as its own developer.
                await self._upsert_assignee(issue.assignee)
                developer_id = issue.assignee.external_id
            await self._graph_repository.add_edge(
                GraphEdge(
                    tenant_id=issue.tenant_id,
                    from_node_id=developer_id,
                    to_node_id=issue.key,
                    kind=EdgeKind.ASSIGNED_TO,
                )
            )

        issue_observed_at = _issue_updated_at(issue, observed_at)
        await self._time_series_repository.append_fact_once(
            FactEvent(
                tenant_id=issue.tenant_id,
                source=self.connector,
                entity_ref=EntityRef(tenant_id=issue.tenant_id, kind=NodeKind.TASK, id=issue.key),
                payload=_issue_fact_payload(issue, _project_key_for_issue(issue, parent.id)),
                observed_at=issue_observed_at,
                correlation_id=(
                    f"{self.connector}:{issue.tenant_id}:{issue.key}:"
                    f"{issue_observed_at.isoformat()}"
                ),
            )
        )

    async def _developers_by_tracker_account(self, tenant_id: str) -> dict[str, str]:
        """Tracker account id -> member id, from identity links.

        Members are keyed by their chat id; the tracker only knows its own
        account ids. Without this map every assignee became a second developer
        node beside the real member, and that member's own views never saw the
        issue.
        """
        if self._identity_link_repository is None:
            return {}
        return {
            link.jira_account_id: link.developer_id
            for link in await self._identity_link_repository.list_identity_links(tenant_id)
            if link.jira_account_id
        }

    async def _upsert_assignee(self, assignee: UserRef) -> None:
        await self._graph_repository.upsert_node(
            Developer(
                tenant_id=assignee.tenant_id,
                id=assignee.external_id,
                name=assignee.display_name or assignee.external_id,
            )
        )


class VcsReadSyncService:
    connector = "vcs"

    def __init__(
        self,
        *,
        vcs_provider: VcsProvider,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
        cursor_repository: SyncCursorRepository,
        identity_link_repository: IdentityLinkRepository | None = None,
    ) -> None:
        self._vcs_provider = vcs_provider
        self._graph_repository = graph_repository
        self._time_series_repository = time_series_repository
        self._cursor_repository = cursor_repository
        self._identity_link_repository = identity_link_repository

    async def sync_repo(
        self,
        *,
        tenant_id: str,
        repo_name: str,
        container_ids: tuple[str, ...] = (),
        observed_at: datetime | None = None,
    ) -> SyncRunResult:
        observed = _timestamp(observed_at)
        scope = f"repo:{repo_name}"
        cursor = await self._cursor_repository.get_cursor(tenant_id, self.connector, scope)
        async with recording_sync_failure(
            self._cursor_repository,
            tenant_id=tenant_id,
            connector=self.connector,
            scope=scope,
            cursor=cursor,
            attempted_at=observed,
        ):
            repo = await self._sync_repo_node(tenant_id, repo_name)
            for container_id in container_ids:
                await self._link_repo_container(tenant_id, container_id, repo.id)
            commits = await self._vcs_provider.list_commits(tenant_id, repo_name, cursor)
            pull_requests = await self._vcs_provider.list_pull_requests(
                tenant_id, repo_name, cursor
            )

            members = await self._members_by_vcs_identity(tenant_id)
            for commit in commits:
                await self._append_commit_fact(commit, repo.ref, members)
            for pull_request in pull_requests:
                await self._append_pull_request_fact(pull_request, repo_name, observed, members)

            timestamps = [
                *(commit.committed_at for commit in commits),
                *(
                    _pull_request_updated_at(pull_request, observed)
                    for pull_request in pull_requests
                ),
            ]
            next_cursor = _next_cursor(cursor, timestamps)
            item_count = len(commits) + len(pull_requests)
            recorded_cursor = succeeded_cursor(next_cursor, observed, item_count)
            await self._cursor_repository.record_cursor(
                tenant_id,
                self.connector,
                scope,
                recorded_cursor,
            )
        return SyncRunResult(
            connector=self.connector,
            scope=scope,
            items_synced=item_count,
            cursor=recorded_cursor,
        )

    async def _sync_repo_node(self, tenant_id: str, repo_name: str) -> RepoNode:
        repos = await self._vcs_provider.list_repos(tenant_id)
        repo = _repo_for_name(repos, repo_name)
        node = RepoNode(
            tenant_id=tenant_id,
            id=repo.name,
            name=repo.name,
            metadata={
                **_scalar_mapping(repo.metadata),
                "external_id": repo.id,
                "default_branch": repo.default_branch,
            },
        )
        await self._graph_repository.upsert_node(node)
        return node

    async def _link_repo_container(self, tenant_id: str, container_id: str, repo_id: str) -> None:
        container = await self._graph_repository.get_node(tenant_id, container_id)
        if container is None:
            return
        await self._graph_repository.add_edge(
            GraphEdge(
                tenant_id=tenant_id,
                from_node_id=container.id,
                to_node_id=repo_id,
                kind=EdgeKind.CONTAINS,
            )
        )

    async def _members_by_vcs_identity(self, tenant_id: str) -> dict[str, str]:
        """Lower-cased git login or commit email -> member id.

        Members are keyed by their chat id. A provider reports a merge request's
        author by login and a commit's author by the email on the commit, so the
        login comes from the identity link's ``vcs_username`` and the email from
        the member's directory profile. Without this every author became a
        second (and for commits a third) developer node beside the member, and
        none of the member's own views or risk owners saw their git activity.
        """
        members: dict[str, str] = {}
        for node in await self._graph_repository.list_nodes(tenant_id, NodeKind.DEVELOPER):
            email = node.metadata.get("email")
            if isinstance(email, str) and email.strip():
                members.setdefault(email.strip().lower(), node.id)
        if self._identity_link_repository is not None:
            for link in await self._identity_link_repository.list_identity_links(tenant_id):
                if link.vcs_username:
                    members[link.vcs_username.strip().lower()] = link.developer_id
        return members

    async def _member_or_new_developer(
        self, author: UserRef, members: Mapping[str, str]
    ) -> UserRef:
        member_id = members.get(author.external_id.strip().lower())
        if member_id is not None:
            return replace(author, external_id=member_id)
        # Nobody is linked to this author yet: keep the activity attributable by
        # recording the author as its own developer, as before.
        await self._upsert_developer(author)
        return author

    async def _append_commit_fact(
        self, commit: Commit, repo_ref: EntityRef, members: Mapping[str, str]
    ) -> None:
        author = (
            await self._member_or_new_developer(commit.author, members)
            if commit.author is not None
            else None
        )
        await self._time_series_repository.append_fact_once(
            FactEvent(
                tenant_id=commit.tenant_id,
                source="vcs_commit",
                entity_ref=_author_or_repo_ref(commit.tenant_id, repo_ref, author),
                payload={
                    "repo": commit.repo,
                    "sha": commit.sha,
                    "message": commit.message,
                },
                observed_at=_timestamp(commit.committed_at),
                correlation_id=f"vcs:commit:{commit.tenant_id}:{commit.repo}:{commit.sha}",
            )
        )

    async def _append_pull_request_fact(
        self,
        pull_request: PullRequest,
        repo_name: str,
        observed_at: datetime,
        members: Mapping[str, str],
    ) -> None:
        author = await self._member_or_new_developer(pull_request.author, members)
        pull_request_observed_at = _pull_request_updated_at(pull_request, observed_at)
        await self._time_series_repository.append_fact_once(
            FactEvent(
                tenant_id=pull_request.tenant_id,
                source="vcs_pull_request",
                entity_ref=EntityRef(
                    tenant_id=pull_request.tenant_id,
                    kind=NodeKind.DEVELOPER,
                    id=author.external_id,
                ),
                payload={
                    "repo": repo_name,
                    "id": pull_request.id,
                    "title": pull_request.title,
                    "merged": pull_request.merged,
                    "opened_at": _datetime_iso(pull_request.opened_at or pull_request.updated_at),
                },
                observed_at=pull_request_observed_at,
                correlation_id=(
                    f"vcs:pull_request:{pull_request.tenant_id}:{repo_name}:"
                    f"{pull_request.id}:{pull_request_observed_at.isoformat()}"
                ),
            )
        )

    async def _upsert_developer(self, user: UserRef) -> None:
        await self._graph_repository.upsert_node(
            Developer(
                tenant_id=user.tenant_id,
                id=user.external_id,
                name=user.display_name or user.external_id,
            )
        )


class CalendarReadSyncService:
    connector = "calendar"

    def __init__(
        self,
        *,
        calendar_provider: CalendarProvider,
        time_series_repository: TimeSeriesRepository,
        cursor_repository: SyncCursorRepository,
    ) -> None:
        self._calendar_provider = calendar_provider
        self._time_series_repository = time_series_repository
        self._cursor_repository = cursor_repository

    async def sync_user(
        self,
        *,
        user: UserRef,
        start: date,
        end: date,
        observed_at: datetime | None = None,
    ) -> SyncRunResult:
        scope = f"user:{user.external_id}"
        return SyncRunResult(
            connector=self.connector,
            scope=scope,
            items_synced=0,
            cursor=SyncCursor(),
        )


def _issue_fact_payload(issue: Issue, project_key: str) -> dict[str, JsonScalar]:
    return {
        "key": issue.key,
        "title": issue.title,
        "state": issue.state.value,
        "project_key": project_key,
        "assignee_id": issue.assignee.external_id if issue.assignee else None,
    }


def _project_key_for_issue(issue: Issue, fallback: str) -> str:
    value = issue.metadata.get("project_key")
    return value if isinstance(value, str) and value else fallback


def _project_for_key(projects: list[Project], project_key: str) -> Project:
    for project in projects:
        if project.key == project_key:
            return project
    return Project(
        tenant_id=projects[0].tenant_id if projects else "",
        id=project_key,
        key=project_key,
        name=project_key,
    )


def _repo_for_name(repos: list[Repo], repo_name: str) -> Repo:
    for repo in repos:
        if repo.name == repo_name or repo.id == repo_name:
            return repo
    return Repo(tenant_id=repos[0].tenant_id if repos else "", id=repo_name, name=repo_name)


def _issue_parent_id(issue: Issue, project_id: str, sprints: list[SprintNode]) -> str:
    sprint_id = _matching_sprint_id(issue.metadata, sprints)
    return sprint_id or project_id


def _matching_sprint_id(
    metadata: Mapping[str, JsonScalar], sprints: list[SprintNode]
) -> str | None:
    sprint_ref = (
        _string_metadata(metadata, "sprint_id")
        or _string_metadata(metadata, "sprint")
        or _string_metadata(metadata, "sprint_name")
    )
    if sprint_ref is None:
        return None
    for sprint in sprints:
        if sprint.id == sprint_ref or sprint.name == sprint_ref:
            return sprint.id
    return None


def _author_or_repo_ref(
    tenant_id: str,
    repo_ref: EntityRef,
    author: UserRef | None,
) -> EntityRef:
    if author is not None:
        return EntityRef(tenant_id=tenant_id, kind=NodeKind.DEVELOPER, id=author.external_id)
    return repo_ref


def _next_cursor(cursor: SyncCursor, timestamps: Iterable[datetime]) -> SyncCursor:
    latest = _timestamp(cursor.updated_at) if cursor.updated_at is not None else None
    for timestamp in timestamps:
        normalized = _timestamp(timestamp)
        if latest is None or normalized > latest:
            latest = normalized
    return SyncCursor(
        value=latest.isoformat() if latest is not None else cursor.value,
        updated_at=latest,
        metadata=dict(cursor.metadata),
    )


def _issue_updated_at(issue: Issue, fallback: datetime) -> datetime:
    return _timestamp(issue.updated_at or fallback)


def _pull_request_updated_at(pull_request: PullRequest, fallback: datetime) -> datetime:
    return _timestamp(pull_request.updated_at or fallback)


def _timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(tz=UTC)
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp


def _scalar_mapping(metadata: Mapping[str, JsonScalar]) -> dict[str, JsonScalar]:
    return {
        key: value
        for key, value in metadata.items()
        if value is None or isinstance(value, str | int | float | bool)
    }


def _string_metadata(metadata: Mapping[str, JsonScalar], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


def _datetime_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
