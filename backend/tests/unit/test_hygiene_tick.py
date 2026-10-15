"""The readiness tick also runs the Jira hygiene scan, each on its own switch."""

from __future__ import annotations

import pytest

from config.settings import Settings
from core.domain.hygiene import HygieneSettings
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from infra.workflows import readiness as tick
from infra.workflows.readiness import ReadinessScanInput

TENANT = "demo"
SECRET = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="
AT = "2026-10-14T09:45:00+00:00"


def _registry(**switches: bool) -> ServiceRegistry:
    return ServiceRegistry(
        Settings(
            _env_file=None, secret_key=SECRET, runtime_mode="memory", tenant_id=TENANT, **switches
        ),
        graph_store=InMemoryGraphStore(),
    )


async def test_the_tick_runs_hygiene_beside_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry()
    await registry.hygiene_repository().save_settings(
        HygieneSettings(tenant_id=TENANT, enabled=True)
    )
    monkeypatch.setattr(tick, "_service_registry", lambda: registry)

    first = await tick.run_readiness_scan_activity(
        ReadinessScanInput(tenant_id=TENANT, observed_at=AT)
    )
    again = await tick.run_readiness_scan_activity(
        ReadinessScanInput(tenant_id=TENANT, observed_at=AT)
    )

    # Readiness was never set up for the tenant; hygiene was, and its slot runs once.
    assert (first.status, first.hygiene_status) == ("skipped", "ok")
    assert again.hygiene_status == "skipped"


async def test_readiness_off_does_not_stop_hygiene_and_hygiene_off_stops_only_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hygiene_only = _registry(readiness_scan_enabled=False)
    await hygiene_only.hygiene_repository().save_settings(
        HygieneSettings(tenant_id=TENANT, enabled=True)
    )
    monkeypatch.setattr(tick, "_service_registry", lambda: hygiene_only)
    first = await tick.run_readiness_scan_activity(
        ReadinessScanInput(tenant_id=TENANT, observed_at=AT)
    )

    readiness_only = _registry(hygiene_scan_enabled=False)
    monkeypatch.setattr(tick, "_service_registry", lambda: readiness_only)
    second = await tick.run_readiness_scan_activity(
        ReadinessScanInput(tenant_id=TENANT, observed_at=AT)
    )

    assert (first.status, first.hygiene_status) == ("disabled", "ok")
    assert (second.status, second.hygiene_status) == ("skipped", "disabled")


async def test_a_failing_hygiene_scan_never_fails_the_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry()

    def broken() -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(registry, "hygiene_service", broken)
    monkeypatch.setattr(tick, "_service_registry", lambda: registry)

    result = await tick.run_readiness_scan_activity(
        ReadinessScanInput(tenant_id=TENANT, observed_at=AT)
    )

    assert (result.status, result.hygiene_status) == ("skipped", "failed")
