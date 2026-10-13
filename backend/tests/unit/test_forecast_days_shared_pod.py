"""A pod two projects share has one forecast row a day, and a bad row never drops the day.

The forecast_days key is (tenant, scope kind, scope id, day): it has no project, so a
pod that works on two projects offers the store the same key twice. Postgres refuses
a multi-row INSERT ... ON CONFLICT DO UPDATE with a repeated key (CardinalityViolation)
and saves none of its rows, which lost a whole day's rows for every project of a
tenant. The demo data shares no pod between projects, which is why nothing caught it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta

import pytest
from structlog.testing import capture_logs

from core.application.forecast_service import ForecastService
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    ForecastDayRecord,
    ForecastDaysNotKept,
    Verdict,
    forecast_day_key,
)
from core.domain.graph import Pod, Project, Task
from infra.persistence.in_memory_forecast import InMemoryForecastDayRepository
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_forecast import PostgresForecastDayRepository
from infra.registry import ServiceRegistry
from infra.workflows import delivery_reports
from tests.unit.test_forecast import (
    NOW,
    TENANT,
    TODAY,
    S,
    _contains,
    _keep_history,
    _registry,
    _snapshot,
)
from tests.unit.test_forecast_days import _service

POD_KIND = CommitmentScopeKind.POD
PROJECT_KIND = CommitmentScopeKind.PROJECT


def _scope(kind: CommitmentScopeKind, scope_id: str, project_id: str) -> CommitmentScope:
    return CommitmentScope(kind=kind, id=scope_id, project_id=project_id)


class _PostgresLikeForecastDays(InMemoryForecastDayRepository):
    """The in-memory rows with Postgres's rule: a call that offers one key twice is refused
    whole and keeps none of its rows. It keeps what the service hands it, for the tests."""

    def __init__(self) -> None:
        super().__init__()
        self.offered: list[ForecastDayRecord] = []

    async def save(self, records: Sequence[ForecastDayRecord]) -> None:
        keys = [forecast_day_key(record) for record in records]
        if len(set(keys)) != len(keys):
            raise RuntimeError("CardinalityViolation: ON CONFLICT DO UPDATE affects a row twice")
        self.offered += records
        await super().save(records)


async def _shared_pod_registry() -> tuple[ServiceRegistry, InMemoryGraphStore]:
    """Checkout and Audit, and the Payments pod working on both, each with its own tickets.

    Audit sorts first by id and last by name, so the project a shared pod is kept under
    can only be the one the rule names, never the one a listing happens to give first.
    """
    registry, store = await _registry()
    for project in (
        Project(
            tenant_id=TENANT,
            id="checkout",
            name="Checkout Revamp",
            metadata={"jira_project_key": "CHK"},
        ),
        Project(
            tenant_id=TENANT, id="audit", name="Zeta Audit", metadata={"jira_project_key": "AUD"}
        ),
        Pod(tenant_id=TENANT, id="pod-aud", name="Audit Pod"),
    ):
        await store.upsert_node(project)
    for parent, child in (("audit", "pod-pay"), ("audit", "pod-aud")):
        await store.add_edge(_contains(parent, child))
    for key, status, pod in (
        ("AUD-7", "In Progress", "pod-pay"),
        ("AUD-8", "In QA", "pod-pay"),
        ("AUD-1", "In Progress", "pod-aud"),
        ("AUD-2", "To Do", "pod-aud"),
    ):
        await store.upsert_node(
            Task(
                tenant_id=TENANT,
                id=key,
                name=f"{key} work",
                metadata={"key": key, "status": status, "state": "in_progress"},
            )
        )
        await store.add_edge(_contains(pod, key))
    await _keep_history(registry)
    await _keep_audit_history(registry)
    return registry, store


async def _keep_audit_history(registry: ServiceRegistry) -> None:
    """Four weeks of working-day snapshots of Audit, like _keep_history's for Checkout."""
    snapshots = registry.requirements_snapshot_repository()
    day = TODAY - timedelta(days=28)
    done: dict[str, S] = {}
    index = 0
    while day < TODAY:
        if day.weekday() < 5:
            index += 1
            if index % 3 == 0:
                done[f"OLD-A{index}"] = S.PRODUCTION
            items = {
                **done,
                "AUD-7": S.IN_DEVELOPMENT,
                "AUD-8": S.IN_TESTING,
                "AUD-1": S.IN_DEVELOPMENT,
                "AUD-2": S.IN_DEVELOPMENT,
            }
            await snapshots.save(_snapshot(day, items, "audit"))
        day += timedelta(days=1)


def _working_days_before_today(days: int) -> int:
    return sum(1 for offset in range(1, days + 1) if (TODAY - timedelta(days=offset)).weekday() < 5)


# --- The hourly run ------------------------------------------------------------------


async def test_a_pod_two_projects_share_gets_one_row_a_day_and_every_project_its_own() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    recorded = await service.record_forecast_days(TENANT, TODAY)

    # Two projects and three pods: Payments, which both work with, counts once.
    assert recorded == 5
    offered = {(row.scope.kind, row.scope.id): row for row in kept.offered}
    assert len(offered) == len(kept.offered) == 5
    assert {scope_id for (kind, scope_id) in offered if kind is PROJECT_KIND} == {
        "checkout",
        "audit",
    }
    assert {scope_id for (kind, scope_id) in offered if kind is POD_KIND} == {
        "pod-pay",
        "pod-store",
        "pod-aud",
    }
    for project_id in ("checkout", "audit"):
        assert await kept.list_between(
            TENANT, _scope(PROJECT_KIND, project_id, project_id), TODAY, TODAY
        )


async def test_a_shared_pods_row_is_its_part_of_the_first_project_by_id() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    await service.record_forecast_days(TENANT, TODAY)

    # Audit, though a listing by name gives Checkout first.
    under_audit = _scope(POD_KIND, "pod-pay", "audit")
    under_checkout = _scope(POD_KIND, "pod-pay", "checkout")
    (row,) = await kept.list_between(TENANT, under_audit, TODAY, TODAY)
    assert await kept.list_between(TENANT, under_checkout, TODAY, TODAY) == []
    # The row and its attribution are one computation, the one a live read of that scope makes.
    assert row == await service.forecast_day(TENANT, under_audit, TODAY)
    assert row.attribution is not None
    # The pod's part of Audit: its two Audit tickets, not the Checkout tickets it also holds.
    delivery = await service.project_delivery(TENANT, "audit", TODAY)
    (part,) = [pod for pod in delivery.pods if pod.scope == under_audit]
    assert (
        (row.remaining, row.unit)
        == (part.history.remaining, part.history.unit)
        == (2, "requirements")
    )
    # A pod only one project works on is kept under it as before.
    (storefront,) = await kept.list_between(
        TENANT, _scope(POD_KIND, "pod-store", "checkout"), TODAY, TODAY
    )
    assert storefront.attribution is not None


async def test_a_later_run_the_same_day_replaces_the_shared_pods_one_row() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    first = await service.record_forecast_days(TENANT, TODAY)
    second = await service.record_forecast_days(TENANT, TODAY)

    assert first == second == 5
    assert len(kept.offered) == 10
    assert (
        len(await kept.list_between(TENANT, _scope(POD_KIND, "pod-pay", "audit"), TODAY, TODAY))
        == 1
    )


# --- The reconstruction --------------------------------------------------------------


async def test_the_reconstruction_rebuilds_a_shared_pod_once_a_day_under_the_same_project() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    summary = await service.reconstruct_forecast_days(TENANT, TODAY, days=14)

    days = _working_days_before_today(14)
    # Two projects and three pods, one row each on each working day with a snapshot.
    assert summary.scopes == 5
    assert summary.written == 5 * days
    rows = await kept.list_between(
        TENANT, _scope(POD_KIND, "pod-pay", "audit"), TODAY - timedelta(days=14), TODAY
    )
    assert len(rows) == days and all(row.reconstructed for row in rows)
    assert (
        await kept.list_between(
            TENANT, _scope(POD_KIND, "pod-pay", "checkout"), TODAY - timedelta(days=14), TODAY
        )
        == []
    )
    # The shared pod's rows carry their breakdown, from the day before in the same scope.
    assert [row.attribution is not None for row in rows[1:]] == [True] * (days - 1)


async def test_the_reconstruction_leaves_the_shared_pods_hourly_row_where_it_was_kept() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)
    friday = TODAY - timedelta(days=3)

    await service.record_forecast_days(TENANT, friday)
    summary = await service.reconstruct_forecast_days(TENANT, TODAY, days=10)

    # The five scopes the Friday kept are left as they were: the pod under the same project.
    assert summary.kept == 5
    (row,) = [
        row
        for row in await kept.list_between(
            TENANT, _scope(POD_KIND, "pod-pay", "audit"), friday, friday
        )
    ]
    assert not row.reconstructed


# --- The activity ---------------------------------------------------------------------


async def test_the_snapshot_run_keeps_every_scope_of_the_day_when_a_pod_is_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _store = await _shared_pod_registry()
    monkeypatch.setattr(delivery_reports, "_service_registry", lambda: registry)
    monkeypatch.setattr(registry, "_tenant_today", lambda: TODAY)

    with capture_logs() as logs:
        result = await delivery_reports.run_delivery_snapshot_activity(
            delivery_reports.DeliverySnapshotInput(tenant_id=TENANT, observed_at=NOW.isoformat())
        )

    assert result.projects_recorded == 2 and result.forecasts_recorded == 5
    assert not [log for log in logs if log["log_level"] in ("warning", "error")]


async def test_a_day_the_store_refuses_is_logged_as_an_error_with_the_rows_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _store = await _registry()
    monkeypatch.setattr(delivery_reports, "_service_registry", lambda: registry)

    async def refused(*_args: object, **_kwargs: object) -> int:
        raise ForecastDaysNotKept(rows=9) from RuntimeError("CardinalityViolation")

    monkeypatch.setattr(ForecastService, "record_forecast_days", refused)
    with capture_logs() as logs:
        result = await delivery_reports.run_delivery_snapshot_activity(
            delivery_reports.DeliverySnapshotInput(tenant_id=TENANT, observed_at=NOW.isoformat())
        )

    # The snapshots are stored and the run still ends, so the log is the only trace.
    assert result.projects_recorded == 1 and result.forecasts_recorded == 0
    (failure,) = [log for log in logs if log["event"] == "forecast_days_not_recorded"]
    assert failure["log_level"] == "error"
    assert failure["rows_lost"] == 9
    assert failure["error_type"] == "RuntimeError"
    assert failure["tenant_id"] == TENANT and failure["day"] == "2026-10-05"


async def test_a_forecast_that_fails_before_any_row_is_saved_is_an_error_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _store = await _registry()
    monkeypatch.setattr(delivery_reports, "_service_registry", lambda: registry)

    async def broken(*_args: object, **_kwargs: object) -> int:
        raise ValueError("boom")

    monkeypatch.setattr(ForecastService, "record_forecast_days", broken)
    with capture_logs() as logs:
        await delivery_reports.run_delivery_snapshot_activity(
            delivery_reports.DeliverySnapshotInput(tenant_id=TENANT, observed_at=NOW.isoformat())
        )

    (failure,) = [log for log in logs if log["event"] == "forecast_days_not_recorded"]
    assert failure["log_level"] == "error"
    # How many rows the day would have had is not known when nothing was computed.
    assert failure["rows_lost"] is None and failure["error_type"] == "ValueError"


# --- The repositories --------------------------------------------------------------------


def _record(
    scope_id: str = "pod-pay",
    project_id: str = "audit",
    *,
    day: date = TODAY,
    p50: date | None = date(2026, 11, 3),
    reconstructed: bool = False,
    kind: CommitmentScopeKind = POD_KIND,
) -> ForecastDayRecord:
    return ForecastDayRecord(
        tenant_id=TENANT,
        scope=_scope(kind, scope_id, project_id),
        day=day,
        p50=p50,
        p85=date(2026, 11, 17),
        verdict=Verdict.AT_RISK,
        target_date=None,
        team_latest=None,
        team_latest_key=None,
        remaining=4,
        unit="requirements",
        sample_days=12,
        completed_in_sample=6,
        min_sample_days=10,
        computed_at=NOW,
        reconstructed=reconstructed,
    )


class _PostgresLikeExecutor:
    """A Postgres that refuses a statement offering one key twice, as the real one does."""

    COLUMNS = 19

    def __init__(self, *, fail_from_statement: int | None = None) -> None:
        self.statements: list[int] = []
        self.rows: dict[tuple[object, ...], tuple[object, ...]] = {}
        self._fail_from = fail_from_statement

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        rows = [tuple(params[i : i + self.COLUMNS]) for i in range(0, len(params), self.COLUMNS)]
        if self._fail_from is not None and len(self.statements) >= self._fail_from:
            raise ConnectionError("the server closed the connection")
        keys = [(row[0], row[1], row[2], row[4]) for row in rows]
        if len(set(keys)) != len(keys):
            raise RuntimeError("CardinalityViolation: ON CONFLICT DO UPDATE affects a row twice")
        self.statements.append(len(rows))
        for key, row in zip(keys, rows, strict=True):
            self.rows[key] = row
        return None

    async def fetch(self, query: str, params: Sequence[object] = ()) -> Sequence[dict[str, object]]:
        return []


async def test_postgres_offers_one_row_per_key_and_keeps_the_last_with_a_warning() -> None:
    executor = _PostgresLikeExecutor()
    repository = PostgresForecastDayRepository(executor)
    other_day = _record(day=TODAY - timedelta(days=1))

    with capture_logs() as logs:
        await repository.save(
            [
                _record(project_id="checkout", p50=date(2026, 11, 3)),
                other_day,
                _record(project_id="audit", p50=date(2026, 12, 1)),
            ]
        )

    assert executor.statements == [2]
    stored = executor.rows[(TENANT, "pod", "pod-pay", TODAY)]
    # The last record of the key won: project, then the p50 after it.
    assert (stored[3], stored[5]) == ("audit", date(2026, 12, 1))
    (warning,) = [log for log in logs if log["event"] == "forecast_day_duplicate_key"]
    assert warning["log_level"] == "warning"
    assert warning["key"] == "pod/pod-pay/2026-10-05"
    assert (warning["replaced_project_id"], warning["kept_project_id"]) == ("checkout", "audit")


async def test_a_batch_the_store_refuses_says_how_many_rows_it_did_not_take() -> None:
    # 250 rows go in batches of 100; the second statement fails, so 150 are lost.
    executor = _PostgresLikeExecutor(fail_from_statement=1)
    repository = PostgresForecastDayRepository(executor)
    rows = [
        _record(scope_id=f"pod-{index}", project_id="audit", kind=POD_KIND) for index in range(250)
    ]

    with pytest.raises(ForecastDaysNotKept) as refused:
        await repository.save(rows)

    assert refused.value.rows == 150
    assert isinstance(refused.value.__cause__, ConnectionError)
    assert executor.statements == [100]


async def test_postgres_saves_nothing_for_no_rows() -> None:
    executor = _PostgresLikeExecutor()

    await PostgresForecastDayRepository(executor).save([])

    assert executor.statements == []


async def test_the_in_memory_rows_keep_the_last_of_a_repeated_key_as_postgres_does() -> None:
    repository = InMemoryForecastDayRepository()

    with capture_logs() as logs:
        await repository.save(
            [
                _record(project_id="checkout", p50=date(2026, 11, 3)),
                _record(project_id="audit", p50=date(2026, 12, 1)),
            ]
        )

    (row,) = await repository.list_between(
        TENANT, _scope(POD_KIND, "pod-pay", "audit"), TODAY, TODAY
    )
    assert row.p50 == date(2026, 12, 1)
    assert (
        await repository.list_between(TENANT, _scope(POD_KIND, "pod-pay", "checkout"), TODAY, TODAY)
        == []
    )
    (warning,) = [log for log in logs if log["event"] == "forecast_day_duplicate_key"]
    assert warning["key"] == "pod/pod-pay/2026-10-05"


async def test_a_repeated_key_is_resolved_before_it_meets_a_row_the_hourly_run_kept() -> None:
    """The same rule on a call that mixes a kept row and a rebuilt one for one key."""
    repository = InMemoryForecastDayRepository()
    kept = _record(p50=date(2026, 11, 3))
    await repository.save([kept])

    await repository.save(
        [
            replace(kept, p50=date(2026, 12, 1), reconstructed=True),
            replace(kept, p50=date(2026, 12, 8), reconstructed=True),
        ]
    )

    (row,) = await repository.list_between(
        TENANT, _scope(POD_KIND, "pod-pay", "audit"), TODAY, TODAY
    )
    assert row == kept


def test_the_exception_says_how_many_rows() -> None:
    assert ForecastDaysNotKept(rows=3).rows == 3
    assert "3" in str(ForecastDaysNotKept(rows=3))
