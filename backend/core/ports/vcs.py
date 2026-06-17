from __future__ import annotations

from typing import Protocol

from core.domain.integrations import Commit, PullRequest, Repo, SyncCursor, UserRef


class VcsProvider(Protocol):
    async def list_repos(self, tenant_id: str) -> list[Repo]: ...

    async def list_commits(self, tenant_id: str, repo: str, cursor: SyncCursor) -> list[Commit]: ...

    async def list_pull_requests(
        self, tenant_id: str, repo: str, cursor: SyncCursor | None = None
    ) -> list[PullRequest]: ...

    async def list_pull_requests_for(self, author: UserRef) -> list[PullRequest]: ...
