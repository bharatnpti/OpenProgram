from __future__ import annotations

from typing import Protocol

from core.domain.integrations import PullRequest, UserRef


class VcsProvider(Protocol):
    async def list_pull_requests(self, tenant_id: str, repo: str) -> list[PullRequest]: ...

    async def list_pull_requests_for(self, author: UserRef) -> list[PullRequest]: ...
