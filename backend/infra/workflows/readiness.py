"""The hourly release readiness check, and the Jira hygiene scan inside the same tick.

Readiness checks every scope against the tenant's criteria; hygiene checks each
scope's issues against the tenant's per-issue rules. Both read only Postgres
(synced issues, stages, dates) through the shared application pool, so the tick
adds no pool and calls no provider. Each run claims its slot (the tick's time)
before it starts, so a retried or doubled tick does nothing new. Each has its own
deployment switch and its own tenant switch: one off never stops the other, and
one failing never fails the other.

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
    #: The Jira hygiene scan's own status, in the same words.
    hygiene_status: str = "disabled"
    hygiene_changed: int = 0


async def run_readiness_scan_activity(payload: ReadinessScanInput) -> ReadinessScanResult:
    registry = _service_registry()
    try:
        settings = registry.settings
        slot = _timestamp(payload.observed_at).isoformat()
        status, scopes, changed = "disabled", 0, 0
        # A schedule registered while the scan was on outlives the switch.
        if settings.readiness_scan_enabled:
            summary = await registry.release_readiness_service().run_tenant(
                payload.tenant_id, slot=slot
            )
            if summary is None:
                status = "skipped"
            else:
                status, scopes, changed = summary.status.value, summary.scopes, summary.changed
        hygiene_status, hygiene_changed = "disabled", 0
        if settings.hygiene_scan_enabled:
            hygiene_status, hygiene_changed = await _run_hygiene(registry, payload.tenant_id, slot)
        return ReadinessScanResult(
            tenant_id=payload.tenant_id,
            status=status,
            scopes=scopes,
            changed=changed,
            hygiene_status=hygiene_status,
            hygiene_changed=hygiene_changed,
        )
    finally:
        await registry.close()


async def _run_hygiene(registry: ServiceRegistry, tenant_id: str, slot: str) -> tuple[str, int]:
    """The Jira hygiene scan for the tick's slot; a failure here never fails readiness."""
    try:
        summary = await registry.hygiene_service().run_tenant(tenant_id, slot=slot)
    except Exception as exc:  # noqa: BLE001 - readiness's result stands whatever hygiene does
        import structlog

        structlog.get_logger(__name__).warning(
            "jira_hygiene_tick_failed", tenant_id=tenant_id, error_type=type(exc).__name__
        )
        return "failed", 0
    if summary is None:
        return "skipped", 0
    return summary.status.value, summary.changed


def _timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC).replace(second=0, microsecond=0)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
