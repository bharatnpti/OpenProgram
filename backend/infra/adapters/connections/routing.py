"""Send each call to the adapter the tenant's connections choose.

The server's settings pick a default adapter for each capability (often the
built-in sample one in a demo). An admin who turns a real connection on in the
console gets that system instead, from the next call, without a restart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from core.domain.integrations import (
    CalendarEvent,
    Commit,
    Issue,
    IssueText,
    Project,
    PullRequest,
    Repo,
    Sprint,
    SyncCursor,
    UserRef,
)
from core.ports.calendar import CalendarProvider
from core.ports.connections import ConnectionResolver
from core.ports.issue_tracker import IssueTracker
from core.ports.vcs import VcsProvider


async def enabled_connector(
    connections: ConnectionResolver, tenant_id: str, connectors: tuple[str, ...]
) -> str | None:
    """The first of ``connectors`` the tenant has turned on, or None."""
    for connector in connectors:
        if await connections.resolve(tenant_id, connector) is not None:
            return connector
    return None


@dataclass(frozen=True)
class TenantRoutedIssueTracker:
    fallback: IssueTracker
    routes: Mapping[str, IssueTracker]
    connections: ConnectionResolver

    async def _pick(self, tenant_id: str) -> IssueTracker:
        connector = await enabled_connector(self.connections, tenant_id, tuple(self.routes))
        return self.routes[connector] if connector is not None else self.fallback

    async def list_projects(self, tenant_id: str) -> list[Project]:
        return await (await self._pick(tenant_id)).list_projects(tenant_id)

    async def list_issues_updated_since(
        self, tenant_id: str, project_key: str, cursor: SyncCursor
    ) -> list[Issue]:
        tracker = await self._pick(tenant_id)
        return await tracker.list_issues_updated_since(tenant_id, project_key, cursor)

    async def list_issues_for_query(
        self, tenant_id: str, jql: str, cursor: SyncCursor
    ) -> list[Issue]:
        return await (await self._pick(tenant_id)).list_issues_for_query(tenant_id, jql, cursor)

    async def list_sprints(self, tenant_id: str, board_id: str) -> list[Sprint]:
        return await (await self._pick(tenant_id)).list_sprints(tenant_id, board_id)

    async def get_issue(self, tenant_id: str, key: str) -> Issue:
        return await (await self._pick(tenant_id)).get_issue(tenant_id, key)

    async def get_issue_text(self, tenant_id: str, key: str) -> IssueText:
        return await (await self._pick(tenant_id)).get_issue_text(tenant_id, key)

    async def list_active_for(self, assignee: UserRef) -> list[Issue]:
        return await (await self._pick(assignee.tenant_id)).list_active_for(assignee)

    async def find_user_by_email(self, tenant_id: str, email: str) -> UserRef | None:
        return await (await self._pick(tenant_id)).find_user_by_email(tenant_id, email)

    async def transition(self, tenant_id: str, key: str, to_state: str) -> None:
        await (await self._pick(tenant_id)).transition(tenant_id, key, to_state)

    async def add_comment(self, tenant_id: str, key: str, body: str) -> None:
        await (await self._pick(tenant_id)).add_comment(tenant_id, key, body)


@dataclass(frozen=True)
class TenantRoutedVcsProvider:
    fallback: VcsProvider
    routes: Mapping[str, VcsProvider]
    connections: ConnectionResolver

    async def _pick(self, tenant_id: str) -> VcsProvider:
        connector = await enabled_connector(self.connections, tenant_id, tuple(self.routes))
        return self.routes[connector] if connector is not None else self.fallback

    async def list_repos(self, tenant_id: str) -> list[Repo]:
        return await (await self._pick(tenant_id)).list_repos(tenant_id)

    async def list_commits(self, tenant_id: str, repo: str, cursor: SyncCursor) -> list[Commit]:
        return await (await self._pick(tenant_id)).list_commits(tenant_id, repo, cursor)

    async def list_pull_requests(
        self, tenant_id: str, repo: str, cursor: SyncCursor | None = None
    ) -> list[PullRequest]:
        return await (await self._pick(tenant_id)).list_pull_requests(tenant_id, repo, cursor)

    async def list_pull_requests_for(self, author: UserRef) -> list[PullRequest]:
        return await (await self._pick(author.tenant_id)).list_pull_requests_for(author)


@dataclass(frozen=True)
class TenantRoutedCalendarProvider:
    fallback: CalendarProvider
    routes: Mapping[str, CalendarProvider]
    connections: ConnectionResolver

    async def list_events(self, user: UserRef, start: date, end: date) -> list[CalendarEvent]:
        connector = await enabled_connector(self.connections, user.tenant_id, tuple(self.routes))
        provider = self.routes[connector] if connector is not None else self.fallback
        return await provider.list_events(user, start, end)
