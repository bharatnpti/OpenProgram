from __future__ import annotations

from datetime import UTC, date, datetime

from core.application.self_status_service import SelfStatusService
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore


async def test_self_status_service_confirms_latest_status() -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 10),
            source=StatusSource.INFERRED,
            blockers=("dependency",),
            summary="Inferred status.",
            eta_change_days=1,
        )
    )
    confirmed_at = datetime(2026, 1, 10, 10, tzinfo=UTC)
    service = SelfStatusService(store)

    confirmed = await service.confirm("demo", "dev-1", date(2026, 1, 10), confirmed_at)

    assert confirmed == DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 10),
        source=StatusSource.CONFIRMED,
        blockers=("dependency",),
        summary="Inferred status.",
        eta_change_days=1,
        developer_confirmed=True,
        confirmed_at=confirmed_at,
    )
    assert await service.my_status("demo", "dev-1", date(2026, 1, 10)) == confirmed


async def test_self_status_service_corrects_structured_fields() -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 10),
            source=StatusSource.STALE,
            blockers=("old dependency",),
            summary="Old status.",
        )
    )
    confirmed_at = datetime(2026, 1, 10, 11, tzinfo=UTC)
    service = SelfStatusService(store)

    corrected = await service.correct(
        "demo",
        "dev-1",
        date(2026, 1, 10),
        summary="Corrected status.",
        blockers=("new dependency",),
        eta_change_days=3,
        confirmed_at=confirmed_at,
    )

    assert corrected is not None
    assert corrected.source is StatusSource.CONFIRMED
    assert corrected.developer_confirmed is True
    assert corrected.confirmed_at == confirmed_at
    assert corrected.summary == "Corrected status."
    assert corrected.blockers == ("new dependency",)
    assert corrected.eta_change_days == 3


async def test_self_status_service_returns_none_without_existing_status() -> None:
    service = SelfStatusService(InMemoryGraphStore())

    assert await service.my_status("demo", "dev-1", date(2026, 1, 10)) is None
    assert await service.confirm("demo", "dev-1", date(2026, 1, 10)) is None
    assert (
        await service.correct(
            "demo",
            "dev-1",
            date(2026, 1, 10),
            summary="Corrected status.",
            blockers=(),
            eta_change_days=None,
        )
        is None
    )
