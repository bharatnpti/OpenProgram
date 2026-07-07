from __future__ import annotations

from datetime import UTC, date, datetime

from core.domain.status import DeveloperStatus, StatusSource
from core.ports.repositories import StatusRepository


class SelfStatusService:
    def __init__(self, status_repository: StatusRepository) -> None:
        self._status_repository = status_repository

    async def my_status(
        self,
        tenant_id: str,
        developer_id: str,
        as_of: date,
    ) -> DeveloperStatus | None:
        return await self._status_repository.latest_developer_status(
            tenant_id,
            developer_id,
            as_of,
        )

    async def confirm(
        self,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        confirmed_at: datetime | None = None,
    ) -> DeveloperStatus | None:
        existing = await self.my_status(tenant_id, developer_id, as_of)
        if existing is None:
            return None
        now = confirmed_at or datetime.now(tz=UTC)
        confirmed = DeveloperStatus(
            tenant_id=existing.tenant_id,
            developer_id=existing.developer_id,
            as_of=existing.as_of,
            source=StatusSource.CONFIRMED,
            blockers=existing.blockers,
            summary=existing.summary,
            eta_change_days=existing.eta_change_days,
            developer_confirmed=True,
            confirmed_at=now,
        )
        await self._status_repository.record_developer_status(confirmed)
        return confirmed

    async def correct(
        self,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        *,
        summary: str,
        blockers: tuple[str, ...],
        eta_change_days: int | None,
        confirmed_at: datetime | None = None,
    ) -> DeveloperStatus | None:
        existing = await self.my_status(tenant_id, developer_id, as_of)
        if existing is None:
            return None
        now = confirmed_at or datetime.now(tz=UTC)
        corrected = DeveloperStatus(
            tenant_id=existing.tenant_id,
            developer_id=existing.developer_id,
            as_of=existing.as_of,
            source=StatusSource.CONFIRMED,
            blockers=blockers,
            summary=summary,
            eta_change_days=eta_change_days,
            developer_confirmed=True,
            confirmed_at=now,
        )
        await self._status_repository.record_developer_status(corrected)
        return corrected
