"""The tenant's "fill the day from Jira" switch, in memory (tests and the memory runtime)."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.domain.observed_day import FillDaySetting


@dataclass
class InMemoryFillDayRepository:
    _settings: dict[str, FillDaySetting] = field(default_factory=dict)

    async def get_fill_day(self, tenant_id: str) -> FillDaySetting | None:
        return self._settings.get(tenant_id)

    async def save_fill_day(self, setting: FillDaySetting) -> None:
        self._settings[setting.tenant_id] = setting
