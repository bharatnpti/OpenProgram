from __future__ import annotations

from typing import Protocol

from core.domain.forecast import CommitmentScope, DateChange, ForecastSettings, Release


class CommitmentRepository(Protocol):
    """Every change to a delivery date, kept in order; the latest is the current date."""

    async def append(self, change: DateChange) -> None: ...

    async def changes(self, tenant_id: str, scope: CommitmentScope) -> list[DateChange]:
        """The scope's changes, oldest first."""
        ...

    async def changes_for_project(self, tenant_id: str, project_id: str) -> list[DateChange]:
        """Every scope's changes within the project, oldest first."""
        ...


class ReleaseRepository(Protocol):
    async def list_for_project(self, tenant_id: str, project_id: str) -> list[Release]: ...

    async def list_all(self, tenant_id: str) -> list[Release]: ...

    async def get(self, tenant_id: str, release_id: str) -> Release | None: ...

    async def save(self, release: Release) -> None: ...

    async def delete(self, tenant_id: str, release_id: str) -> bool: ...


class ForecastSettingsRepository(Protocol):
    """The tenant's forecast settings. None until an admin saves one."""

    async def get(self, tenant_id: str) -> ForecastSettings | None: ...

    async def save(self, settings: ForecastSettings) -> None: ...
