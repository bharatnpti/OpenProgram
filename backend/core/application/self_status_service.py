from __future__ import annotations

from datetime import UTC, date, datetime

from core.application.blocker_lifecycle import BlockerLifecycleService
from core.application.blocker_resolution import BlockerResolutionService
from core.application.persona_views import BlockerDetailView
from core.domain.blockers import (
    BlockerReconciliation,
    BlockerReport,
    BlockerSource,
    ReconcileMode,
    blocker_descriptions,
)
from core.domain.status import CheckInSignals, DeveloperStatus, StatusSource
from core.ports.repositories import StatusRepository


class SelfStatusService:
    """The developer's own status view plus the UI confirm/correct writes.

    Corrections are total statements — the UI shows the full open blocker set
    before editing — so an omitted blocker resolves (deliberately unlike chat
    replies, which are partial statements and carry unmentioned blockers
    forward). Confirm re-asserts every open blocker today.
    """

    def __init__(
        self,
        status_repository: StatusRepository,
        blocker_lifecycle: BlockerLifecycleService | None = None,
        blocker_resolution: BlockerResolutionService | None = None,
    ) -> None:
        self._status_repository = status_repository
        self._blockers = blocker_lifecycle or BlockerLifecycleService(status_repository)
        self._blocker_resolution = blocker_resolution

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

    async def my_blocker_details(
        self,
        tenant_id: str,
        developer_id: str,
        as_of: date,
    ) -> tuple[BlockerDetailView, ...]:
        if self._blocker_resolution is None:
            return ()
        resolved = await self._blocker_resolution.open_blockers_for_developer(
            tenant_id, developer_id, as_of
        )
        return tuple(
            BlockerDetailView(
                blocker_id=blocker.blocker_id,
                description=blocker.description,
                work_item_id=blocker.work_item_ref.id if blocker.work_item_ref else None,
                work_item_name=None,
                pod_id=blocker.explicit_pod_ref.id if blocker.explicit_pod_ref else None,
                unattributed=blocker.unattributed,
                first_seen_on=blocker.first_seen_on,
                age_days=max((as_of - blocker.first_seen_on).days, 0),
            )
            for blocker in resolved
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
        reconciliation = await self._reconcile(
            tenant_id,
            developer_id,
            existing,
            reports=(),
            mode=ReconcileMode.CONFIRM_ALL,
        )
        confirmed = DeveloperStatus(
            tenant_id=existing.tenant_id,
            developer_id=existing.developer_id,
            as_of=existing.as_of,
            source=StatusSource.CONFIRMED,
            blockers=blocker_descriptions(reconciliation.open_after) or existing.blockers,
            summary=existing.summary,
            eta_change_days=existing.eta_change_days,
            developer_confirmed=True,
            confirmed_at=now,
        )
        await self._blockers.persist_with_status(confirmed, reconciliation)
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
        blocker_reports: tuple[BlockerReport, ...] | None = None,
        confirmed_at: datetime | None = None,
    ) -> DeveloperStatus | None:
        existing = await self.my_status(tenant_id, developer_id, as_of)
        if existing is None:
            return None
        now = confirmed_at or datetime.now(tz=UTC)
        # Structured items are authoritative when present; the legacy flat
        # strings keep their replace-set meaning otherwise.
        reports = (
            blocker_reports
            if blocker_reports is not None
            else tuple(BlockerReport(description=blocker) for blocker in blockers)
        )
        reconciliation = await self._reconcile(
            tenant_id,
            developer_id,
            existing,
            reports=reports,
            mode=ReconcileMode.AUTHORITATIVE_SET,
        )
        corrected = DeveloperStatus(
            tenant_id=existing.tenant_id,
            developer_id=existing.developer_id,
            as_of=existing.as_of,
            source=StatusSource.CONFIRMED,
            blockers=blocker_descriptions(reconciliation.open_after),
            summary=summary,
            eta_change_days=eta_change_days,
            developer_confirmed=True,
            confirmed_at=now,
        )
        await self._blockers.persist_with_status(corrected, reconciliation)
        return corrected

    async def _reconcile(
        self,
        tenant_id: str,
        developer_id: str,
        existing: DeveloperStatus,
        *,
        reports: tuple[BlockerReport, ...],
        mode: ReconcileMode,
    ) -> BlockerReconciliation:
        prior = await self._blockers.open_blockers(
            tenant_id,
            developer_id,
            existing.as_of,
            legacy_status=existing,
        )
        signals = CheckInSignals(
            progress_note=existing.summary,
            blockers=tuple(report.description for report in reports if not report.resolved),
            blocker_reports=reports,
        )
        return await self._blockers.reconcile(
            tenant_id=tenant_id,
            developer_id=developer_id,
            as_of=existing.as_of,
            prior=prior,
            signals=signals,
            mode=mode,
            source=BlockerSource.CORRECTION,
            # An explicit human edit may re-attribute an existing blocker.
            overwrite_attribution=True,
        )
