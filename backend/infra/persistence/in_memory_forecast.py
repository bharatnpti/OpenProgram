from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date

from core.domain.forecast import (
    CommitmentScope,
    DateChange,
    ForecastDayKey,
    ForecastDayRecord,
    ForecastSettings,
    Release,
    forecast_day_key,
)
from infra.persistence.forecast_day_rows import distinct_forecast_days


@dataclass
class InMemoryCommitmentRepository:
    _changes: list[DateChange] = field(default_factory=list)

    async def append(self, change: DateChange) -> None:
        self._changes.append(change)

    async def changes(self, tenant_id: str, scope: CommitmentScope) -> list[DateChange]:
        return sorted(
            (c for c in self._changes if c.tenant_id == tenant_id and c.scope == scope),
            key=lambda change: change.changed_at,
        )

    async def changes_for_project(self, tenant_id: str, project_id: str) -> list[DateChange]:
        return sorted(
            (
                c
                for c in self._changes
                if c.tenant_id == tenant_id and c.scope.project_id == project_id
            ),
            key=lambda change: change.changed_at,
        )


@dataclass
class InMemoryReleaseRepository:
    _releases: dict[tuple[str, str], Release] = field(default_factory=dict)

    async def list_for_project(self, tenant_id: str, project_id: str) -> list[Release]:
        return [
            release
            for release in self._releases.values()
            if release.tenant_id == tenant_id and release.project_id == project_id
        ]

    async def list_all(self, tenant_id: str) -> list[Release]:
        return [release for release in self._releases.values() if release.tenant_id == tenant_id]

    async def get(self, tenant_id: str, release_id: str) -> Release | None:
        return self._releases.get((tenant_id, release_id))

    async def save(self, release: Release) -> None:
        self._releases[(release.tenant_id, release.release_id)] = release

    async def delete(self, tenant_id: str, release_id: str) -> bool:
        return self._releases.pop((tenant_id, release_id), None) is not None


@dataclass
class InMemoryForecastSettingsRepository:
    _settings: dict[str, ForecastSettings] = field(default_factory=dict)

    async def get(self, tenant_id: str) -> ForecastSettings | None:
        return self._settings.get(tenant_id)

    async def save(self, settings: ForecastSettings) -> None:
        self._settings[settings.tenant_id] = settings


@dataclass
class InMemoryForecastDayRepository:
    """forecast_days as Postgres keeps them: one row per (tenant, scope kind, scope id,
    project, day), so a pod two projects share has a row under each."""

    _rows: dict[ForecastDayKey, ForecastDayRecord] = field(default_factory=dict)

    async def save(self, records: Sequence[ForecastDayRecord]) -> None:
        # As Postgres keeps them: a key offered twice in one call keeps its last record.
        for record in distinct_forecast_days(records):
            key = forecast_day_key(record)
            kept = self._rows.get(key)
            if kept is not None and record.reconstructed and not kept.reconstructed:
                continue
            self._rows[key] = record

    async def fill_attribution(self, records: Sequence[ForecastDayRecord]) -> None:
        for record in records:
            key = forecast_day_key(record)
            kept = self._rows.get(key)
            if kept is not None and kept.attribution is None and record.attribution is not None:
                self._rows[key] = replace(kept, attribution=record.attribution)

    async def list_between(
        self, tenant_id: str, scope: CommitmentScope, start: date, end: date
    ) -> list[ForecastDayRecord]:
        return sorted(
            (
                row
                for (tenant, kind, scope_id, project_id, day), row in self._rows.items()
                if tenant == tenant_id
                and kind == scope.kind.value
                and scope_id == scope.id
                and project_id == scope.project_id
                and start <= day <= end
            ),
            key=lambda row: row.day,
        )
