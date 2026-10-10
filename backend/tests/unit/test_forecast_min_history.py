"""How many working days of history a forecast needs: the rule, its bounds, and who sets it.

Ten by default, from OPENPROGRAM_FORECAST_MIN_HISTORY_DAYS, and per tenant from
Admin. The window the samples are read from follows the minimum, so every
minimum that may be set can be reached; with ten nothing changes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.main import create_app
from config.settings import Settings
from core.application.forecast_service import MAX_FORECAST_HISTORY_DAYS, ForecastService
from core.domain.delivery import DEFAULT_STAGE_MAPPING, DeliverySettings, DeliveryStage
from core.domain.forecast import (
    HIGHEST_MIN_SAMPLE_DAYS,
    HISTORY_DAYS,
    LOWEST_MIN_SAMPLE_DAYS,
    MIN_SAMPLE_DAYS,
    CommitmentScopeKind,
    ForecastSettings,
    ForecastSettingsError,
    ReleaseMatch,
    ReleaseMatchKind,
    Verdict,
    daily_completions,
    history_forecast,
    history_window_days,
    validated_min_sample_days,
)
from core.domain.reports import render_text
from infra.persistence.in_memory_forecast import InMemoryForecastSettingsRepository
from infra.persistence.postgres_delivery import (
    PostgresDeliverySettingsRepository,
    PostgresForecastSettingsRepository,
    forecast_settings_from_json,
    forecast_settings_to_json,
)
from infra.registry import ServiceRegistry
from tests.fixtures.day_report_full_day import NOTE, WithVerdict, a_full_day
from tests.unit.test_forecast import NOW, TENANT, TODAY, _keep_history, _registry, _snapshot
from tests.unit.test_forecast import _service as _plain_service

S = DeliveryStage
SAMPLES = [0.0, 1.0, 2.0, 1.0, 0.0, 3.0, 1.0]  # seven working days


# --- The rule ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("minimum", "forecasts", "reason"),
    [
        (5, True, None),
        (10, False, "Only 7 working days of history; a forecast needs 10."),
        (15, False, "Only 7 working days of history; a forecast needs 15."),
    ],
)
def test_a_forecast_waits_for_the_minimum_it_is_given(
    minimum: int, forecasts: bool, reason: str | None
) -> None:
    forecast = history_forecast(SAMPLES, 6, start=TODAY, seed="checkout", min_sample_days=minimum)

    assert (forecast.p50 is not None, forecast.p85 is not None) == (forecasts, forecasts)
    assert forecast.reason == reason
    assert (forecast.sample_days, forecast.needed_days) == (7, minimum)


@pytest.mark.parametrize("minimum", [5, 10, 15])
def test_with_no_history_the_reason_names_the_minimum(minimum: int) -> None:
    forecast = history_forecast([], 6, start=TODAY, seed="x", min_sample_days=minimum)

    assert forecast.reason == (
        f"No history yet: a forecast needs {minimum} working days of daily snapshots."
    )


def test_ten_is_the_default_and_forecasts_exactly_as_before() -> None:
    samples = SAMPLES + [2.0, 0.0, 1.0, 1.0]

    assert MIN_SAMPLE_DAYS == 10
    assert history_forecast(samples, 9, start=TODAY, seed="checkout") == history_forecast(
        samples, 9, start=TODAY, seed="checkout", min_sample_days=10
    )
    assert history_forecast(SAMPLES, 9, start=TODAY, seed="x").reason == (
        "Only 7 working days of history; a forecast needs 10."
    )


def test_the_window_stays_thirty_days_until_the_minimum_needs_more() -> None:
    assert history_window_days(MIN_SAMPLE_DAYS) == HISTORY_DAYS == 30
    assert history_window_days(LOWEST_MIN_SAMPLE_DAYS) == 30
    assert history_window_days(19) == 30
    assert history_window_days(20) == 31
    assert history_window_days(25) == 38
    assert history_window_days(HIGHEST_MIN_SAMPLE_DAYS) == 87
    windows = [history_window_days(days) for days in range(3, HIGHEST_MIN_SAMPLE_DAYS + 1)]
    assert windows == sorted(windows)
    # The history read replays a day's window on top of the days it shows.
    assert history_window_days(HIGHEST_MIN_SAMPLE_DAYS) <= MAX_FORECAST_HISTORY_DAYS


@pytest.mark.parametrize("minimum", range(LOWEST_MIN_SAMPLE_DAYS, HIGHEST_MIN_SAMPLE_DAYS + 1))
def test_every_minimum_can_be_reached_wherever_the_weekend_falls(minimum: int) -> None:
    window = history_window_days(minimum)
    for weekday in range(7):
        as_of = TODAY + timedelta(days=weekday)
        # A snapshot every day of the window before as_of, and as_of: what the forecast reads.
        series = [
            _snapshot(as_of - timedelta(days=offset), {"A": S.IN_DEVELOPMENT})
            for offset in range(window, -1, -1)
        ]
        assert len(daily_completions(series)) >= minimum, (minimum, as_of)
        # The requirements read's timeline of `window` days up to as_of holds as many.
        assert _working_days(as_of, window) - 1 >= minimum, (minimum, as_of)
    if window > HISTORY_DAYS:
        # No longer than it has to be: a day less misses the minimum on some weekday.
        assert any(
            _working_days(TODAY + timedelta(days=weekday), window - 1) - 1 < minimum
            for weekday in range(7)
        )


def _working_days(as_of: object, days: int) -> int:
    """The working days among the ``days`` calendar days up to ``as_of``."""
    assert isinstance(as_of, type(TODAY))
    return sum(1 for offset in range(days) if (as_of - timedelta(days=offset)).weekday() < 5)


def test_the_minimum_is_held_between_three_and_sixty_in_plain_words() -> None:
    assert validated_min_sample_days(3) == 3
    assert validated_min_sample_days(60) == 60
    with pytest.raises(ForecastSettingsError) as low:
        validated_min_sample_days(2)
    with pytest.raises(ForecastSettingsError) as high:
        validated_min_sample_days(61)

    assert str(low.value) == (
        "A forecast needs at least 3 working days of history: with fewer, its 50% and 85% "
        "dates replay the same one or two days."
    )
    assert str(high.value) == (
        "A forecast can wait for at most 60 working days of history, about three months."
    )


def test_the_deployment_default_comes_from_the_environment_and_is_checked_at_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="
    assert Settings(_env_file=None, secret_key=key).forecast_min_history_days == 10
    monkeypatch.setenv("OPENPROGRAM_FORECAST_MIN_HISTORY_DAYS", "15")
    assert Settings(_env_file=None, secret_key=key).forecast_min_history_days == 15
    monkeypatch.setenv("OPENPROGRAM_FORECAST_MIN_HISTORY_DAYS", "61")
    with pytest.raises(ValidationError, match="at most 60 working days"):
        Settings(_env_file=None, secret_key=key)
    monkeypatch.setenv("OPENPROGRAM_FORECAST_MIN_HISTORY_DAYS", "1")
    with pytest.raises(ValidationError, match="at least 3 working days"):
        Settings(_env_file=None, secret_key=key)


# --- The service passes the tenant's minimum through ---------------------------------------


async def _service_with(
    minimum: int | None, *, default: int = MIN_SAMPLE_DAYS
) -> tuple[ServiceRegistry, ForecastService]:
    """The test tenant's forecast service, with ``minimum`` saved by an admin (None: none)."""
    registry, _store = await _registry()
    plain = _plain_service(registry)
    repository = InMemoryForecastSettingsRepository()
    if minimum is not None:
        await repository.save(
            ForecastSettings(
                tenant_id=TENANT, min_sample_days=minimum, updated_at=NOW, updated_by="admin"
            )
        )
    service = ForecastService(
        graph_repository=registry.graph_repository(),
        delivery_service=plain._delivery,
        commitment_repository=registry.commitment_repository(),
        release_repository=registry.release_repository(),
        time_series_repository=registry.time_series_repository(),
        settings_repository=repository,
        default_min_sample_days=default,
        clock=lambda: NOW,
        today=lambda: TODAY,
        new_id=lambda: "rel-1",
    )
    return registry, service


async def test_every_scope_forecasts_with_the_tenants_minimum() -> None:
    _registry_5, service = await _service_with(5)
    await service.save_release(
        TENANT,
        "checkout",
        release_id=None,
        name="Release 1",
        match=ReleaseMatch(kind=ReleaseMatchKind.FIX_VERSION, value="R1"),
        actor="po",
    )

    view = await service.project_delivery(TENANT, "checkout", TODAY)

    scopes = [view.project, *view.pods, *view.releases]
    assert {scope.scope.kind for scope in scopes} == set(CommitmentScopeKind)
    assert all(scope.history.needed_days == 5 for scope in scopes)
    assert (
        "No history yet: a forecast needs 5 working days of daily snapshots."
        in view.project.reasons
    )
    history = await service.forecast_history(TENANT, "checkout", TODAY, days=5)
    assert history.needed_days == 5


async def test_a_higher_minimum_holds_the_forecast_back_and_its_history_agrees() -> None:
    registry_15, fifteen = await _service_with(15)
    await _keep_history(registry_15)
    registry_21, longer = await _service_with(21)
    await _keep_history(registry_21)

    at_fifteen = (await fifteen.project_delivery(TENANT, "checkout", TODAY)).project.history
    at_21 = (await longer.project_delivery(TENANT, "checkout", TODAY)).project.history

    # Four weeks of snapshots and today: 20 working days whose day before was kept.
    assert at_fifteen.p50 is not None and at_fifteen.needed_days == 15
    assert at_21.p50 is None and at_21.sample_days == 20
    assert at_21.reason == "Only 20 working days of history; a forecast needs 21."
    for service, today in ((fifteen, at_fifteen), (longer, at_21)):
        replay = await service.forecast_history(TENANT, "checkout", TODAY, days=10)
        assert (replay.days[-1].p50, replay.days[-1].sample_days) == (
            today.p50,
            today.sample_days,
        )


async def test_the_window_grows_so_a_long_minimum_reads_old_enough_snapshots() -> None:
    registry, service = await _service_with(25)
    snapshots = registry.requirements_snapshot_repository()
    window = history_window_days(25)
    done: dict[str, DeliveryStage] = {}
    for offset in range(window, 0, -1):
        day = TODAY - timedelta(days=offset)
        if day.weekday() < 5:
            done[f"OLD-{offset}"] = S.PRODUCTION
            await snapshots.save(_snapshot(day, {**done, "CHK-2": S.IN_DEVELOPMENT}))

    history = (await service.project_delivery(TENANT, "checkout", TODAY)).project.history

    assert history.sample_days >= 25 and history.p50 is not None


async def test_the_default_applies_until_an_admin_sets_one_and_after_a_reset() -> None:
    _registry_12, service = await _service_with(None, default=12)

    before = await service.forecast_settings(TENANT)
    saved = await service.save_forecast_settings(TENANT, 20, actor="admin")
    reset = await service.save_forecast_settings(TENANT, None, actor="admin")

    assert (before.min_sample_days, before.is_default, before.window_days) == (12, True, 30)
    assert (saved.min_sample_days, saved.is_default, saved.window_days) == (20, False, 31)
    assert (saved.updated_by, saved.updated_at) == ("admin", NOW)
    assert (reset.min_sample_days, reset.is_default) == (12, True)
    with pytest.raises(ForecastSettingsError):
        await service.save_forecast_settings(TENANT, 61, actor="admin")
    assert (await service.forecast_settings(TENANT)).min_sample_days == 12


async def test_the_day_report_says_the_tenants_minimum_and_only_then_changes() -> None:
    registry = await a_full_day()
    await registry.forecast_settings_repository().save(
        ForecastSettings(tenant_id=TENANT, min_sample_days=15, updated_at=NOW, updated_by="a")
    )
    builder = registry.day_report_builder()
    builder._forecast = WithVerdict(builder._forecast, Verdict.AT_RISK)  # type: ignore[assignment]
    report = await builder.build(TENANT, "checkout", TODAY, report_id="rep-1", note=NOTE)

    assert report.facts is not None and report.facts.delivery is not None
    assert (report.facts.delivery.history_days, report.facts.delivery.history_needed) == (1, 15)
    assert "• Only 1 working day of history; a forecast needs 15." in render_text(report)


# --- Storage -------------------------------------------------------------------------------


@dataclass
class _Executor:
    rows: list[dict[str, object]] = field(default_factory=list)
    calls: list[tuple[str, Sequence[object]]] = field(default_factory=list)

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        self.calls.append((query, params))
        return object()

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        self.calls.append((query, params))
        return self.rows


def test_the_settings_round_trip_through_json() -> None:
    settings = ForecastSettings(
        tenant_id=TENANT, min_sample_days=15, updated_at=NOW, updated_by="admin"
    )
    reset = ForecastSettings(tenant_id=TENANT, min_sample_days=None, updated_at=NOW, updated_by="")
    raw = json.loads(json.dumps(forecast_settings_to_json(settings)))

    assert forecast_settings_from_json(TENANT, raw) == settings
    assert forecast_settings_from_json(TENANT, forecast_settings_to_json(reset)) == reset
    assert forecast_settings_from_json(TENANT, {}) is None


async def test_the_settings_ride_in_the_delivery_settings_row_beside_the_mapping() -> None:
    executor = _Executor()
    settings = ForecastSettings(
        tenant_id=TENANT, min_sample_days=15, updated_at=NOW, updated_by="admin"
    )

    await PostgresForecastSettingsRepository(executor).save(settings)
    query, params = executor.calls[0]
    # Saving them merges one key into the row and leaves the mapping's own columns alone.
    assert "INSERT INTO delivery_settings" in query
    assert "|| jsonb_build_object('forecast', %s::jsonb)" in " ".join(query.split())
    assert "updated_at = " not in query.split("DO UPDATE SET", 1)[1]
    assert json.loads(str(params[1]))["min_history_days"] == 15

    executor.rows = [{"forecast": json.loads(str(params[1]))}]
    assert await PostgresForecastSettingsRepository(executor).get(TENANT) == settings
    executor.rows = [{"forecast": None}]
    assert await PostgresForecastSettingsRepository(executor).get(TENANT) is None


async def test_a_row_with_only_forecast_settings_holds_no_stage_mapping() -> None:
    executor = _Executor(
        rows=[
            {
                "tenant_id": TENANT,
                "mapping": {"forecast": {"min_history_days": 5}},
                "updated_at": NOW,
                "updated_by": "admin",
            }
        ]
    )
    repository = PostgresDeliverySettingsRepository(executor)

    assert await repository.get(TENANT) is None

    await repository.save(
        DeliverySettings(tenant_id=TENANT, mapping=DEFAULT_STAGE_MAPPING, updated_at=NOW)
    )
    query, _params = executor.calls[-1]
    # Saving the mapping keeps the forecast settings the row holds.
    assert "THEN EXCLUDED.mapping || jsonb_build_object(" in query
    assert "delivery_settings.mapping -> 'forecast'" in query


# --- API -----------------------------------------------------------------------------------


def _as(role: str, user: str = "U1001") -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


def test_api_an_admin_sets_the_minimum_and_every_read_follows(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    with TestClient(app) as client:
        assert client.post(
            "/config/projects", json={"id": "checkout", "name": "Checkout"}
        ).status_code in {200, 201}
        before = client.get("/config/delivery/forecast", headers=_as("admin"))
        saved = client.put(
            "/config/delivery/forecast", json={"min_history_days": 5}, headers=_as("admin")
        )
        read = client.get("/config/delivery/forecast", headers=_as("admin"))
        delivery = client.get("/projects/checkout/delivery", headers=_as("po"))
        history = client.get("/projects/checkout/delivery/history?days=3", headers=_as("po"))
        requirements = client.get("/projects/checkout/requirements", headers=_as("po"))
        longer = client.put(
            "/config/delivery/forecast", json={"min_history_days": 25}, headers=_as("admin")
        )
        long_requirements = client.get("/projects/checkout/requirements", headers=_as("po"))
        asked_requirements = client.get(
            "/projects/checkout/requirements?days=14", headers=_as("po")
        )
        too_few = client.put(
            "/config/delivery/forecast", json={"min_history_days": 2}, headers=_as("admin")
        )
        too_many = client.put(
            "/config/delivery/forecast", json={"min_history_days": 61}, headers=_as("admin")
        )
        kept = client.get("/config/delivery/forecast", headers=_as("admin"))
        reset = client.put(
            "/config/delivery/forecast", json={"min_history_days": None}, headers=_as("admin")
        )
        manager_read = client.get("/config/delivery/forecast", headers=_as("mgr"))
        manager_write = client.put(
            "/config/delivery/forecast", json={"min_history_days": 5}, headers=_as("mgr")
        )

    assert before.status_code == 200, before.text
    assert before.json() == {
        "min_history_days": 10,
        "default_min_history_days": 10,
        "is_default": True,
        "lowest": 3,
        "highest": 60,
        "window_days": 30,
        "updated_at": None,
        "updated_by": None,
    }
    assert saved.status_code == 200, saved.text
    assert saved.json()["min_history_days"] == 5 and saved.json()["is_default"] is False
    assert saved.json()["updated_by"] == "U1001"
    assert read.json() == saved.json()
    project = delivery.json()["project"]
    assert project["history"]["needed_days"] == 5
    assert history.json()["needed_days"] == 5
    assert requirements.json()["forecast_needed_days"] == 5
    assert requirements.json()["timeline_days"] == 30
    assert longer.json()["window_days"] == history_window_days(25) == 38
    assert long_requirements.json()["timeline_days"] == 38
    assert long_requirements.json()["forecast_needed_days"] == 25
    assert asked_requirements.json()["timeline_days"] == 14
    assert too_few.status_code == 422
    assert too_few.json()["detail"].startswith("A forecast needs at least 3 working days")
    assert too_many.status_code == 422
    assert too_many.json()["detail"] == (
        "A forecast can wait for at most 60 working days of history, about three months."
    )
    assert kept.json()["min_history_days"] == 25
    assert reset.json()["min_history_days"] == 10 and reset.json()["is_default"] is True
    assert manager_read.status_code == 403 and manager_write.status_code == 403
