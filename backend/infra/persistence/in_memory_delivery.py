from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from core.domain.delivery import DeliverySettings, RequirementsSnapshot


@dataclass
class InMemoryDeliverySettingsRepository:
    _settings: dict[str, DeliverySettings] = field(default_factory=dict)

    async def get(self, tenant_id: str) -> DeliverySettings | None:
        return self._settings.get(tenant_id)

    async def save(self, settings: DeliverySettings) -> None:
        self._settings[settings.tenant_id] = settings


@dataclass
class InMemoryRequirementsSnapshotRepository:
    _snapshots: dict[tuple[str, str, date], RequirementsSnapshot] = field(default_factory=dict)

    async def save(self, snapshot: RequirementsSnapshot) -> None:
        self._snapshots[(snapshot.tenant_id, snapshot.project_id, snapshot.day)] = snapshot

    async def get(self, tenant_id: str, project_id: str, day: date) -> RequirementsSnapshot | None:
        return self._snapshots.get((tenant_id, project_id, day))

    async def list_between(
        self, tenant_id: str, project_id: str, start: date, end: date
    ) -> list[RequirementsSnapshot]:
        return sorted(
            (
                snapshot
                for (tenant, project, day), snapshot in self._snapshots.items()
                if tenant == tenant_id and project == project_id and start <= day <= end
            ),
            key=lambda snapshot: snapshot.day,
        )

    async def latest_before(
        self, tenant_id: str, project_id: str, day: date
    ) -> RequirementsSnapshot | None:
        earlier = [
            snapshot
            for (tenant, project, snapshot_day), snapshot in self._snapshots.items()
            if tenant == tenant_id and project == project_id and snapshot_day < day
        ]
        return max(earlier, key=lambda snapshot: snapshot.day) if earlier else None
