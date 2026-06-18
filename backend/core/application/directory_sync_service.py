from __future__ import annotations

from dataclasses import dataclass

from core.domain.workflows import DirectorySyncResult
from core.ports.directory import DirectoryProvider, DirectoryUserRepository


@dataclass
class DirectorySyncService:
    provider: DirectoryProvider
    repository: DirectoryUserRepository

    async def sync(self, tenant_id: str) -> DirectorySyncResult:
        users = await self.provider.fetch_users(tenant_id)
        await self.repository.upsert_users(users)
        deactivated_count = await self.repository.deactivate_missing(
            tenant_id,
            [user.external_id for user in users],
        )
        return DirectorySyncResult(
            tenant_id=tenant_id,
            synced_count=len(users),
            deactivated_count=deactivated_count,
        )
