"""Scheduled delivery work: requirement snapshots, and day reports that are due.

The snapshot run stores today's requirements of every project (a later run the
same day replaces the earlier one), which the requirements timeline and the day
reports' "moved since" read. The dispatch run checks every enabled day report
against its own local send time; each report claims its day before sending, so
a tick that fires twice, or is retried, never sends a second copy.

Imports stay light: the registry, and with it logging and providers, is only
reached inside an activity, so the workflow sandbox can import this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.domain.graph import NodeKind

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


@dataclass(frozen=True, kw_only=True)
class DeliverySnapshotInput:
    tenant_id: str
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class DeliverySnapshotResult:
    tenant_id: str
    day: str
    projects_recorded: int


@dataclass(frozen=True, kw_only=True)
class DayReportDispatchInput:
    tenant_id: str
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class DayReportDispatchResult:
    tenant_id: str
    status: str
    reports_sent: int


@dataclass(frozen=True, kw_only=True)
class GateScanInput:
    tenant_id: str
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class GateScanResult:
    tenant_id: str
    status: str
    issues_read: int
    items_suggested: int


async def run_gate_scan_activity(payload: GateScanInput) -> GateScanResult:
    """Read the Jira text of every requirement that changed since its last read."""
    registry = _service_registry()
    try:
        if not registry.settings.gate_scan_enabled:
            return GateScanResult(
                tenant_id=payload.tenant_id, status="disabled", issues_read=0, items_suggested=0
            )
        observed_at = _timestamp(payload.observed_at)
        day = observed_at.astimezone(_zone(registry.settings.tenant_default_timezone)).date()
        delivery = registry.delivery_service()
        gates = registry.gate_service()
        read = suggested = 0
        for project in await registry.graph_repository().list_nodes(
            payload.tenant_id, NodeKind.PROJECT
        ):
            tasks = await delivery.scope_tasks(payload.tenant_id, project.id, day)
            summary = await gates.scan_tasks(payload.tenant_id, tasks)
            read += summary.read
            suggested += summary.suggested_items
        return GateScanResult(
            tenant_id=payload.tenant_id, status="ok", issues_read=read, items_suggested=suggested
        )
    finally:
        await registry.close()


async def run_delivery_snapshot_activity(payload: DeliverySnapshotInput) -> DeliverySnapshotResult:
    registry = _service_registry()
    try:
        observed_at = _timestamp(payload.observed_at)
        day = observed_at.astimezone(_zone(registry.settings.tenant_default_timezone)).date()
        recorded = await registry.delivery_service().record_snapshots(payload.tenant_id, day)
        return DeliverySnapshotResult(
            tenant_id=payload.tenant_id, day=day.isoformat(), projects_recorded=recorded
        )
    finally:
        await registry.close()


async def run_day_report_dispatch_activity(
    payload: DayReportDispatchInput,
) -> DayReportDispatchResult:
    registry = _service_registry()
    try:
        # A schedule registered while reports were on outlives the switch.
        if not registry.settings.day_report_enabled:
            return DayReportDispatchResult(
                tenant_id=payload.tenant_id, status="disabled", reports_sent=0
            )
        runs = await registry.day_report_service().dispatch_due(
            payload.tenant_id, _timestamp(payload.observed_at)
        )
        return DayReportDispatchResult(
            tenant_id=payload.tenant_id, status="ok", reports_sent=len(runs)
        )
    finally:
        await registry.close()


def _timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
