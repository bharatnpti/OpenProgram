from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    JsonScalar,
    NodeKind,
    Task,
)
from core.domain.integrations import CalendarEvent, Commit, Issue, PullRequest, SyncCursor, UserRef
from core.ports.calendar import CalendarProvider
from core.ports.issue_tracker import IssueTracker
from core.ports.repositories import GraphRepository, SyncCursorRepository, TimeSeriesRepository
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
    ) -> None:
        self._issue_tracker = issue_tracker
        self._graph_repository = graph_repository
        self._time_series_repository = time_series_repository
        self._cursor_repository = cursor_repository

    async def sync_project(
        self,
        *,
        tenant_id: str,
        project_key: str,
        container_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> SyncRunResult:
        observed = _timestamp(observed_at)
        scope = f"project:{project_key}"
        cursor = await self._cursor_repository.get_cursor(tenant_id, self.connector, scope)
        issues = await self._issue_tracker.list_issues_updated_since(
            tenant_id,
            project_key,
            cursor,
        )

        for issue in issues:
            await self._sync_issue(issue, project_key, container_id, observed)

        next_cursor = _next_cursor(cursor, (_issue_updated_at(issue, observed) for issue in issues))
        recorded_cursor = _with_sync_metadata(next_cursor, observed, len(issues))
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

    async def _sync_issue(
        self,
        issue: Issue,
        project_key: str,
        container_id: str | None,
        observed_at: datetime,
    ) -> None:
        metadata = {
            **_scalar_mapping(issue.metadata),
            "key": issue.key,
            "state": issue.state.value,
            "project_key": project_key,
        }
        await self._graph_repository.upsert_node(
            Task(
                tenant_id=issue.tenant_id,
                id=issue.key,
                name=issue.title,
                metadata=metadata,
            )
        )
        parent_id = container_id or _string_metadata(issue.metadata, "container_id")
        if parent_id:
            await self._graph_repository.add_edge(
                GraphEdge(
                    tenant_id=issue.tenant_id,
                    from_node_id=parent_id,
                    to_node_id=issue.key,
                    kind=EdgeKind.CONTAINS,
                )
            )
        if issue.assignee is not None:
            await self._upsert_assignee(issue.assignee)
            await self._graph_repository.add_edge(
                GraphEdge(
                    tenant_id=issue.tenant_id,
                    from_node_id=issue.assignee.external_id,
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
                payload=_issue_fact_payload(issue, project_key),
                observed_at=issue_observed_at,
                correlation_id=(
                    f"{self.connector}:{issue.tenant_id}:{issue.key}:"
                    f"{issue_observed_at.isoformat()}"
                ),
            )
        )

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
        time_series_repository: TimeSeriesRepository,
        cursor_repository: SyncCursorRepository,
    ) -> None:
        self._vcs_provider = vcs_provider
        self._time_series_repository = time_series_repository
        self._cursor_repository = cursor_repository

    async def sync_repo(
        self,
        *,
        tenant_id: str,
        repo_name: str,
        observed_at: datetime | None = None,
    ) -> SyncRunResult:
        observed = _timestamp(observed_at)
        scope = f"repo:{repo_name}"
        cursor = await self._cursor_repository.get_cursor(tenant_id, self.connector, scope)
        commits = await self._vcs_provider.list_commits(tenant_id, repo_name, cursor)
        pull_requests = await self._vcs_provider.list_pull_requests(tenant_id, repo_name, cursor)

        for commit in commits:
            await self._append_commit_fact(commit)
        for pull_request in pull_requests:
            await self._append_pull_request_fact(pull_request, repo_name, observed)

        timestamps = [
            *(commit.committed_at for commit in commits),
            *(_pull_request_updated_at(pull_request, observed) for pull_request in pull_requests),
        ]
        next_cursor = _next_cursor(cursor, timestamps)
        item_count = len(commits) + len(pull_requests)
        recorded_cursor = _with_sync_metadata(next_cursor, observed, item_count)
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

    async def _append_commit_fact(self, commit: Commit) -> None:
        await self._time_series_repository.append_fact_once(
            FactEvent(
                tenant_id=commit.tenant_id,
                source="vcs_commit",
                entity_ref=_author_or_repo_ref(commit.tenant_id, commit.repo, commit.author),
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
    ) -> None:
        pull_request_observed_at = _pull_request_updated_at(pull_request, observed_at)
        await self._time_series_repository.append_fact_once(
            FactEvent(
                tenant_id=pull_request.tenant_id,
                source="vcs_pull_request",
                entity_ref=EntityRef(
                    tenant_id=pull_request.tenant_id,
                    kind=NodeKind.DEVELOPER,
                    id=pull_request.author.external_id,
                ),
                payload={
                    "repo": repo_name,
                    "id": pull_request.id,
                    "title": pull_request.title,
                    "merged": pull_request.merged,
                },
                observed_at=pull_request_observed_at,
                correlation_id=(
                    f"vcs:pull_request:{pull_request.tenant_id}:{repo_name}:"
                    f"{pull_request.id}:{pull_request_observed_at.isoformat()}"
                ),
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
        observed = _timestamp(observed_at)
        scope = f"user:{user.external_id}"
        events = await self._calendar_provider.list_events(user, start, end)

        for event in events:
            await self._append_event_fact(event, observed)

        cursor = _with_sync_metadata(
            SyncCursor(
                value=end.isoformat(),
                updated_at=observed,
                metadata={"start": start.isoformat(), "end": end.isoformat()},
            ),
            observed,
            len(events),
        )
        await self._cursor_repository.record_cursor(
            user.tenant_id,
            self.connector,
            scope,
            cursor,
        )
        return SyncRunResult(
            connector=self.connector,
            scope=scope,
            items_synced=len(events),
            cursor=cursor,
        )

    async def _append_event_fact(self, event: CalendarEvent, observed_at: datetime) -> None:
        await self._time_series_repository.append_fact_once(
            FactEvent(
                tenant_id=event.tenant_id,
                source=self.connector,
                entity_ref=EntityRef(
                    tenant_id=event.tenant_id,
                    kind=NodeKind.DEVELOPER,
                    id=event.user.external_id,
                ),
                payload={
                    "kind": event.kind,
                    "starts_on": event.starts_on.isoformat(),
                    "ends_on": event.ends_on.isoformat(),
                    "timezone": _calendar_timezone(event),
                },
                observed_at=observed_at,
                correlation_id=(
                    f"{self.connector}:{event.tenant_id}:{event.user.external_id}:"
                    f"{event.starts_on.isoformat()}:{event.ends_on.isoformat()}:{event.kind}"
                ),
            )
        )


def _issue_fact_payload(issue: Issue, project_key: str) -> dict[str, JsonScalar]:
    return {
        "key": issue.key,
        "title": issue.title,
        "state": issue.state.value,
        "project_key": project_key,
        "assignee_id": issue.assignee.external_id if issue.assignee else None,
    }


def _author_or_repo_ref(
    tenant_id: str,
    repo_name: str,
    author: UserRef | None,
) -> EntityRef:
    if author is not None:
        return EntityRef(tenant_id=tenant_id, kind=NodeKind.DEVELOPER, id=author.external_id)
    return EntityRef(tenant_id=tenant_id, kind=NodeKind.PROJECT, id=repo_name)


def _next_cursor(cursor: SyncCursor, timestamps: Iterable[datetime]) -> SyncCursor:
    latest = _timestamp(cursor.updated_at) if cursor.updated_at is not None else None
    for timestamp in timestamps:
        normalized = _timestamp(timestamp)
        if latest is None or normalized > _timestamp(latest):
            latest = normalized
    return SyncCursor(
        value=latest.isoformat() if latest is not None else cursor.value,
        updated_at=latest,
        metadata=dict(cursor.metadata),
    )


def _with_sync_metadata(cursor: SyncCursor, observed_at: datetime, item_count: int) -> SyncCursor:
    return SyncCursor(
        value=cursor.value,
        updated_at=cursor.updated_at,
        metadata={
            **cursor.metadata,
            "last_checked_at": observed_at.isoformat(),
            "last_item_count": item_count,
        },
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


def _calendar_timezone(event: CalendarEvent) -> str | None:
    for key in ("timezone", "time_zone", "tz"):
        value = event.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
