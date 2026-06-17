from __future__ import annotations

from typing import Protocol

from core.domain.integrations import Issue, Project, Sprint, SyncCursor, UserRef


class IssueTracker(Protocol):
    async def list_projects(self, tenant_id: str) -> list[Project]: ...

    async def list_issues_updated_since(
        self, tenant_id: str, project_key: str, cursor: SyncCursor
    ) -> list[Issue]: ...

    async def list_sprints(self, tenant_id: str, board_id: str) -> list[Sprint]: ...

    async def get_issue(self, tenant_id: str, key: str) -> Issue: ...

    async def list_active_for(self, assignee: UserRef) -> list[Issue]: ...

    # Reserved for later write-back phases. Phase 1 application code must not call this.
    async def transition(self, tenant_id: str, key: str, to_state: str) -> None: ...

    # Reserved for later write-back phases. Phase 1 application code must not call this.
    async def add_comment(self, tenant_id: str, key: str, body: str) -> None: ...
