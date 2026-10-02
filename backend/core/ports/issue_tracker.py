from __future__ import annotations

from typing import Protocol

from core.domain.integrations import Issue, Project, Sprint, SyncCursor, UserRef


class IssueTracker(Protocol):
    async def list_projects(self, tenant_id: str) -> list[Project]: ...

    async def list_issues_updated_since(
        self, tenant_id: str, project_key: str, cursor: SyncCursor
    ) -> list[Issue]: ...

    async def list_issues_for_query(
        self, tenant_id: str, jql: str, cursor: SyncCursor
    ) -> list[Issue]: ...

    async def list_sprints(self, tenant_id: str, board_id: str) -> list[Sprint]: ...

    async def get_issue(self, tenant_id: str, key: str) -> Issue: ...

    async def list_active_for(self, assignee: UserRef) -> list[Issue]: ...

    async def find_user_by_email(self, tenant_id: str, email: str) -> UserRef | None:
        """Resolve the tracker account an email belongs to; ``None`` unless exactly one matches.

        Trackers index assignments by their own account id (Jira's ``accountId``),
        which nothing outside the tracker knows; email is the identifier people
        share across tools. Read-only.
        """
        ...

    # Reserved for later write-back phases. Phase 1 application code must not call this.
    async def transition(self, tenant_id: str, key: str, to_state: str) -> None: ...

    # Reserved for later write-back phases. Phase 1 application code must not call this.
    async def add_comment(self, tenant_id: str, key: str, body: str) -> None: ...
