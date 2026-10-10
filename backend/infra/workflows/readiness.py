"""The hourly release readiness check: every scope against the tenant's criteria.

It reads only Postgres (synced issues, stages, dates) through the shared
application pool, so it adds no pool and calls no provider. The run claims its
slot (the tick's time) before it starts, so a retried or doubled tick does
nothing new; the tenant's own switch decides whether a tick checks anything.

Imports stay light: the registry, and with it logging and providers, is only
reached inside an activity, so the workflow sandbox can import this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


@dataclass(frozen=True, kw_only=True)
class ReadinessScanInput:
    tenant_id: str
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReadinessScanResult:
    tenant_id: str
    #: disabled (deployment switch), skipped (slot already ran, or never set up),
    #: or the run's own status: ok, off, failed.
    status: str
    scopes: int
    changed: int


async def run_readiness_scan_activity(payload: ReadinessScanInput) -> ReadinessScanResult:
    registry = _service_registry()
    try:
        # A schedule registered while the scan was on outlives the switch.
        if not registry.settings.readiness_scan_enabled:
            return ReadinessScanResult(
                tenant_id=payload.tenant_id, status="disabled", scopes=0, changed=0
            )
        slot = _timestamp(payload.observed_at).isoformat()
        summary = await registry.release_readiness_service().run_tenant(
            payload.tenant_id, slot=slot
        )
        if summary is None:
            return ReadinessScanResult(
                tenant_id=payload.tenant_id, status="skipped", scopes=0, changed=0
            )
        return ReadinessScanResult(
            tenant_id=payload.tenant_id,
            status=summary.status.value,
            scopes=summary.scopes,
            changed=summary.changed,
        )
    finally:
        await registry.close()


def _timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC).replace(second=0, microsecond=0)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
