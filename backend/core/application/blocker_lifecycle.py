"""Write-side orchestration of the blocker lifecycle.

The collector and the self-status service fetch the open set, reconcile one
batch of reports against it (pure domain logic), and persist the outcome
atomically with the day's :class:`DeveloperStatus`. This module also owns the
migration shim: a developer with no lifecycle rows yet gets their legacy
status strings synthesized as carry-forward rows, which are persisted on
their next status write so reads flip to the lifecycle table cleanly.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from uuid import uuid4

from core.domain.blockers import (
    BlockerReconciliation,
    BlockerReport,
    BlockerSource,
    DeveloperBlocker,
    ReconcileMode,
    normalize_blocker_key,
    reconcile_blockers,
    unattributed_blockers,
)
from core.domain.graph import GraphNode
from core.domain.status import CheckInSignals, DeveloperStatus
from core.ports.repositories import GraphRepository, StatusRepository

_SYNTHETIC_BLOCKER = "no confirmed reply"


class BlockerLifecycleService:
    def __init__(
        self,
        status_repository: StatusRepository,
        graph_repository: GraphRepository | None = None,
    ) -> None:
        self._status_repository = status_repository
        self._graph_repository = graph_repository

    async def open_blockers(
        self,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        *,
        legacy_status: DeveloperStatus | None = None,
    ) -> tuple[DeveloperBlocker, ...]:
        """Open lifecycle rows, or shim rows synthesized from legacy strings.

        Shim rows are unsaved: reconciliation persists every one that remains
        open so the developer's read path flips to the lifecycle table on
        their first status write.
        """
        if await self._status_repository.has_blocker_rows(tenant_id, developer_id):
            return tuple(
                await self._status_repository.open_blockers(tenant_id, developer_id, as_of)
            )
        if legacy_status is None:
            legacy_status = await self._status_repository.latest_developer_status(
                tenant_id, developer_id, as_of
            )
        return _shim_rows(tenant_id, developer_id, legacy_status)

    async def pods_for_developer(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> tuple[GraphNode, ...]:
        if self._graph_repository is None:
            return ()
        return tuple(
            await self._graph_repository.pods_containing_developer(tenant_id, developer_id, as_of)
        )

    async def reconcile(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        prior: tuple[DeveloperBlocker, ...],
        signals: CheckInSignals,
        mode: ReconcileMode,
        source: BlockerSource,
        source_correlation_id: str | None = None,
        overwrite_attribution: bool = False,
    ) -> BlockerReconciliation:
        pods = await self.pods_for_developer(tenant_id, developer_id, as_of)
        reports = tuple(_normalize_pod_hint(report, pods) for report in signals.blocker_reports)
        reconciliation = reconcile_blockers(
            prior,
            reports,
            tenant_id=tenant_id,
            developer_id=developer_id,
            as_of=as_of,
            mode=mode,
            source=source,
            resolved_blocker_ids=signals.resolved_blocker_ids,
            source_correlation_id=source_correlation_id,
            overwrite_attribution=overwrite_attribution,
        )
        return _persist_shim_carries(reconciliation)

    async def persist_with_status(
        self, status: DeveloperStatus, reconciliation: BlockerReconciliation
    ) -> None:
        await self._status_repository.record_developer_status_with_blockers(
            status, reconciliation.upserts
        )

    @staticmethod
    def unattributed(
        blockers: tuple[DeveloperBlocker, ...],
    ) -> tuple[DeveloperBlocker, ...]:
        return unattributed_blockers(blockers)


def open_blocker_descriptions(reconciliation: BlockerReconciliation) -> tuple[str, ...]:
    return tuple(blocker.description for blocker in reconciliation.open_after)


def reconciliation_with_updates(
    reconciliation: BlockerReconciliation,
    updates: tuple[DeveloperBlocker, ...],
) -> BlockerReconciliation:
    """Swap updated rows in by blocker id, ensuring each lands in ``upserts``.

    Used for auto-attribution and attribution-asked stamps, which must persist
    even for rows that reconcile classified as untouched carries.
    """
    if not updates:
        return reconciliation
    by_id = {blocker.blocker_id: blocker for blocker in updates}

    def swap(items: tuple[DeveloperBlocker, ...]) -> tuple[DeveloperBlocker, ...]:
        return tuple(by_id.get(blocker.blocker_id, blocker) for blocker in items)

    upserts = swap(reconciliation.upserts)
    present = {blocker.blocker_id for blocker in upserts}
    extras = tuple(blocker for blocker in updates if blocker.blocker_id not in present)
    return replace(
        reconciliation,
        open_after=swap(reconciliation.open_after),
        upserts=upserts + extras,
        minted=swap(reconciliation.minted),
        carried=swap(reconciliation.carried),
    )


def _shim_rows(
    tenant_id: str, developer_id: str, status: DeveloperStatus | None
) -> tuple[DeveloperBlocker, ...]:
    if status is None:
        return ()
    descriptions = [
        description
        for description in status.blockers
        if normalize_blocker_key(description) != _SYNTHETIC_BLOCKER
    ]
    return tuple(
        DeveloperBlocker(
            tenant_id=tenant_id,
            blocker_id=f"shim:{developer_id}:{index}",
            developer_id=developer_id,
            description=description,
            normalized_key=normalize_blocker_key(description),
            source=BlockerSource.CARRY_FORWARD,
            first_seen_on=status.as_of,
            last_seen_on=status.as_of,
        )
        for index, description in enumerate(descriptions, start=1)
    )


def _persist_shim_carries(reconciliation: BlockerReconciliation) -> BlockerReconciliation:
    """Shim rows that stay open must be written even when untouched.

    Otherwise the first lifecycle write would flip reads to the table while a
    carried legacy blocker exists only in the compat strings. Shim ids are
    reminted once so ``open_after``/``carried``/``upserts`` stay consistent.
    """
    reminted = {
        blocker.blocker_id: replace(blocker, blocker_id=uuid4().hex)
        for blocker in reconciliation.carried
        if blocker.blocker_id.startswith("shim:")
    }
    if not reminted:
        return reconciliation

    def swap(items: tuple[DeveloperBlocker, ...]) -> tuple[DeveloperBlocker, ...]:
        return tuple(reminted.get(blocker.blocker_id, blocker) for blocker in items)

    return replace(
        reconciliation,
        carried=swap(reconciliation.carried),
        open_after=swap(reconciliation.open_after),
        upserts=reconciliation.upserts + tuple(reminted.values()),
    )


def _normalize_pod_hint(report: BlockerReport, pods: tuple[GraphNode, ...]) -> BlockerReport:
    """Map a free-text pod hint to a known pod id; drop unmatched hints."""
    if report.pod_id is None:
        return report
    hint = report.pod_id.strip()
    for pod in pods:
        if pod.id == hint:
            return report
    folded = hint.casefold()
    for pod in pods:
        if pod.name.casefold() == folded or pod.id.casefold() == folded:
            return replace(report, pod_id=pod.id)
    return replace(report, pod_id=None)
