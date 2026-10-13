"""A pod that works on two projects keeps no forecast row, and a bad row never drops the day.

The forecast_days key is (tenant, scope kind, scope id, day): it has no project. A pod's
forecast is its part of one project's work, so a pod on two projects offers the store the
same key twice, with two different forecasts. Postgres refuses a multi-row INSERT ... ON
CONFLICT DO UPDATE with a repeated key (CardinalityViolation) and saves none of its rows,
which lost a whole day's rows for every project of a tenant. The demo data shares no pod
between projects, which is why nothing caught it.

Decision D80: until the key has the project, such a pod keeps no row on the days it works
on more than one project. Its today is live in each project's own context, and its past
days are not kept yet; a row of one project is never read as the other's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta

import pytest
from structlog.testing import capture_logs

from core.application.forecast_service import ForecastService
from core.application.impact_service import ImpactPeriod, PeriodKind
from core.domain.auth import Principal, Role
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    ForecastDayRecord,
    ForecastDaysNotKept,
    Verdict,
    forecast_day_key,
)
from core.domain.graph import GraphEdge, Pod, Project, Task
from core.domain.impact import Measure
from infra.persistence.in_memory_forecast import InMemoryForecastDayRepository
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_forecast import PostgresForecastDayRepository
from infra.registry import ServiceRegistry
from infra.workflows import delivery_reports
from tests.unit.test_delivery_impact import FULL
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
SHARED_EVENT = "forecast_day_not_kept_pod_on_several_projects"
#: The day Audit starts working with Payments Pod, when a world says so (a Monday).
JOINED = date(2026, 9, 28)


def _scope(kind: CommitmentScopeKind, scope_id: str, project_id: str) -> CommitmentScope:
    return CommitmentScope(kind=kind, id=scope_id, project_id=project_id)


PAY_IN_CHECKOUT = _scope(POD_KIND, "pod-pay", "checkout")
PAY_IN_AUDIT = _scope(POD_KIND, "pod-pay", "audit")


class _OnTheDay(ServiceRegistry):
    def _tenant_today(self) -> date:
        return TODAY


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


async def _shared_pod_registry(
    *, joined: date | None = None
) -> tuple[ServiceRegistry, InMemoryGraphStore]:
    """Checkout and Audit, and Payments Pod working on both, each with its own tickets.

    Audit sorts first by id and last by name. Payments Pod works on Audit from ``joined``,
    else always. Storefront works on Checkout only, and Audit Pod on Audit only.
    """
    registry, store = await _registry()
    for node in (
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
        await store.upsert_node(node)
    await store.add_edge(_contains("audit", "pod-aud"))
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT,
            from_node_id="audit",
            to_node_id="pod-pay",
            kind=_contains("audit", "pod-pay").kind,
            valid_from=joined,
        )
    )
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
    # The registry reads today as TODAY, so the timeline and the impact read can be asked.
    registry = _OnTheDay(registry.settings, graph_store=store)
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


def _working_days(first: date, last: date) -> list[date]:
    return [
        first + timedelta(days=offset)
        for offset in range((last - first).days + 1)
        if (first + timedelta(days=offset)).weekday() < 5
    ]


def _manager() -> Principal:
    return Principal(tenant_id=TENANT, subject="dev-dana", roles=frozenset({Role.MGR}))


# --- The hourly run ------------------------------------------------------------------


async def test_a_pod_on_two_projects_keeps_no_row_and_every_other_scope_keeps_its_own() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    recorded = await service.record_forecast_days(TENANT, TODAY)

    # Two projects and the two pods that work on one project each; Payments, on both, none.
    assert recorded == 4
    offered = {(row.scope.kind, row.scope.id, row.scope.project_id) for row in kept.offered}
    assert offered == {
        (PROJECT_KIND, "checkout", "checkout"),
        (PROJECT_KIND, "audit", "audit"),
        (POD_KIND, "pod-store", "checkout"),
        (POD_KIND, "pod-aud", "audit"),
    }
    for scope in (PAY_IN_CHECKOUT, PAY_IN_AUDIT):
        assert await kept.list_between(TENANT, scope, date(2026, 1, 1), TODAY) == []


async def test_a_pod_on_one_project_is_kept_as_it_always_was() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)
    alone, _alone_store = await _registry()
    await _keep_history(alone)
    alone_kept = InMemoryForecastDayRepository()
    await _service(alone, kept=alone_kept).record_forecast_days(TENANT, TODAY)

    await service.record_forecast_days(TENANT, TODAY)

    storefront = _scope(POD_KIND, "pod-store", "checkout")
    (row,) = await kept.list_between(TENANT, storefront, TODAY, TODAY)
    # The same row the tenant kept with no second project, and what a live read computes.
    assert row == (await alone_kept.list_between(TENANT, storefront, TODAY, TODAY))[0]
    assert row == await service.forecast_day(TENANT, storefront, TODAY)
    assert row.attribution is not None
    audit_pod = _scope(POD_KIND, "pod-aud", "audit")
    (kept_audit_pod,) = await kept.list_between(TENANT, audit_pod, TODAY, TODAY)
    assert kept_audit_pod == await service.forecast_day(TENANT, audit_pod, TODAY)


async def test_one_info_line_names_the_pod_and_the_day_it_was_not_kept() -> None:
    registry, _store = await _shared_pod_registry()
    service = _service(registry, kept=InMemoryForecastDayRepository())

    with capture_logs() as logs:
        await service.record_forecast_days(TENANT, TODAY)

    (line,) = [log for log in logs if log["event"] == SHARED_EVENT]
    assert line["log_level"] == "info"
    assert (line["tenant_id"], line["day"], line["pod_id"]) == (TENANT, "2026-10-05", "pod-pay")
    assert line["project_ids"] == "audit,checkout"
    assert not [log for log in logs if log["log_level"] in ("warning", "error")]


async def test_a_pod_that_joins_a_second_project_is_kept_until_the_day_it_does() -> None:
    registry, _store = await _shared_pod_registry(joined=JOINED)
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)
    before = JOINED - timedelta(days=3)  # a Friday: Payments works on Checkout alone

    await service.record_forecast_days(TENANT, before)
    alone = [row for row in kept.offered if row.scope.id == "pod-pay"]
    with capture_logs() as logs:
        await service.record_forecast_days(TENANT, JOINED)

    assert [(row.scope.project_id, row.day) for row in alone] == [("checkout", before)]
    assert [row.scope.id for row in kept.offered if row.day == JOINED].count("pod-pay") == 0
    assert len([log for log in logs if log["event"] == SHARED_EVENT]) == 1


# --- The reconstruction --------------------------------------------------------------


async def test_the_reconstruction_rebuilds_no_day_of_a_pod_on_two_projects() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    with capture_logs() as logs:
        summary = await service.reconstruct_forecast_days(TENANT, TODAY, days=14)

    days = len(_working_days(TODAY - timedelta(days=14), TODAY - timedelta(days=1)))
    # Two projects and the two pods that work on one project, one row a working day each.
    assert summary.scopes == 4
    assert summary.written == 4 * days
    window = (TODAY - timedelta(days=14), TODAY)
    for scope in (PAY_IN_CHECKOUT, PAY_IN_AUDIT):
        assert await kept.list_between(TENANT, scope, *window) == []
    rows = await kept.list_between(TENANT, _scope(POD_KIND, "pod-store", "checkout"), *window)
    assert len(rows) == days and all(row.reconstructed for row in rows)
    # One info line for each day of the 14 it was not rebuilt.
    says = [log for log in logs if log["event"] == SHARED_EVENT]
    assert len(says) == 14
    assert {log["pod_id"] for log in says} == {"pod-pay"}
    assert {log["day"] for log in says} == {
        (TODAY - timedelta(days=offset)).isoformat() for offset in range(1, 15)
    }


async def test_the_reconstruction_rebuilds_the_days_before_a_pod_took_a_second_project() -> None:
    registry, _store = await _shared_pod_registry(joined=JOINED)
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    await service.reconstruct_forecast_days(TENANT, TODAY, days=14)

    window = (TODAY - timedelta(days=14), TODAY)
    rows = await kept.list_between(TENANT, PAY_IN_CHECKOUT, *window)
    # Payments Pod worked on Checkout alone until Sun 27 Sep: those working days, and no more.
    assert [row.day for row in rows] == _working_days(window[0], JOINED - timedelta(days=1))
    assert all(row.reconstructed for row in rows)
    assert await kept.list_between(TENANT, PAY_IN_AUDIT, *window) == []


async def test_the_reconstruction_leaves_what_the_hourly_run_kept_where_it_was_kept() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)
    friday = TODAY - timedelta(days=3)

    await service.record_forecast_days(TENANT, friday)
    summary = await service.reconstruct_forecast_days(TENANT, TODAY, days=10)

    # The four scopes the Friday kept are left as they were.
    assert summary.kept == 4


# --- The readers ---------------------------------------------------------------------


async def test_each_projects_timeline_shows_the_shared_pods_live_forecast_for_that_project() -> (
    None
):
    registry, _store = await _shared_pod_registry()
    service = registry.forecast_service()
    await service.reconstruct_forecast_days(TENANT, TODAY, days=14)
    await service.record_forecast_days(TENANT, TODAY)
    timeline = registry.timeline_service()

    seen: dict[str, tuple[object, ...]] = {}
    for project_id in ("checkout", "audit"):
        live = await service.project_delivery(TENANT, project_id, TODAY)
        (part,) = [pod for pod in live.pods if pod.scope.id == "pod-pay"]
        view = await timeline.project_timeline(_manager(), project_id, as_of=TODAY)
        (row,) = [row for row in view.rows if row.row_id == "pod:pod-pay"]
        # Today's forecast of the pod's part of this project, computed now, not a kept row.
        assert row.scope.project_id == project_id and row.forecast_on == TODAY
        assert (row.p50, row.p85) == (part.history.p50, part.history.p85)
        assert row.start == TODAY  # no kept history: its span starts today
        seen[project_id] = (row.done, row.total, row.in_scope, row.team_date)
    # And the pod's own timeline lists its row under each project, each its own part.
    pod_view = await timeline.pod_timeline(_manager(), "pod-pay", as_of=TODAY)
    assert sorted(row.scope.project_id for row in pod_view.rows) == ["audit", "checkout"]
    assert all(row.forecast_on == TODAY and row.start == TODAY for row in pod_view.rows)
    assert seen["checkout"] != seen["audit"]


async def test_a_row_kept_under_one_project_is_never_read_as_the_pod_of_the_other() -> None:
    registry, _store = await _shared_pod_registry()
    service = registry.forecast_service()
    friday = TODAY - timedelta(days=3)
    # What an earlier run left: the pod's part of Audit on a past day, with a date of its own.
    stale = ForecastDayRecord(
        tenant_id=TENANT,
        scope=PAY_IN_AUDIT,
        day=friday,
        p50=date(2027, 1, 4),
        p85=date(2027, 2, 1),
        verdict=Verdict.OFF_TRACK,
        target_date=None,
        team_latest=None,
        team_latest_key=None,
        remaining=2,
        unit="requirements",
        sample_days=12,
        completed_in_sample=3,
        min_sample_days=10,
        computed_at=NOW,
    )
    await registry.forecast_day_repository().save([stale])
    timeline = registry.timeline_service()

    assert await service.kept_days(TENANT, PAY_IN_CHECKOUT, friday, friday) == []
    assert await service.kept_days(TENANT, PAY_IN_AUDIT, friday, friday) == [stale]
    for project_id, start in (("checkout", TODAY), ("audit", friday)):
        view = await timeline.project_timeline(_manager(), project_id, as_of=TODAY)
        (row,) = [row for row in view.rows if row.row_id == "pod:pod-pay"]
        # Today is live in every project; only Audit's own history reaches back to Friday.
        assert row.start == start
        assert row.p50 != date(2027, 1, 4) and row.forecast_on == TODAY


async def test_the_impact_read_of_a_shared_pod_is_its_live_forecast_with_no_kept_history() -> None:
    registry, _store = await _shared_pod_registry()
    forecasts = registry.forecast_service()
    await forecasts.reconstruct_forecast_days(TENANT, TODAY, days=14)
    await forecasts.record_forecast_days(TENANT, TODAY)

    for scope in (PAY_IN_CHECKOUT, PAY_IN_AUDIT):
        live = await forecasts.project_delivery(TENANT, scope.project_id, TODAY)
        (part,) = [pod for pod in live.pods if pod.scope == scope]
        view = await registry.delivery_impact_service().impact(
            TENANT,
            scope,
            period=ImpactPeriod(kind=PeriodKind.LAST_30_DAYS),
            measure=Measure.P85,
            as_of=TODAY,
            access=FULL,
        )
        assert view.start is not None and view.end is not None
        # One row, today's, computed in the project's context: no past day, no other project's.
        assert (view.start.day, view.end.day) == (TODAY, TODAY)
        assert (view.end.p50, view.end.p85) == (part.history.p50, part.history.p85)
        assert view.drivers == () and view.reconstructed_days == 0


# --- The activity ---------------------------------------------------------------------


async def test_the_snapshot_run_keeps_what_it_can_of_the_day_when_a_pod_is_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _store = await _shared_pod_registry()
    monkeypatch.setattr(delivery_reports, "_service_registry", lambda: registry)

    with capture_logs() as logs:
        result = await delivery_reports.run_delivery_snapshot_activity(
            delivery_reports.DeliverySnapshotInput(tenant_id=TENANT, observed_at=NOW.isoformat())
        )

    assert result.projects_recorded == 2 and result.forecasts_recorded == 4
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
