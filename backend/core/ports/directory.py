from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from core.domain.directory import DirectoryUser


class DirectoryProvider(Protocol):
    async def fetch_users(self, tenant_id: str) -> list[DirectoryUser]: ...


class DirectoryUserRepository(Protocol):
    async def upsert_users(self, users: Sequence[DirectoryUser]) -> None: ...

    async def search(
        self,
        tenant_id: str,
        query: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> list[DirectoryUser]: ...

    async def count(self, tenant_id: str, query: str = "") -> int: ...

    async def get(self, tenant_id: str, external_id: str) -> DirectoryUser | None: ...

    async def deactivate_missing(self, tenant_id: str, seen_external_ids: Sequence[str]) -> int: ...
