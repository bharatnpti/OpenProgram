from __future__ import annotations

from typing import Protocol

from core.domain.integrations import BuildResult


class CiProvider(Protocol):
    async def latest_build(self, tenant_id: str, pipeline_id: str) -> BuildResult | None: ...

    async def list_recent_failures(self, tenant_id: str, repo: str) -> list[BuildResult]: ...
