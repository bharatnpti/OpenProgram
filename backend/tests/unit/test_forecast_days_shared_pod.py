"""A pod that works on two projects keeps a forecast row under each, and a bad row never
drops the day.

A pod's forecast is its part of one project's work: that project's keys, date and seed.
The forecast_days key is (tenant, scope kind, scope id, project, day) since migration
0044, so a pod on two projects keeps two rows a day, each its part of one project, with
the breakdown from that project's own computation, and every reader asks for the row
under the project it reads. Before 0044 the key had no project: the shared pod offered
one key twice (Postgres refused the whole statement, CardinalityViolation), and then,
by D80, kept no row at all, so it had no history.

The world: Checkout and Audit, and Payments Pod working on both. Its part of Checkout
is CHK-2 and CHK-3 open, and a CHK-5x finished every third working day; its part of
Audit is AUD-7 and AUD-8 open, AUD-9 from Wed 30 Sep, and an AUD-5x finished every
second working day.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta

import pytest
from structlog.testing import capture_logs

from core.application.ask_service import DeliveryImpactTool
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
from core.domain.graph import Pod, Project, Task
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
#: The day Audit starts working with Payments Pod, when a world says so (a Monday).
JOINED = date(2026, 9, 28)
#: The day AUD-9 joins Payments Pod's part of Audit (a Wednesday).
ADDED = date(2026, 9, 30)
FRIDAY = TODAY - timedelta(days=3)


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
    else always; AUD-9 joins its part of Audit on ADDED. Storefront works on Checkout
    only, and Audit Pod on Audit only. Payments Pod finishes work in each project at its
    own pace (_FINISHED), so each part has its own forecast.
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
    await store.add_edge(replace(_contains("audit", "pod-pay"), valid_from=joined))
    finished = [(key, "Done", "pod-pay", None) for key in _FINISHED.values()]
    for key, status, pod, since in (
        ("AUD-7", "In Progress", "pod-pay", None),
        ("AUD-8", "In QA", "pod-pay", None),
        ("AUD-9", "To Do", "pod-pay", ADDED),
        ("AUD-1", "In Progress", "pod-aud", None),
        ("AUD-2", "To Do", "pod-aud", None),
        *finished,
    ):
        await store.upsert_node(
            Task(
                tenant_id=TENANT,
                id=key,
                name=f"{key} work",
                metadata={"key": key, "status": status, "state": "in_progress"},
            )
        )
        await store.add_edge(replace(_contains(pod, key), valid_from=since))
    # The registry reads today as TODAY, so the timeline and the impact read can be asked.
    registry = _OnTheDay(registry.settings, graph_store=store)
    await _keep_shared_history(registry)
    return registry, store


def _finished() -> dict[tuple[str, int], str]:
    """Payments Pod's finished work: (project, working-day index) to its issue key."""
    found: dict[tuple[str, int], str] = {}
    for index in range(1, 21):
        if index % 3 == 0:
            found[("checkout", index)] = f"CHK-{50 + index}"
        if index % 2 == 0:
            found[("audit", index)] = f"AUD-{50 + index}"
    return found


_FINISHED = _finished()


async def _keep_shared_history(registry: ServiceRegistry) -> None:
    """Four weeks of working-day snapshots of both projects, as _keep_history keeps them for
    Checkout, with Payments Pod's finished work (_FINISHED) in each."""
    snapshots = registry.requirements_snapshot_repository()
    day = TODAY - timedelta(days=28)
    done: dict[str, dict[str, S]] = {"checkout": {}, "audit": {}}
    index = 0
    while day < TODAY:
        if day.weekday() < 5:
            index += 1
            if index % 3 == 0:
                done["checkout"][f"OLD-{index}"] = S.PRODUCTION
                done["audit"][f"OLD-A{index}"] = S.PRODUCTION
            for project_id in ("checkout", "audit"):
                if (key := _FINISHED.get((project_id, index))) is not None:
                    done[project_id][key] = S.PRODUCTION
            checkout = {**done["checkout"], "CHK-2": S.IN_DEVELOPMENT, "CHK-3": S.IN_TESTING}
            audit = {
                **done["audit"],
                "AUD-7": S.IN_DEVELOPMENT,
                "AUD-8": S.IN_TESTING,
                "AUD-1": S.IN_DEVELOPMENT,
                "AUD-2": S.IN_DEVELOPMENT,
                **({"AUD-9": S.IN_DEVELOPMENT} if day >= ADDED else {}),
            }
            await snapshots.save(_snapshot(day, checkout))
            await snapshots.save(_snapshot(day, audit, "audit"))
        day += timedelta(days=1)


def _working_days(first: date, last: date) -> list[date]:
    return [
        first + timedelta(days=offset)
        for offset in range((last - first).days + 1)
        if (first + timedelta(days=offset)).weekday() < 5
    ]


def _manager() -> Principal:
    return Principal(tenant_id=TENANT, subject="dev-dana", roles=frozenset({Role.MGR}))


def _attributed_keys(row: ForecastDayRecord) -> set[str]:
    """Every issue key a kept breakdown names."""
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            key = value.get("key")
            if isinstance(key, str):
                found.add(key)
            for inner in value.values():
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(dict(row.attribution or {}))
    return found


def _kept(scope: CommitmentScope, day: date, p50: date, p85: date) -> ForecastDayRecord:
    """A row an earlier run kept for ``scope`` on ``day``, with dates of its own."""
    return ForecastDayRecord(
        tenant_id=TENANT,
        scope=scope,
        day=day,
        p50=p50,
        p85=p85,
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


# --- The hourly run ------------------------------------------------------------------


async def test_a_pod_on_two_projects_keeps_a_row_under_each_with_that_projects_numbers() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    with capture_logs() as logs:
        recorded = await service.record_forecast_days(TENANT, TODAY)

    # Two projects, the two pods on one project each, and Payments under each of its two.
    assert recorded == 6
    offered = {(row.scope.kind, row.scope.id, row.scope.project_id) for row in kept.offered}
    assert offered == {
        (PROJECT_KIND, "checkout", "checkout"),
        (PROJECT_KIND, "audit", "audit"),
        (POD_KIND, "pod-store", "checkout"),
        (POD_KIND, "pod-aud", "audit"),
        (POD_KIND, "pod-pay", "checkout"),
        (POD_KIND, "pod-pay", "audit"),
    }
    (in_checkout,) = await kept.list_between(TENANT, PAY_IN_CHECKOUT, TODAY, TODAY)
    (in_audit,) = await kept.list_between(TENANT, PAY_IN_AUDIT, TODAY, TODAY)
    # Each row is that project's part of the pod, row and breakdown from one computation:
    # what a live read of the pod in that project's context computes.
    assert in_checkout == await service.forecast_day(TENANT, PAY_IN_CHECKOUT, TODAY)
    assert in_audit == await service.forecast_day(TENANT, PAY_IN_AUDIT, TODAY)
    assert (in_checkout.remaining, in_audit.remaining) == (2, 3)
    assert in_checkout.completed_in_sample < in_audit.completed_in_sample
    assert in_checkout.p85 is not None and in_audit.p85 is not None
    assert in_checkout.attribution is not None and in_audit.attribution is not None
    assert all(key.startswith("CHK-") for key in _attributed_keys(in_checkout))
    assert all(key.startswith("AUD-") for key in _attributed_keys(in_audit))
    # Two keys, not a repeat: nothing to warn about, and no row skipped.
    assert not [log for log in logs if log["log_level"] in ("warning", "error")]
    assert not [log for log in logs if "several_projects" in str(log["event"])]


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


async def test_a_pod_that_joins_a_second_project_keeps_a_row_under_it_from_that_day() -> None:
    registry, _store = await _shared_pod_registry(joined=JOINED)
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)
    before = JOINED - timedelta(days=3)  # a Friday: Payments works on Checkout alone

    await service.record_forecast_days(TENANT, before)
    await service.record_forecast_days(TENANT, JOINED)

    pay = sorted(
        (row.day, row.scope.project_id) for row in kept.offered if row.scope.id == "pod-pay"
    )
    assert pay == [(before, "checkout"), (JOINED, "audit"), (JOINED, "checkout")]


# --- The reconstruction --------------------------------------------------------------


async def test_the_reconstruction_rebuilds_a_shared_pod_under_each_project() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    with capture_logs() as logs:
        summary = await service.reconstruct_forecast_days(TENANT, TODAY, days=14)

    window = (TODAY - timedelta(days=14), TODAY - timedelta(days=1))
    days = _working_days(*window)
    # Two projects, two single-project pods and Payments under each project, every day.
    assert summary.scopes == 6
    assert summary.written == 6 * len(days)
    in_checkout = await kept.list_between(TENANT, PAY_IN_CHECKOUT, *window)
    in_audit = await kept.list_between(TENANT, PAY_IN_AUDIT, *window)
    assert [row.day for row in in_checkout] == [row.day for row in in_audit] == days
    assert all(row.reconstructed for row in (*in_checkout, *in_audit))
    # Each project's own part: Checkout's two, Audit's two, then three from AUD-9's day,
    # each forecast from its own finished work.
    assert {row.remaining for row in in_checkout} == {2}
    assert [row.remaining for row in in_audit] == [3 if day >= ADDED else 2 for day in days]
    assert all(row.p85 is not None for row in (*in_checkout, *in_audit))
    assert [row.p85 for row in in_checkout] != [row.p85 for row in in_audit]
    # And each breakdown names its own project's work: AUD-9 joins Audit's part only.
    (joined_audit,) = [row for row in in_audit if row.day == ADDED]
    assert "AUD-9" in _attributed_keys(joined_audit)
    assert all(key.startswith("CHK-") for row in in_checkout for key in _attributed_keys(row))
    assert all(key.startswith("AUD-") for row in in_audit for key in _attributed_keys(row))
    assert not [log for log in logs if log["log_level"] in ("warning", "error")]


async def test_the_reconstruction_rebuilds_a_pod_under_a_second_project_from_its_first_day() -> (
    None
):
    registry, _store = await _shared_pod_registry(joined=JOINED)
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    await service.reconstruct_forecast_days(TENANT, TODAY, days=14)

    window = (TODAY - timedelta(days=14), TODAY - timedelta(days=1))
    in_checkout = await kept.list_between(TENANT, PAY_IN_CHECKOUT, *window)
    in_audit = await kept.list_between(TENANT, PAY_IN_AUDIT, *window)
    # Payments worked on Checkout throughout, and on Audit from Mon 28 Sep.
    assert [row.day for row in in_checkout] == _working_days(*window)
    assert [row.day for row in in_audit] == _working_days(JOINED, window[1])


async def test_the_reconstruction_leaves_what_the_hourly_run_kept_under_each_project() -> None:
    registry, _store = await _shared_pod_registry()
    kept = _PostgresLikeForecastDays()
    service = _service(registry, kept=kept)

    await service.record_forecast_days(TENANT, FRIDAY)
    hourly = {
        scope: await kept.list_between(TENANT, scope, FRIDAY, FRIDAY)
        for scope in (PAY_IN_CHECKOUT, PAY_IN_AUDIT)
    }
    summary = await service.reconstruct_forecast_days(TENANT, TODAY, days=10)

    # The six scopes the Friday kept, Payments under both projects, are left as they were.
    assert summary.kept == 6
    for scope, rows in hourly.items():
        (row,) = rows
        assert not row.reconstructed
        assert await kept.list_between(TENANT, scope, FRIDAY, FRIDAY) == [row]


async def test_a_rebuilt_day_of_the_shared_pod_is_the_day_the_hourly_run_kept() -> None:
    """Reconstructed and hourly rows of one project's part agree on the day's forecast."""
    registry, _store = await _shared_pod_registry()
    hourly = InMemoryForecastDayRepository()
    await _service(registry, kept=hourly).record_forecast_days(TENANT, FRIDAY)
    rebuilt = InMemoryForecastDayRepository()

    await _service(registry, kept=rebuilt).reconstruct_forecast_days(TENANT, TODAY, days=7)

    for scope in (PAY_IN_CHECKOUT, PAY_IN_AUDIT):
        (kept_row,) = await hourly.list_between(TENANT, scope, FRIDAY, FRIDAY)
        (rebuilt_row,) = await rebuilt.list_between(TENANT, scope, FRIDAY, FRIDAY)
        assert (rebuilt_row.p50, rebuilt_row.p85, rebuilt_row.remaining) == (
            kept_row.p50,
            kept_row.p85,
            kept_row.remaining,
        )
        assert rebuilt_row.scope == kept_row.scope == scope


# --- The readers ---------------------------------------------------------------------


async def test_each_projects_timeline_reads_only_its_own_row_of_the_shared_pod() -> None:
    registry, _store = await _shared_pod_registry()
    rows = registry.forecast_day_repository()
    await rows.save(
        [
            _kept(PAY_IN_CHECKOUT, FRIDAY, date(2026, 12, 1), date(2026, 12, 15)),
            _kept(PAY_IN_AUDIT, FRIDAY, date(2027, 1, 4), date(2027, 2, 1)),
        ]
    )
    timeline = registry.timeline_service()

    # A past day reads stored rows only: each project's pod row is its own kept row.
    for project_id, band in (
        ("checkout", (date(2026, 12, 1), date(2026, 12, 15))),
        ("audit", (date(2027, 1, 4), date(2027, 2, 1))),
    ):
        view = await timeline.project_timeline(_manager(), project_id, as_of=FRIDAY)
        (row,) = [row for row in view.rows if row.row_id == "pod:pod-pay"]
        assert row.scope.project_id == project_id
        assert (row.forecast_on, row.p50, row.p85) == (FRIDAY, *band)
    # The pod's own panel has a row per project, each reading that project's row.
    pod_view = await timeline.pod_timeline(_manager(), "pod-pay", as_of=FRIDAY)
    assert {row.scope.project_id: row.p50 for row in pod_view.rows} == {
        "audit": date(2027, 1, 4),
        "checkout": date(2026, 12, 1),
    }


async def test_each_projects_timeline_has_the_shared_pods_history_and_its_markers() -> None:
    registry, _store = await _shared_pod_registry()
    service = registry.forecast_service()
    await service.reconstruct_forecast_days(TENANT, TODAY, days=14)
    await service.record_forecast_days(TENANT, TODAY)
    timeline = registry.timeline_service()
    first = _working_days(TODAY - timedelta(days=14), TODAY)[0]

    seen: dict[str, tuple[object, ...]] = {}
    for scope in (PAY_IN_CHECKOUT, PAY_IN_AUDIT):
        live = await service.project_delivery(TENANT, scope.project_id, TODAY)
        (part,) = [pod for pod in live.pods if pod.scope == scope]
        view = await timeline.project_timeline(_manager(), scope.project_id, as_of=TODAY)
        (row,) = [row for row in view.rows if row.row_id == "pod:pod-pay"]
        # Today is live in the project's context, and the span reaches back to its kept rows.
        assert row.forecast_on == TODAY
        assert (row.p50, row.p85) == (part.history.p50, part.history.p85)
        assert row.start == first
        added = {item.key for marker in row.markers for item in marker.items}
        seen[scope.project_id] = (row.done, row.total, frozenset(added))
    # AUD-9 joined Audit's part only: its marker is on Audit's row, never on Checkout's.
    assert "AUD-9" in seen["audit"][2]
    assert not any(key.startswith("AUD-") for key in seen["checkout"][2])
    assert seen["checkout"][:2] != seen["audit"][:2]
    pod_view = await timeline.pod_timeline(_manager(), "pod-pay", as_of=TODAY)
    assert sorted(row.scope.project_id for row in pod_view.rows) == ["audit", "checkout"]
    assert all(row.start == first for row in pod_view.rows)


async def test_the_impact_read_of_a_shared_pod_reads_each_projects_own_history() -> None:
    registry, _store = await _shared_pod_registry()
    forecasts = registry.forecast_service()
    await forecasts.reconstruct_forecast_days(TENANT, TODAY, days=14)
    await forecasts.record_forecast_days(TENANT, TODAY)
    start = TODAY - timedelta(days=7)

    for scope, added in ((PAY_IN_CHECKOUT, set()), (PAY_IN_AUDIT, {"AUD-9"})):
        live = await forecasts.project_delivery(TENANT, scope.project_id, TODAY)
        (part,) = [pod for pod in live.pods if pod.scope == scope]
        (anchor,) = await forecasts.kept_days(TENANT, scope, start, start)
        view = await registry.delivery_impact_service().impact(
            TENANT,
            scope,
            period=ImpactPeriod(kind=PeriodKind.LAST_7_DAYS),
            measure=Measure.P85,
            as_of=TODAY,
            access=FULL,
        )
        assert view.start is not None and view.end is not None
        # From the project's own row a week ago to today's, live in the project's context.
        assert (view.start.day, view.start.p50, view.start.p85) == (
            start,
            anchor.p50,
            anchor.p85,
        )
        assert (view.end.day, view.end.p50, view.end.p85) == (
            TODAY,
            part.history.p50,
            part.history.p85,
        )
        assert view.reconstructed_days > 0
        assert {item.key for item in view.scope_changes.added} == added


async def test_ora_reads_the_shared_pods_history_under_each_project() -> None:
    registry, _store = await _shared_pod_registry()
    forecasts = registry.forecast_service()
    await forecasts.reconstruct_forecast_days(TENANT, TODAY, days=14)
    await forecasts.record_forecast_days(TENANT, TODAY)
    tool = DeliveryImpactTool(
        principal=_manager(),
        service=registry.delivery_impact_service(),
        forecasts=forecasts,
        repository=registry.graph_repository(),
        as_of=TODAY,
    )

    payload = json.loads(await tool.run({"node_id": "pod-pay", "period": "last_7_days"}))

    by_project = {part["scope"]["project_id"]: part for part in payload["projects"]}
    assert sorted(by_project) == ["audit", "checkout"]
    start = TODAY - timedelta(days=7)
    for scope in (PAY_IN_CHECKOUT, PAY_IN_AUDIT):
        (anchor,) = await forecasts.kept_days(TENANT, scope, start, start)
        then = by_project[scope.project_id]["then"]
        # Each project's "then" is that project's own kept row, not the other's.
        assert then["day"] == start.isoformat()
        assert then["likely_by"] == (anchor.p85.isoformat() if anchor.p85 else None)
    assert by_project["audit"]["scope_changes"]["added"] == 1
    assert by_project["checkout"]["scope_changes"]["added"] == 0


# --- The activity ---------------------------------------------------------------------


async def test_the_snapshot_run_keeps_every_row_of_the_day_when_a_pod_is_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _store = await _shared_pod_registry()
    monkeypatch.setattr(delivery_reports, "_service_registry", lambda: registry)

    with capture_logs() as logs:
        result = await delivery_reports.run_delivery_snapshot_activity(
            delivery_reports.DeliverySnapshotInput(tenant_id=TENANT, observed_at=NOW.isoformat())
        )

    assert result.projects_recorded == 2 and result.forecasts_recorded == 6
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
    """A Postgres keyed (tenant, kind, scope, project, day) that refuses a statement offering
    one key twice, as the real one does."""

    COLUMNS = 19

    def __init__(self, *, fail_from_statement: int | None = None) -> None:
        self.statements: list[int] = []
        self.queries: list[str] = []
        self.rows: dict[tuple[object, ...], tuple[object, ...]] = {}
        self._fail_from = fail_from_statement

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        rows = [tuple(params[i : i + self.COLUMNS]) for i in range(0, len(params), self.COLUMNS)]
        if self._fail_from is not None and len(self.statements) >= self._fail_from:
            raise ConnectionError("the server closed the connection")
        keys = [(row[0], row[1], row[2], row[3], row[4]) for row in rows]
        if len(set(keys)) != len(keys):
            raise RuntimeError("CardinalityViolation: ON CONFLICT DO UPDATE affects a row twice")
        self.statements.append(len(rows))
        self.queries.append(" ".join(query.split()))
        for key, row in zip(keys, rows, strict=True):
            self.rows[key] = row
        return None

    async def fetch(self, query: str, params: Sequence[object] = ()) -> Sequence[dict[str, object]]:
        return []


async def test_postgres_keeps_a_pod_under_two_projects_as_two_rows_on_the_new_key() -> None:
    executor = _PostgresLikeExecutor()
    repository = PostgresForecastDayRepository(executor)

    with capture_logs() as logs:
        await repository.save(
            [
                _record(project_id="checkout", p50=date(2026, 11, 3)),
                _record(project_id="audit", p50=date(2026, 12, 1)),
            ]
        )

    assert executor.statements == [2]
    assert "ON CONFLICT (tenant_id, scope_kind, scope_id, project_id, day)" in executor.queries[0]
    assert executor.rows[(TENANT, "pod", "pod-pay", "checkout", TODAY)][5] == date(2026, 11, 3)
    assert executor.rows[(TENANT, "pod", "pod-pay", "audit", TODAY)][5] == date(2026, 12, 1)
    assert not [log for log in logs if log["event"] == "forecast_day_duplicate_key"]


async def test_postgres_offers_one_row_per_key_and_keeps_the_last_with_a_warning() -> None:
    executor = _PostgresLikeExecutor()
    repository = PostgresForecastDayRepository(executor)
    other_day = _record(day=TODAY - timedelta(days=1))

    with capture_logs() as logs:
        await repository.save(
            [
                _record(project_id="audit", p50=date(2026, 11, 3)),
                other_day,
                _record(project_id="audit", p50=date(2026, 12, 1)),
            ]
        )

    assert executor.statements == [2]
    stored = executor.rows[(TENANT, "pod", "pod-pay", "audit", TODAY)]
    # The last record of the key won.
    assert stored[5] == date(2026, 12, 1)
    (warning,) = [log for log in logs if log["event"] == "forecast_day_duplicate_key"]
    assert warning["log_level"] == "warning"
    assert (warning["key"], warning["project_id"]) == ("pod/pod-pay/2026-10-05", "audit")


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


async def test_the_in_memory_rows_keep_a_pod_under_each_project() -> None:
    repository = InMemoryForecastDayRepository()

    with capture_logs() as logs:
        await repository.save(
            [
                _record(project_id="checkout", p50=date(2026, 11, 3)),
                _record(project_id="audit", p50=date(2026, 12, 1)),
            ]
        )

    for project_id, p50 in (("checkout", date(2026, 11, 3)), ("audit", date(2026, 12, 1))):
        (row,) = await repository.list_between(
            TENANT, _scope(POD_KIND, "pod-pay", project_id), TODAY, TODAY
        )
        assert row.p50 == p50
    assert not [log for log in logs if log["event"] == "forecast_day_duplicate_key"]


async def test_the_in_memory_rows_keep_the_last_of_a_repeated_key_as_postgres_does() -> None:
    repository = InMemoryForecastDayRepository()

    with capture_logs() as logs:
        await repository.save(
            [
                _record(project_id="audit", p50=date(2026, 11, 3)),
                _record(project_id="audit", p50=date(2026, 12, 1)),
            ]
        )

    (row,) = await repository.list_between(
        TENANT, _scope(POD_KIND, "pod-pay", "audit"), TODAY, TODAY
    )
    assert row.p50 == date(2026, 12, 1)
    (warning,) = [log for log in logs if log["event"] == "forecast_day_duplicate_key"]
    assert (warning["key"], warning["project_id"]) == ("pod/pod-pay/2026-10-05", "audit")


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


async def test_a_row_kept_under_one_project_never_stops_a_rebuilt_row_under_the_other() -> None:
    repository = InMemoryForecastDayRepository()
    kept = _record(project_id="audit", p50=date(2026, 11, 3))
    await repository.save([kept])

    rebuilt = _record(project_id="checkout", p50=date(2026, 12, 1), reconstructed=True)
    await repository.save([rebuilt, replace(kept, p50=date(2027, 1, 4), reconstructed=True)])

    # Audit's kept row stays; Checkout's own day is rebuilt beside it.
    assert await repository.list_between(TENANT, kept.scope, TODAY, TODAY) == [kept]
    assert await repository.list_between(TENANT, rebuilt.scope, TODAY, TODAY) == [rebuilt]


def test_the_exception_says_how_many_rows() -> None:
    assert ForecastDaysNotKept(rows=3).rows == 3
    assert "3" in str(ForecastDaysNotKept(rows=3))
