from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from core.domain.forecast import (
    CommitmentScope,
    DateChange,
    ForecastDayRecord,
    ForecastSettings,
    Release,
)


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


class ForecastDayRepository(Protocol):
    """Each scope's forecast as it stood per day (forecast_days), one row per scope,
    project and day: a pod two projects share keeps its part of each under that project."""

    async def save(self, records: Sequence[ForecastDayRecord]) -> None:
        """Keep each record, replacing the scope's row for its project and day.

        A reconstructed record never replaces a row the hourly run kept; any
        other record replaces whatever is there. A key (tenant, scope kind, scope
        id, project, day) offered twice in one call keeps its last record and is
        warned about, never the whole call lost. A store that cannot take rows
        raises ForecastDaysNotKept with how many.
        """
        ...

    async def list_between(
        self, tenant_id: str, scope: CommitmentScope, start: date, end: date
    ) -> list[ForecastDayRecord]:
        """The scope's rows under its project (``scope.project_id``) from ``start`` to
        ``end``, oldest first: never a row the scope kept under another project."""
        ...

    async def fill_attribution(self, records: Sequence[ForecastDayRecord]) -> None:
        """Give each record's row its ``attribution`` where the row has none yet.

        Nothing else of the row changes: a day the hourly run kept before its
        breakdown was computed keeps its forecast and gains only the breakdown.
        """
        ...
