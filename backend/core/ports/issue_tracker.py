from __future__ import annotations

from typing import Protocol

from core.domain.integrations import Issue, UserRef


class IssueTracker(Protocol):
    async def get_issue(self, tenant_id: str, key: str) -> Issue: ...

    async def list_active_for(self, assignee: UserRef) -> list[Issue]: ...

    async def transition(self, tenant_id: str, key: str, to_state: str) -> None: ...

    async def add_comment(self, tenant_id: str, key: str, body: str) -> None: ...
