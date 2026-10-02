from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.application.sync_recording import recording_sync_failure, succeeded_cursor
from core.domain.workflows import DirectorySyncResult
from core.ports.directory import DirectoryProvider, DirectoryUserRepository
from core.ports.repositories import SyncCursorRepository

DIRECTORY_SYNC_CONNECTOR = "directory"
DIRECTORY_SYNC_SCOPE = "workspace"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class DirectorySyncService:
    provider: DirectoryProvider
    repository: DirectoryUserRepository
    # Optional so callers that only need the import keep working; the registry
    # always passes one, which is what lets the admin view say when the
    # directory last synced and whether the last run failed.
    cursor_repository: SyncCursorRepository | None = None
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def sync(self, tenant_id: str) -> DirectorySyncResult:
        if self.cursor_repository is None:
            return await self._sync(tenant_id)
        attempted_at = self.clock()
        cursor = await self.cursor_repository.get_cursor(
            tenant_id, DIRECTORY_SYNC_CONNECTOR, DIRECTORY_SYNC_SCOPE
        )
        async with recording_sync_failure(
            self.cursor_repository,
            tenant_id=tenant_id,
            connector=DIRECTORY_SYNC_CONNECTOR,
            scope=DIRECTORY_SYNC_SCOPE,
            cursor=cursor,
            attempted_at=attempted_at,
        ):
            result = await self._sync(tenant_id)
            await self.cursor_repository.record_cursor(
                tenant_id,
                DIRECTORY_SYNC_CONNECTOR,
                DIRECTORY_SYNC_SCOPE,
                succeeded_cursor(cursor, attempted_at, result.synced_count),
            )
        return result

    async def _sync(self, tenant_id: str) -> DirectorySyncResult:
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
