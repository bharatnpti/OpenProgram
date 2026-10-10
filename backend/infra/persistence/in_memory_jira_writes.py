from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from core.domain.jira_writes import JiraWritesChange, StoredJiraWrites


@dataclass
class InMemoryJiraWritesRepository:
    """The tenant's Jira write switches and change log, for memory mode and tests."""

    _stored: dict[str, StoredJiraWrites] = field(default_factory=dict)
    _changes: list[JiraWritesChange] = field(default_factory=list)

    async def get_jira_writes(self, tenant_id: str) -> StoredJiraWrites | None:
        return self._stored.get(tenant_id)

    async def save_jira_writes(
        self, tenant_id: str, stored: StoredJiraWrites, *, at: datetime, actor: str
    ) -> None:
        self._stored[tenant_id] = stored

    async def append_jira_writes_change(self, change: JiraWritesChange) -> None:
        self._changes.append(change)

    async def list_jira_writes_changes(self, tenant_id: str, limit: int) -> list[JiraWritesChange]:
        mine = [change for change in self._changes if change.tenant_id == tenant_id]
        # Newest first; a stable sort keeps the order of rows written at the same moment.
        return sorted(reversed(mine), key=lambda change: change.at, reverse=True)[:limit]
