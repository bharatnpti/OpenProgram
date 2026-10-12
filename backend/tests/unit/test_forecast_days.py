"""The forecast kept per day: the hourly run, the reconstruction, and the history read on them.

The history read reads the kept rows instead of replaying every day, so it must
say exactly what the replay said on the days a reconstruction rebuilt, and a day
the hourly run kept on the day stays as it was kept.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.delivery_service import DeliveryService
from core.application.forecast_service import ForecastService
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    ForecastDayRecord,
    ReleaseMatch,
    ReleaseMatchKind,
    Verdict,
    changes_until,
)
from core.domain.workflows import SyncDispatchInput
from infra.persistence.in_memory_forecast import InMemoryForecastDayRepository
from infra.registry import ServiceRegistry
from infra.workflows import delivery_reports
from infra.workflows.dispatch import sync_workflow_input
from tests.unit.test_forecast import (
    NOW,
    TENANT,
    TODAY,
    _as,
    _keep_history,
    _project_scope,
    _registry,
    _seed,
)


def _service(
    registry: ServiceRegistry, *, kept: InMemoryForecastDayRepository | None
) -> ForecastService:
    """The forecast service on the registry's stores, with or without the kept rows."""
    delivery = DeliveryService(
        graph_repository=registry.graph_repository(),
        settings_repository=registry.delivery_settings_repository(),
        snapshot_repository=registry.requirements_snapshot_repository(),
        release_repository=registry.release_repository(),
        clock=lambda: NOW,
        today=lambda: TODAY,
    )
    return ForecastService(
        graph_repository=registry.graph_repository(),
        delivery_service=delivery,
        commitment_repository=registry.commitment_repository(),
        release_repository=registry.release_repository(),
        time_series_repository=registry.time_series_repository(),
        forecast_day_repository=kept,
        clock=lambda: NOW,
        today=lambda: TODAY,
        new_id=lambda: "rel-1",
    )


def _days(view: object) -> list[tuple[date, date | None, date | None, int]]:
    return [(item.day, item.p50, item.p85, item.sample_days) for item in view.days]  # type: ignore[attr-defined]


async def test_the_history_read_on_reconstructed_days_says_what_the_replay_said() -> None:
    registry, _store = await _registry()
    await _keep_history(registry)
    replayed = await _service(registry, kept=None).forecast_history(
        TENANT, "checkout", TODAY, days=30
    )
    kept = InMemoryForecastDayRepository()
    service = _service(registry, kept=kept)

    summary = await service.reconstruct_forecast_days(TENANT, TODAY, days=30)
    read = await service.forecast_history(TENANT, "checkout", TODAY, days=30)

    assert summary.first_day == TODAY - timedelta(days=30)
    assert summary.last_day == TODAY - timedelta(days=1)
    # The project and its two pods (Payments and Storefront).
    assert summary.scopes == 3 and summary.kept == 0
    # One row per scope and working day with a snapshot in the 30 days before today.
    assert summary.written == 3 * (len(read.days) - 1)
    assert _days(read) == _days(replayed)
    assert any(item.p50 is not None for item in read.days)
    assert all(item.reconstructed for item in read.days[:-1])
    # Today is always live, never read from a row.
    assert read.days[-1].day == TODAY and not read.days[-1].reconstructed


async def test_a_releases_reconstructed_days_count_only_its_own_requirements() -> None:
    registry, _store = await _registry()
    plain = _service(registry, kept=None)
    release = await plain.save_release(
        TENANT,
        "checkout",
        release_id=None,
        name="Release 1",
        match=ReleaseMatch(kind=ReleaseMatchKind.FIX_VERSION, value="R1"),
        actor="po",
    )
    await _keep_history(registry, release_id=release.release_id)
    replayed = await plain.forecast_history(
        TENANT, "checkout", TODAY, release_id=release.release_id, days=20
    )
    service = _service(registry, kept=InMemoryForecastDayRepository())

    summary = await service.reconstruct_forecast_days(TENANT, TODAY, days=20)
    read = await service.forecast_history(
        TENANT, "checkout", TODAY, release_id=release.release_id, days=20
    )

    # The project, its release and its two pods.
    assert summary.scopes == 4
    assert _days(read) == _days(replayed)
    assert all(item.reconstructed for item in read.days[:-1])


async def test_a_reconstructed_day_is_measured_against_the_date_committed_by_then() -> None:
    registry, _store = await _registry()
    await _keep_history(registry)
    kept = InMemoryForecastDayRepository()
    service = _service(registry, kept=kept)
    committed_on = TODAY - timedelta(days=7)  # a Monday
    await registry.commitment_repository().append(
        DateChange(
            tenant_id=TENANT,
            scope=_project_scope(),
            target_date=date(2027, 3, 1),
            changed_at=datetime.combine(committed_on, datetime.min.time(), tzinfo=UTC)
            + timedelta(hours=10),
            changed_by="po",
        )
    )

    await service.reconstruct_forecast_days(TENANT, TODAY, days=14)
    rows = {
        row.day: row
        for row in await kept.list_between(
            TENANT, _project_scope(), TODAY - timedelta(days=14), TODAY
        )
    }

    before = rows[committed_on - timedelta(days=3)]  # the Friday before
    after = rows[committed_on]
    assert (before.target_date, before.verdict) == (None, Verdict.NO_DATE)
    assert after.target_date == date(2027, 3, 1)
    assert after.verdict is not Verdict.NO_DATE
    # The delivery view of that Friday reads today's date: the gap the rows close.
    view = await service.project_delivery(TENANT, "checkout", before.day)
    assert view.project.target == date(2027, 3, 1)


async def test_a_reconstructed_day_counts_only_the_etas_stated_by_then() -> None:
    registry, _store = await _registry()
    await _keep_history(registry)
    kept = InMemoryForecastDayRepository()
    service = _service(registry, kept=kept)

    await service.reconstruct_forecast_days(TENANT, TODAY, days=5)
    (row, *_rest) = await kept.list_between(
        TENANT, _project_scope(), TODAY - timedelta(days=5), TODAY
    )

    # CHK-3's ETA (4 Nov) was stated today: earlier days had only CHK-2's due date.
    assert (row.team_latest, row.team_latest_key) == (date(2026, 10, 30), "CHK-2")
    # The delivery view of that day counts the ETA stated since.
    that_day = (await service.project_delivery(TENANT, "checkout", row.day)).project.team
    assert (that_day.latest, that_day.latest_key) == (date(2026, 11, 4), "CHK-3")


async def test_a_day_the_hourly_run_kept_is_never_replaced_by_a_reconstruction() -> None:
    registry, _store = await _registry()
    await _keep_history(registry)
    kept = InMemoryForecastDayRepository()
    service = _service(registry, kept=kept)
    friday = TODAY - timedelta(days=3)

    recorded = await service.record_forecast_days(TENANT, friday)
    first = await service.reconstruct_forecast_days(TENANT, TODAY, days=10)
    again = await service.reconstruct_forecast_days(TENANT, TODAY, days=10)
    rows = await kept.list_between(TENANT, _project_scope(), TODAY - timedelta(days=10), TODAY)

    # The project and its two pods, each kept on the Friday.
    assert recorded == 3
    assert (first.kept, again.kept) == (3, 3)
    assert first.written == again.written == 3 * (len(rows) - 1)
    (on_the_day,) = [row for row in rows if row.day == friday]
    assert not on_the_day.reconstructed
    assert all(row.reconstructed for row in rows if row.day != friday)


async def test_the_history_read_takes_a_kept_day_as_it_was_kept() -> None:
    registry, _store = await _registry()
    await _keep_history(registry)
    kept = InMemoryForecastDayRepository()
    service = _service(registry, kept=kept)
    friday = TODAY - timedelta(days=3)
    # A day kept under another minimum, so its forecast differs from today's replay.
    await kept.save(
        [
            ForecastDayRecord(
                tenant_id=TENANT,
                scope=_project_scope(),
                day=friday,
                p50=date(2026, 12, 1),
                p85=date(2026, 12, 15),
                verdict=Verdict.AT_RISK,
                target_date=date(2026, 12, 10),
                team_latest=None,
                team_latest_key=None,
                remaining=2,
                unit="requirements",
                sample_days=6,
                completed_in_sample=2,
                min_sample_days=5,
                computed_at=NOW,
            )
        ]
    )

    read = await service.forecast_history(TENANT, "checkout", TODAY, days=5)
    past = await service.forecast_history(TENANT, "checkout", friday, days=1)

    (day,) = [item for item in read.days if item.day == friday]
    assert (day.p50, day.p85, day.sample_days, day.reconstructed) == (
        date(2026, 12, 1),
        date(2026, 12, 15),
        6,
        False,
    )
    # A past day asked for itself reads its own row too.
    assert _days(past) == [(friday, date(2026, 12, 1), date(2026, 12, 15), 6)]


async def test_the_hourly_run_keeps_todays_forecast_and_a_later_run_replaces_it() -> None:
    registry, _store = await _registry()
    await _keep_history(registry)
    kept = InMemoryForecastDayRepository()
    service = _service(registry, kept=kept)

    await service.record_forecast_days(TENANT, TODAY)
    await service.set_date(TENANT, _project_scope(), date(2027, 3, 1), note="", actor="po")
    await service.record_forecast_days(TENANT, TODAY)
    (row,) = await kept.list_between(TENANT, _project_scope(), TODAY, TODAY)

    view = (await service.project_delivery(TENANT, "checkout", TODAY)).project
    assert (row.p50, row.p85, row.sample_days) == (
        view.history.p50,
        view.history.p85,
        view.history.sample_days,
    )
    assert (row.target_date, row.verdict) == (date(2027, 3, 1), view.verdict)
    assert (row.team_latest, row.team_latest_key) == (view.team.latest, view.team.latest_key)
    assert row.min_sample_days == 10 and not row.reconstructed


def test_changes_until_keeps_the_changes_made_before_the_cutoff() -> None:
    scope = _project_scope()
    early = DateChange(
        tenant_id=TENANT,
        scope=scope,
        target_date=date(2026, 12, 1),
        changed_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
        changed_by="po",
    )
    late = DateChange(
        tenant_id=TENANT,
        scope=scope,
        target_date=date(2026, 12, 8),
        changed_at=datetime(2026, 10, 2, 0, tzinfo=UTC),
        changed_by="po",
    )

    assert changes_until([early, late], datetime(2026, 10, 2, tzinfo=UTC)) == [early]


# --- The workflow and the admin dispatch -------------------------------------------------


async def test_the_snapshot_run_keeps_todays_forecast_and_rebuilds_when_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _store = await _registry()
    await _keep_history(registry)
    monkeypatch.setattr(delivery_reports, "_service_registry", lambda: registry)
    monkeypatch.setattr(registry, "_tenant_today", lambda: TODAY)
    observed = NOW.isoformat()

    hourly = await delivery_reports.run_delivery_snapshot_activity(
        delivery_reports.DeliverySnapshotInput(tenant_id=TENANT, observed_at=observed)
    )
    dispatched = await delivery_reports.run_delivery_snapshot_activity(
        delivery_reports.DeliverySnapshotInput(
            tenant_id=TENANT, observed_at=observed, reconstruct_days=7
        )
    )

    # The project and its two pods.
    assert (hourly.forecasts_recorded, hourly.forecast_days_reconstructed) == (3, 0)
    # The working days of the week before, for each of the three.
    assert dispatched.forecast_days_reconstructed == 15
    rows = await registry.forecast_day_repository().list_between(
        TENANT, _project_scope(), TODAY - timedelta(days=7), TODAY
    )
    assert [row.reconstructed for row in rows] == [True] * 5 + [False]


async def test_the_snapshot_run_still_stores_snapshots_when_the_forecast_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _store = await _registry()
    monkeypatch.setattr(delivery_reports, "_service_registry", lambda: registry)

    async def broken(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(ForecastService, "record_forecast_days", broken)
    result = await delivery_reports.run_delivery_snapshot_activity(
        delivery_reports.DeliverySnapshotInput(tenant_id=TENANT, observed_at=NOW.isoformat())
    )

    assert result.projects_recorded == 1 and result.forecasts_recorded == 0


def test_the_dispatch_carries_the_days_to_rebuild() -> None:
    workflow_input = sync_workflow_input(
        SyncDispatchInput(
            tenant_id=TENANT,
            connector="delivery_snapshot",
            scope="forecast-days",
            payload={"reconstruct_days": 30},
        )
    )

    assert isinstance(workflow_input, delivery_reports.DeliverySnapshotInput)
    assert workflow_input.reconstruct_days == 30


def test_api_only_an_admin_dispatches_the_reconstruction(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(update={"workflow_provider": "fake", "demo_mode": True})
    )
    with TestClient(app) as client:
        admin = client.post(
            "/admin/workflows/forecast-days/reconstruct", json={"days": 30}, headers=_as("admin")
        )
        default = client.post(
            "/admin/workflows/forecast-days/reconstruct", json={}, headers=_as("admin")
        )
        manager = client.post(
            "/admin/workflows/forecast-days/reconstruct", json={}, headers=_as("mgr")
        )
        too_many = client.post(
            "/admin/workflows/forecast-days/reconstruct", json={"days": 366}, headers=_as("admin")
        )

    assert admin.status_code == 200, admin.text
    assert admin.json()["workflow_id"]
    assert default.status_code == 200
    assert manager.status_code == 403
    assert too_many.status_code == 422


def test_api_the_history_says_which_days_were_rebuilt(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    with TestClient(app) as client:
        _seed(client)
        registry: ServiceRegistry = app.state.registry
        today = registry._tenant_today()
        scope = CommitmentScope(
            kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout"
        )
        yesterday = today - timedelta(days=1)

        async def keep() -> None:
            await registry.forecast_day_repository().save(
                [
                    ForecastDayRecord(
                        tenant_id=TENANT,
                        scope=scope,
                        day=yesterday,
                        p50=None,
                        p85=None,
                        verdict=Verdict.NOT_ENOUGH_DATA,
                        target_date=None,
                        team_latest=None,
                        team_latest_key=None,
                        remaining=1,
                        unit="requirements",
                        sample_days=0,
                        completed_in_sample=0,
                        min_sample_days=10,
                        computed_at=NOW,
                        reconstructed=True,
                    )
                ]
            )

        client.portal.call(keep)  # type: ignore[union-attr]
        read = client.get(
            f"/projects/checkout/delivery/history?days=2&as_of={today.isoformat()}",
            headers=_as("po"),
        )

    assert read.status_code == 200, read.text
    days = read.json()["days"]
    assert [(item["day"], item["reconstructed"]) for item in days] == [
        (yesterday.isoformat(), True),
        (today.isoformat(), False),
    ]
