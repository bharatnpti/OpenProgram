from __future__ import annotations

from datetime import date
from typing import Protocol

from core.domain.delivery import DeliverySettings, RequirementsSnapshot


class DeliverySettingsRepository(Protocol):
    """The tenant's stage mapping. None until an admin saves one."""

    async def get(self, tenant_id: str) -> DeliverySettings | None: ...

    async def save(self, settings: DeliverySettings) -> None: ...


class RequirementsSnapshotRepository(Protocol):
    """One snapshot per project and day; a later one for the same day replaces it."""

    async def save(self, snapshot: RequirementsSnapshot) -> None: ...

    async def get(
        self, tenant_id: str, project_id: str, day: date
    ) -> RequirementsSnapshot | None: ...

    async def list_between(
        self, tenant_id: str, project_id: str, start: date, end: date
    ) -> list[RequirementsSnapshot]:
        """Snapshots from ``start`` to ``end`` inclusive, oldest first."""
        ...

    async def latest_before(
        self, tenant_id: str, project_id: str, day: date
    ) -> RequirementsSnapshot | None:
        """The newest snapshot strictly before ``day``."""
        ...
