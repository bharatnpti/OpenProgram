"""Why the date moved: the breakdown kept per day, the period read, its gate and its surfaces.

A made-up month on Checkout Revamp (Payments Pod, run by Dana, and the
Storefront Pod). Planning was marked complete on Thu 1 Oct with the change
label "CR" and two grace days. After it, CHK-20 joined on Tue 6 Oct carrying
the label, and CHK-21 on Thu 8 Oct without it (reported by Asha). The
committed date moved on Wed 7 Oct, "two changes approved". CHK-4 is open past
its due date of 2 Oct. Eight older requirements (OLD-1 to OLD-8) were in
development from the start; one reaches production every third working day
(OLD-5 on 2 Oct, OLD-6 on 7 Oct, OLD-7 today), so the forecast has history
from the end of September.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.impact_dtos import MarkerResponse
from api.main import create_app
from api.timeline_dtos import TimelineMarkerResponse
from config.settings import Settings
from core.application.ask_investigation import PLAN_MORE_REMINDER, STEP_GUIDANCE
from core.application.ask_service import (
    ASK_TOOL_GUIDANCE,
    AskService,
    DeliveryImpactTool,
    without_unread_negatives,
)
from core.application.blocker_resolution import BlockerResolutionService
from core.application.checkin_drift import CHECKIN_DRIFT_FACT_SOURCE, ETA_STATED
from core.application.delivery_scope import DeliveryScopeService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.impact_service import (
    ImpactAccess,
    ImpactDetail,
    ImpactPeriod,
    MarkerView,
    PeriodKind,
)
from core.application.persona_views import PersonaViewService
from core.application.risk_service import RiskService
from core.domain.auth import Principal, Role
from core.domain.baseline import (
    BaselineItem,
    BaselineKind,
    BaselineScopeKind,
    PlanningBaseline,
)
from core.domain.delivery import DeliveryStage, RequirementsSnapshot, SnapshotItem
from core.domain.errors import AuthorizationDenied
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    ForecastDayRecord,
    ReleaseMatch,
    ReleaseMatchKind,
    Verdict,
)
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    NodeKind,
    Pod,
    Project,
    Task,
)
from core.domain.impact import (
    DayAttribution,
    Driver,
    Item,
    Measure,
    NotTakenApart,
    ScopeGroup,
    Shift,
    Step,
    attribution_from_payload,
    attribution_payload,
    working_days_moved,
)
from core.domain.llm import LlmResponse, LlmToolCall, TokenUsage
from infra.adapters.llm.fake import FakeLlmProvider
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry

TENANT = "demo"
TODAY = date(2026, 10, 12)  # Monday
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
PLANNED = date(2026, 10, 1)
S = DeliveryStage
PROJECT = CommitmentScope(kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout")
PAY = CommitmentScope(kind=CommitmentScopeKind.POD, id="pod-pay", project_id="checkout")
STORE = CommitmentScope(kind=CommitmentScopeKind.POD, id="pod-store", project_id="checkout")
FULL = ImpactAccess(detail=ImpactDetail.FULL, added_by=True)


class _RegistryOnTheDay(ServiceRegistry):
    def _tenant_today(self) -> date:
        return TODAY


#: When each requirement joined the scope: before the history starts, unless named.
_JOINED = {"CHK-20": date(2026, 10, 6), "CHK-21": date(2026, 10, 8)}
_OPEN = {"CHK-2": S.IN_DEVELOPMENT, "CHK-3": S.IN_DEVELOPMENT, "CHK-4": S.RAISED}
_TASKS: dict[str, tuple[str, str, str, dict[str, object]]] = {
    "CHK-2": ("In Progress", "in_progress", "pod-pay", {}),
    "CHK-3": ("In Progress", "in_progress", "pod-pay", {}),
    "CHK-4": ("To Do", "todo", "pod-store", {"due_date": "2026-10-02"}),
    "CHK-20": (
        "To Do",
        "todo",
        "pod-pay",
        {"labels": "CR", "created_at": "2026-10-06T09:00:00+00:00"},
    ),
    "CHK-21": (
        "To Do",
        "todo",
        "pod-pay",
        {"created_at": "2026-10-08T09:00:00+00:00", "reporter_member_id": "dev-asha"},
    ),
}


def _working_days(first: date, last: date) -> list[date]:
    days = []
    day = first
    while day <= last:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _snapshot(day: date, items: dict[str, DeliveryStage]) -> RequirementsSnapshot:
    counts = {stage: 0 for stage in S}
    for stage in items.values():
        counts[stage] += 1
    return RequirementsSnapshot(
        tenant_id=TENANT,
        project_id="checkout",
        day=day,
        stage_counts=counts,
        stage_points={stage: 0.0 for stage in S},
        has_points=False,
        excluded=0,
        unmapped_statuses=(),
        items=items,
        titles={key: f"{key} work" for key in items},
        details={key: SnapshotItem(type="Story") for key in items},
        computed_at=NOW,
    )


async def _world() -> tuple[ServiceRegistry, InMemoryGraphStore]:
    store = InMemoryGraphStore()
    for node in (
        Project(tenant_id=TENANT, id="checkout", name="Checkout Revamp"),
        Pod(
            tenant_id=TENANT,
            id="pod-pay",
            name="Payments Pod",
            metadata={"escalation_sm_member_id": "dev-dana"},
        ),
        Pod(tenant_id=TENANT, id="pod-store", name="Storefront Pod"),
        Developer(tenant_id=TENANT, id="dev-asha", name="Asha Rao"),
        Developer(tenant_id=TENANT, id="dev-dana", name="Dana Kim"),
        Developer(tenant_id=TENANT, id="dev-mina", name="Mina Patel"),
    ):
        await store.upsert_node(node)
    for parent, child in (
        ("checkout", "pod-pay"),
        ("checkout", "pod-store"),
        ("pod-pay", "dev-asha"),
    ):
        await store.add_edge(_contains(parent, child))
    snapshots: list[RequirementsSnapshot] = []
    older = {f"OLD-{n}": S.IN_DEVELOPMENT for n in range(1, 9)}
    for index, day in enumerate(_working_days(TODAY - timedelta(days=28), TODAY), start=1):
        if index % 3 == 0:
            older[f"OLD-{index // 3}"] = S.PRODUCTION
        items = {**older, **_OPEN}
        items.update({key: S.RAISED for key, joined in _JOINED.items() if day >= joined})
        if day < TODAY:
            snapshots.append(_snapshot(day, items))
    for key, stage in older.items():
        if stage is S.PRODUCTION:
            await _task(store, key, "Done", "done", "pod-pay", {})
        else:
            await _task(store, key, "In Progress", "in_progress", "pod-pay", {})
    for key, (status, state, pod, extra) in _TASKS.items():
        await _task(store, key, status, state, pod, extra)
    settings = Settings(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        runtime_mode="memory",
        # The acting-as headers name the person and role a request is made as.
        demo_mode=True,
    )
    registry = _RegistryOnTheDay(settings, graph_store=store)
    await _plan(registry, snapshots)
    return registry, store


async def _plan(registry: ServiceRegistry, snapshots: list[RequirementsSnapshot]) -> None:
    """The stored month, the baseline of 1 Oct and the committed date's two changes."""
    for snapshot in snapshots:
        await registry.requirements_snapshot_repository().save(snapshot)
    baseline_day = next(item for item in snapshots if item.day == PLANNED)
    await registry.planning_baseline_repository().add(
        PlanningBaseline(
            tenant_id=TENANT,
            baseline_id="base-1",
            scope_kind=BaselineScopeKind.PROJECT,
            scope_id="checkout",
            project_id="checkout",
            kind=BaselineKind.DATE,
            planned_on=PLANNED,
            items={
                key: BaselineItem(stage=stage, due=date(2026, 10, 2) if key == "CHK-4" else None)
                for key, stage in baseline_day.items.items()
            },
            reconstructed=False,
            snapshot_day=None,
            change_label="CR",
            grace_working_days=2,
            set_by="dev-mina",
            set_at=datetime(2026, 10, 1, 16, 0, tzinfo=UTC),
        )
    )
    for when, target, note in (
        (datetime(2026, 9, 20, 9, tzinfo=UTC), date(2026, 11, 20), ""),
        (datetime(2026, 10, 7, 9, tzinfo=UTC), date(2026, 11, 27), "two changes approved"),
    ):
        await registry.commitment_repository().append(
            DateChange(
                tenant_id=TENANT,
                scope=PROJECT,
                target_date=target,
                changed_at=when,
                changed_by="dev-mina",
                note=note,
            )
        )


async def _task(
    store: InMemoryGraphStore,
    key: str,
    status: str,
    state: str,
    pod: str,
    extra: dict[str, object],
) -> None:
    metadata = {"key": key, "status": status, "state": state, "issue_type": "Story", **extra}
    await store.upsert_node(
        Task(tenant_id=TENANT, id=key, name=f"{key} work", metadata=metadata)  # type: ignore[arg-type]
    )
    await store.add_edge(_contains(pod, key))


def _contains(parent: str, child: str) -> GraphEdge:
    return GraphEdge(
        tenant_id=TENANT, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
    )


async def _reconstructed() -> tuple[ServiceRegistry, InMemoryGraphStore]:
    registry, store = await _world()
    await registry.forecast_service().reconstruct_forecast_days(TENANT, TODAY, days=30)
    return registry, store


def _principal(role: Role, subject: str = "someone") -> Principal:
    return Principal(tenant_id=TENANT, subject=subject, roles=frozenset({role}))


# --- The breakdown kept per day -----------------------------------------------------------


async def test_each_reconstructed_day_keeps_why_it_moved_and_the_days_add_up() -> None:
    registry, _store = await _reconstructed()

    rows = await registry.forecast_day_repository().list_between(
        TENANT, PROJECT, TODAY - timedelta(days=30), TODAY
    )

    assert rows and all(row.reconstructed for row in rows)
    attributed = [row for row in rows[1:] if row.attribution is not None]
    assert len(attributed) == len(rows) - 1
    for previous, row in zip(rows, rows[1:], strict=False):
        attribution = attribution_from_payload(row.attribution)
        assert attribution is not None and attribution.from_day == previous.day
        if attribution.taken_apart:
            assert previous.p85 is not None and row.p85 is not None
            total = sum(step.shift.p85 for step in attribution.steps)
            assert total == working_days_moved(previous.p85, row.p85)
    joined = {
        item.key: row.day
        for row in rows
        if (attribution := attribution_from_payload(row.attribution)) is not None
        for item in attribution.added
    }
    assert joined == {"CHK-20": date(2026, 10, 6), "CHK-21": date(2026, 10, 8)}


async def test_the_pods_get_their_own_rows_with_their_own_keys() -> None:
    registry, _store = await _reconstructed()

    pay = await registry.forecast_day_repository().list_between(
        TENANT, PAY, TODAY - timedelta(days=30), TODAY
    )
    store_rows = await registry.forecast_day_repository().list_between(
        TENANT, STORE, TODAY - timedelta(days=30), TODAY
    )

    assert pay and store_rows
    added_to_pay = {
        item.key
        for row in pay
        if (attribution := attribution_from_payload(row.attribution)) is not None
        for item in attribution.added
    }
    assert added_to_pay == {"CHK-20", "CHK-21"}
    assert all(
        not attribution.added
        for row in store_rows
        if (attribution := attribution_from_payload(row.attribution)) is not None
    )


async def test_the_hourly_run_keeps_todays_breakdown_against_the_day_before() -> None:
    registry, _store = await _reconstructed()
    service = registry.forecast_service()

    recorded = await service.record_forecast_days(TENANT, TODAY)
    (row,) = await registry.forecast_day_repository().list_between(TENANT, PROJECT, TODAY, TODAY)

    assert recorded == 3  # the project and its two pods
    attribution = attribution_from_payload(row.attribution)
    assert attribution is not None and attribution.from_day == date(2026, 10, 9)
    assert not row.reconstructed
    live = await service.forecast_day(TENANT, PROJECT, TODAY)
    assert live is not None and live.attribution == row.attribution


async def test_a_kept_day_without_a_breakdown_gains_one_and_keeps_its_forecast() -> None:
    registry, _store = await _world()
    service = registry.forecast_service()
    friday = date(2026, 10, 9)
    await service.record_forecast_days(TENANT, friday)
    repository = registry.forecast_day_repository()
    (kept,) = await repository.list_between(TENANT, PROJECT, friday, friday)
    await repository.save([replace(kept, attribution=None)])

    await service.reconstruct_forecast_days(TENANT, TODAY, days=10)
    (after,) = await repository.list_between(TENANT, PROJECT, friday, friday)

    assert not after.reconstructed and (after.p50, after.p85) == (kept.p50, kept.p85)
    attribution = attribution_from_payload(after.attribution)
    assert attribution is not None and attribution.from_day == date(2026, 10, 8)


# --- The period read ------------------------------------------------------------------


async def test_since_planning_the_drivers_add_up_and_the_additions_are_grouped() -> None:
    registry, _store = await _reconstructed()
    service = registry.delivery_impact_service()

    view = await service.impact(
        TENANT,
        PROJECT,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=TODAY,
        access=FULL,
    )

    assert view.start is not None and view.end is not None
    assert (view.start.day, view.end.day) == (PLANNED, TODAY)
    assert view.period_words == "since planning (1 Oct)"
    assert view.baseline_day == PLANNED and view.change_label == "CR"
    assert view.moved_working_days is not None
    assert sum(driver.working_days for driver in view.drivers) == pytest.approx(
        view.moved_working_days, abs=0.05
    )
    assert view.start.p85 and view.end.p85
    assert view.moved_working_days == working_days_moved(view.start.p85, view.end.p85)
    # At most three causes: one bar for scope added, its items each with their group.
    assert {driver.kind for driver in view.drivers} <= {
        "scope_added",
        "scope_removed",
        "team_speed",
    }
    (scope,) = [driver for driver in view.drivers if driver.kind == Driver.SCOPE_ADDED.value]
    added = {item.key: item.group for item in scope.items}
    assert added == {"CHK-20": ScopeGroup.CHANGE_REQUEST, "CHK-21": ScopeGroup.NO_LABEL}
    assert scope.group is None and scope.note == "1 change request, 1 without the CR label"
    items = {
        item.key: item
        for driver in view.drivers
        if driver.kind == Driver.SCOPE_ADDED.value
        for item in driver.items
    }
    assert items["CHK-20"].change_label is True and items["CHK-21"].change_label is False
    assert items["CHK-21"].added_by == "Asha Rao"
    assert items["CHK-20"].day == date(2026, 10, 6)
    # The additions pushed the date out; finishing pulled it back; worst first.
    assert sum(d.working_days for d in view.drivers if d.kind == "scope_added") > 0
    team = next(driver for driver in view.drivers if driver.kind == "team_speed")
    assert [item.key for item in team.items] == ["OLD-5", "OLD-6", "OLD-7"]
    assert team.working_days < 0
    assert list(view.drivers) == sorted(view.drivers, key=lambda driver: -driver.working_days)
    assert all(driver.working_days != 0 or driver.items for driver in view.drivers)
    # Where the date stands, then why, in a leader's words.
    assert view.summary.startswith("Likely by 12 Nov, 2 weeks before the committed 27 Nov. ")
    assert "Since planning (1 Oct) it " in view.summary
    assert "added scope pushed it back 7 days" in view.summary
    assert not any(
        word in view.summary.lower() for word in ("85%", "throughput", "window", "replay", "pace")
    )
    assert view.reconstructed_days > 0
    assert view.link == "/projects/checkout?row=project:checkout#timeline"


async def test_the_committed_date_lane_names_who_and_why_beside_the_addition() -> None:
    registry, _store = await _reconstructed()

    view = await registry.delivery_impact_service().impact(
        TENANT,
        PROJECT,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=TODAY,
        access=FULL,
    )

    (change,) = view.committed_changes
    assert (change.from_date, change.to_date, change.on) == (
        date(2026, 11, 20),
        date(2026, 11, 27),
        date(2026, 10, 7),
    )
    assert (change.by, change.note, change.after_added) == (
        "Mina Patel",
        "two changes approved",
        ("CHK-20",),
    )
    assert view.end is not None and view.end.committed == date(2026, 11, 27)
    assert view.start is not None and view.start.committed == date(2026, 11, 20)


async def test_evidence_is_the_late_and_the_undated_never_a_share() -> None:
    registry, _store = await _reconstructed()

    view = await registry.delivery_impact_service().impact(
        TENANT,
        PROJECT,
        period=ImpactPeriod(kind=PeriodKind.LAST_7_DAYS),
        measure=Measure.P85,
        as_of=TODAY,
        access=FULL,
    )

    assert view.evidence is not None
    (late,) = view.evidence.late
    assert (late.key, late.date, late.date_kind, late.working_days_late) == (
        "CHK-4",
        date(2026, 10, 2),
        "due",
        6,
    )
    assert set(view.evidence.undated) == {"CHK-2", "CHK-3", "CHK-20", "CHK-21", "OLD-8"}
    assert "late" not in {driver.kind for driver in view.drivers}
    team = next(driver for driver in view.drivers if driver.kind == "team_speed")
    assert team.note == "2 requirements finished; CHK-4 is late"


async def test_the_markers_are_per_day_and_kind_and_carry_the_change_label() -> None:
    registry, _store = await _reconstructed()

    view = await registry.delivery_impact_service().impact(
        TENANT,
        PROJECT,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=TODAY,
        access=FULL,
    )

    markers = {(marker.day, marker.kind, marker.change_label): marker for marker in view.markers}
    assert markers[(date(2026, 10, 6), "scope_added", True)].items[0].key == "CHK-20"
    assert markers[(date(2026, 10, 8), "scope_added", False)].items[0].key == "CHK-21"
    assert (date(2026, 10, 3), "late", None) in markers
    assert (date(2026, 10, 7), "committed_date_changed", None) in markers
    assert all(marker.count == len(marker.items) for marker in view.markers if marker.items)


# --- Days per event on the markers ---------------------------------------------------
#
# A made-up week of kept rows on Checkout Revamp, Mon 5 Oct to Fri 9 Oct:
#
# - Tue: CHK-40 and CHK-41 join with the CR label, CHK-42 without it, and CHK-43, a bug
#   counted as a requirement; together they pushed the dates out 4 (50%) and 8 (85%)
#   working days; a re-estimate added one more on the 85% date, and time passing took
#   one and two back.
# - Wed (a reconstructed row): CHK-42 left the scope (-1, -2), OLD-1 reopened (+1, +2).
# - Thu: CHK-42 came back (+1, +3).
# - Fri: CHK-44 joined, but the day's breakdown cannot be read; the dates moved +3, +4.

MON, TUE, WED, THU, FRI = (date(2026, 10, day) for day in (5, 6, 7, 8, 9))
_WEEK: dict[date, dict[Driver, tuple[float, float]]] = {
    TUE: {
        Driver.TIME_PASSED: (-1, -2),
        Driver.SCOPE_ADDED: (4, 8),
        Driver.RE_ESTIMATED: (0, 1),
    },
    WED: {Driver.TIME_PASSED: (-1, -1), Driver.SCOPE_REMOVED: (-1, -2), Driver.REOPENED: (1, 2)},
    THU: {Driver.SCOPE_ADDED: (1, 3)},
    FRI: {Driver.SCOPE_ADDED: (3, 4)},
}
#: The re-estimate on Tuesday: it joins the scope-added bar but is no event's.
_RE_ESTIMATE = {Measure.P50: 0.0, Measure.P85: 1.0}


def _by_working_days(day: date, count: float) -> date:
    """The working day ``count`` working days from ``day``, later or earlier."""
    step, left = (1 if count >= 0 else -1), abs(int(count))
    while left:
        day += timedelta(days=step)
        if day.weekday() < 5:
            left -= 1
    return day


def _requirement(key: str, kind: str = "Story") -> Item:
    return Item(key=key, title=f"{key} work", type=kind)


async def _week(registry: ServiceRegistry, store: InMemoryGraphStore, friday: str) -> None:
    """Keep the week's rows; ``friday`` says why Friday's breakdown cannot be read:
    not_taken_apart, not_chained (it runs from Wednesday), or no_date_yet (no likely
    date before Friday, so nothing before it is counted)."""
    for key in ("CHK-40", "CHK-41"):
        await _task(
            store, key, "To Do", "todo", "pod-pay", {"labels": "CR", "created_at": "2026-10-06"}
        )
    items: dict[date, dict[str, tuple[Item, ...]]] = {
        TUE: {
            "added": (
                *(_requirement(key) for key in ("CHK-40", "CHK-41", "CHK-42")),
                _requirement("CHK-43", "Bug"),
            )
        },
        WED: {"removed": (_requirement("CHK-42"),), "reopened": (_requirement("OLD-1"),)},
        THU: {"added": (_requirement("CHK-42"),)},
        FRI: {"added": (_requirement("CHK-44"),)},
    }
    p50, p85 = date(2026, 11, 2), date(2026, 11, 16)
    rows = [_kept_row(MON, p50, p85, None)]
    previous = MON
    for day, steps in _WEEK.items():
        to50 = _by_working_days(p50, sum(move for move, _ in steps.values()))
        to85 = _by_working_days(p85, sum(move for _, move in steps.values()))
        unreadable = day == FRI and friday == "not_taken_apart"
        kept_steps = tuple(
            Step(driver=driver, shift=Shift(p50=move50, p85=move85))
            for driver, (move50, move85) in steps.items()
        )
        breakdown = DayAttribution(
            from_day=WED if day == FRI and friday == "not_chained" else previous,
            to_day=day,
            unit="requirements",
            from_p50=p50,
            from_p85=p85,
            to_p50=to50,
            to_p85=to85,
            steps=() if unreadable else kept_steps,
            not_taken_apart=NotTakenApart.REPLAY_DIFFERS if unreadable else None,
            added=items[day].get("added", ()),
            removed=items[day].get("removed", ()),
            reopened=items[day].get("reopened", ()),
        )
        rows.append(
            _kept_row(day, to50, to85, attribution_payload(breakdown), reconstructed=day == WED)
        )
        p50, p85, previous = to50, to85, day
    if friday == "no_date_yet":
        rows = [replace(row, p50=None, p85=None) if row.day < FRI else row for row in rows]
    await registry.forecast_day_repository().save(rows)


def _kept_row(
    day: date,
    p50: date | None,
    p85: date | None,
    attribution: dict[str, object] | None,
    *,
    reconstructed: bool = False,
) -> ForecastDayRecord:
    return ForecastDayRecord(
        tenant_id=TENANT,
        scope=PROJECT,
        day=day,
        p50=p50,
        p85=p85,
        verdict=Verdict.ON_TRACK,
        target_date=date(2026, 11, 27),
        team_latest=None,
        team_latest_key=None,
        remaining=10.0,
        unit="requirements",
        sample_days=20,
        completed_in_sample=6.0,
        min_sample_days=10,
        computed_at=NOW,
        reconstructed=reconstructed,
        attribution=attribution,
    )


async def _week_markers(
    friday: str = "not_taken_apart",
) -> dict[tuple[date, str, bool | None], MarkerView]:
    registry, store = await _world()
    await _week(registry, store, friday)
    markers = await registry.delivery_impact_service().timeline_markers(
        TENANT,
        PROJECT,
        start=MON,
        as_of=FRI,
        access=FULL,
        open_requirements=(),
        baseline=None,
        stages={},
    )
    return {(marker.day, marker.kind, marker.change_label): marker for marker in markers}


async def test_a_days_scope_added_step_is_split_among_its_markers_by_item_count() -> None:
    markers = await _week_markers()

    labelled = markers[(TUE, "scope_added", True)]
    unlabelled = markers[(TUE, "scope_added", False)]
    bug = markers[(TUE, "bug", None)]
    assert [item.key for item in labelled.items] == ["CHK-40", "CHK-41"]
    assert [item.key for item in unlabelled.items] == ["CHK-42"]
    assert [item.key for item in bug.items] == ["CHK-43"]
    # The timeline reads the 85% date: 2, 1 and 1 of the 4 items of an 8-day step.
    assert [(m.working_days, m.p85_days, m.p50_days) for m in (labelled, unlabelled, bug)] == [
        (4.0, 4.0, 2.0),
        (2.0, 2.0, 1.0),
        (2.0, 2.0, 1.0),
    ]
    assert (
        sum(m.p85_days or 0 for m in (labelled, unlabelled, bug))
        == _WEEK[TUE][Driver.SCOPE_ADDED][1]
    )
    assert all(m.taken_apart is True and not m.reconstructed for m in (labelled, unlabelled, bug))


async def test_removed_and_reopened_get_their_own_steps_on_their_own_day() -> None:
    markers = await _week_markers()

    removed = markers[(WED, "scope_removed", None)]
    reopened = markers[(WED, "reopened", None)]
    assert [item.key for item in removed.items] == ["CHK-42"]
    assert (removed.working_days, removed.p50_days, removed.p85_days) == (-2.0, -1.0, -2.0)
    assert [item.key for item in reopened.items] == ["OLD-1"]
    assert (reopened.working_days, reopened.p50_days, reopened.p85_days) == (2.0, 1.0, 2.0)
    # Wednesday's row was a reconstruction: its events say so, and only theirs.
    assert removed.reconstructed and reopened.reconstructed
    assert not any(marker.reconstructed for marker in markers.values() if marker.day != WED)


async def test_an_item_added_removed_and_added_again_is_marked_each_day_with_its_share() -> None:
    markers = await _week_markers()

    again = markers[(THU, "scope_added", False)]
    assert [item.key for item in again.items] == ["CHK-42"]
    assert (again.count, again.working_days, again.p50_days) == (1, 3.0, 1.0)
    # Its first day keeps Tuesday's share, not the two days' summed.
    assert markers[(TUE, "scope_added", False)].working_days == 2.0


@pytest.mark.parametrize("friday", ["not_taken_apart", "not_chained", "no_date_yet"])
async def test_a_day_whose_breakdown_cannot_be_read_is_not_known(friday: str) -> None:
    markers = await _week_markers(friday)

    joined = markers[(FRI, "scope_added", False)]
    assert [item.key for item in joined.items] == ["CHK-44"] and joined.count == 1
    assert joined.taken_apart is False
    assert (joined.working_days, joined.p50_days, joined.p85_days) == (None, None, None)
    # Before the period's first likely date nothing is counted, as summed() counts it.
    scope = [
        marker
        for (day, kind, _label), marker in markers.items()
        if day < FRI and kind in {"scope_added", "bug", "scope_removed", "reopened"}
    ]
    assert len(scope) == 6
    assert all(marker.taken_apart is (friday != "no_date_yet") for marker in scope)


async def test_the_committed_dates_move_carries_no_days() -> None:
    markers = await _week_markers()

    moved = markers[(WED, "committed_date_changed", None)]
    assert (moved.taken_apart, moved.working_days, moved.reconstructed) == (None, None, False)


@pytest.mark.parametrize("measure", [Measure.P85, Measure.P50])
async def test_the_events_and_team_speed_add_up_to_the_move(measure: Measure) -> None:
    registry, store = await _world()
    await _week(registry, store, "not_taken_apart")

    view = await registry.delivery_impact_service().impact(
        TENANT,
        PROJECT,
        period=ImpactPeriod(kind=PeriodKind.FROM_DAY, from_day=MON),
        measure=measure,
        as_of=FRI,
        access=FULL,
    )

    assert view.moved_working_days == {Measure.P85: 13.0, Measure.P50: 6.0}[measure]
    events = sum(marker.working_days or 0.0 for marker in view.markers)
    team = next(
        (driver.working_days for driver in view.drivers if driver.kind == "team_speed"), 0.0
    )
    # Friday is not known: its move is under team speed, never handed to CHK-44.
    assert events == {Measure.P85: 11.0, Measure.P50: 5.0}[measure]
    assert events + team + _RE_ESTIMATE[measure] == pytest.approx(view.moved_working_days, abs=0.05)
    labelled = next(m for m in view.markers if (m.day, m.change_label) == (TUE, True))
    assert labelled.working_days == {Measure.P85: 4.0, Measure.P50: 2.0}[measure]


def test_the_marker_responses_carry_the_days() -> None:
    marker = MarkerView(
        day=TUE,
        kind="scope_added",
        count=3,
        change_label=False,
        working_days=2.5,
        p50_days=1.0,
        p85_days=2.5,
        taken_apart=True,
        reconstructed=True,
    )

    for response in (TimelineMarkerResponse.from_view(marker), MarkerResponse.from_view(marker)):
        assert (
            response.working_days,
            response.p50_days,
            response.p85_days,
            response.taken_apart,
            response.reconstructed,
        ) == (2.5, 1.0, 2.5, True, True)
    late = MarkerResponse.from_view(MarkerView(day=TUE, kind="late", count=1))
    assert (late.working_days, late.taken_apart, late.reconstructed) == (None, None, False)


async def test_a_pod_reads_only_its_own_requirements() -> None:
    registry, _store = await _reconstructed()

    view = await registry.delivery_impact_service().impact(
        TENANT,
        PAY,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=TODAY,
        access=FULL,
    )

    assert view.name == "Payments Pod" and view.project_name == "Checkout Revamp"
    assert view.scope_changes.added_count == 2
    assert view.evidence is not None and view.evidence.late == ()
    assert view.link == "/projects/checkout?row=pod:pod-pay#timeline"


async def test_before_planning_is_marked_it_reads_the_last_thirty_days_and_says_so() -> None:
    registry, _store = await _reconstructed()
    service = registry.delivery_impact_service()
    release_less = CommitmentScope(
        kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout"
    )

    view = await service.impact(
        TENANT,
        release_less,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=date(2026, 9, 30),
        access=FULL,
    )

    assert view.period_words == "in the last 30 days"
    assert any("Planning is not marked complete" in note for note in view.not_known)
    # With no baseline, the change label is the default and nothing is "without" it yet.
    assert view.change_label == "CR"


async def test_without_kept_rows_today_alone_is_read_live() -> None:
    registry, _store = await _world()

    view = await registry.delivery_impact_service().impact(
        TENANT,
        PROJECT,
        period=ImpactPeriod(kind=PeriodKind.LAST_7_DAYS),
        measure=Measure.P50,
        as_of=TODAY,
        access=FULL,
    )

    assert view.end is not None and view.end.day == TODAY
    assert view.measure is Measure.P50


# --- Who reads what -------------------------------------------------------------------


async def test_executives_read_it_all_but_who_added_an_item() -> None:
    registry, _store = await _reconstructed()
    service = registry.delivery_impact_service()

    access = await service.access(_principal(Role.EXEC), PROJECT)
    view = await service.impact(
        TENANT,
        PROJECT,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=TODAY,
        access=access,
    )

    assert access == ImpactAccess(detail=ImpactDetail.FULL, added_by=False)
    added = [item for driver in view.drivers for item in driver.items if item.group]
    assert added and all(item.added_by is None for item in added)
    assert all(item.added_by is None for marker in view.markers for item in marker.items)


async def test_a_scrum_master_reads_their_pod_whole_and_another_pods_dates_only() -> None:
    registry, _store = await _reconstructed()
    service = registry.delivery_impact_service()
    dana = _principal(Role.SM, "dev-dana")

    own = await service.access(dana, PAY)
    other = await service.access(dana, STORE)
    view = await service.impact(
        TENANT,
        STORE,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=TODAY,
        access=other,
    )

    # Their own pod's blockers too: the scrum master reads the pod blocker read, and
    # hygiene's issues there, so an addition Jira hygiene has open is ringed.
    assert own == ImpactAccess(detail=ImpactDetail.FULL, added_by=True, blockers=True, hygiene=True)
    assert other == ImpactAccess(detail=ImpactDetail.DATES, added_by=False)
    assert view.markers == () and view.evidence is None
    assert all(driver.items == () for driver in view.drivers)


async def test_a_developer_reads_none_of_it_not_even_for_their_own_pod() -> None:
    registry, _store = await _reconstructed()
    service = registry.delivery_impact_service()
    asha = _principal(Role.DEV, "dev-asha")

    with pytest.raises(AuthorizationDenied):
        await service.access(asha, PAY)
    with pytest.raises(AuthorizationDenied):
        await service.access(asha, PROJECT)


# --- The day report -----------------------------------------------------------------


async def test_the_day_report_says_why_the_85_date_moved_since_the_last_one() -> None:
    registry, _store = await _reconstructed()
    line = await registry.delivery_impact_service().report_line(
        TENANT, PROJECT, since=date(2026, 10, 9), day=TODAY
    )

    report = await registry.day_report_builder().build(TENANT, "checkout", TODAY)

    # OLD-7 finished today: the date came in, which is said, but never as a threat.
    assert line is not None and not line.leads
    assert line.text == (
        "The likely date moved 5 working days earlier: finishing OLD-7 pulled it in 5 days."
    )
    changed = next(
        group
        for section in report.sections
        if section.title == "Where we stand"
        for group in section.groups
        if group.heading.startswith("What changed")
    )
    assert changed.lines[-1] == line.text
    important = next(section for section in report.sections if section.title == "Most important")
    assert line.text not in important.lines
    assert report.facts is not None and line.text in report.facts.progress.other_changes


async def test_an_unlabelled_addition_that_moved_the_date_out_leads_the_day() -> None:
    registry, _store = await _reconstructed()
    service = registry.delivery_impact_service()

    added = await service.report_line(
        TENANT, PROJECT, since=date(2026, 10, 7), day=date(2026, 10, 8)
    )
    labelled = await service.report_line(
        TENANT, PROJECT, since=date(2026, 10, 5), day=date(2026, 10, 6)
    )

    assert added is not None and added.leads
    assert added.text == (
        "The likely date moved 7 working days later: nothing finished pushed it back 4 days, "
        "added scope (CHK-21, without the CR label) pushed it back 3."
    )
    assert labelled is not None
    assert "added scope (CHK-20, change request) pushed it back 4 days" in labelled.text


async def test_no_line_without_a_previous_report_or_a_forecast() -> None:
    registry, _store = await _reconstructed()
    service = registry.delivery_impact_service()

    assert await service.report_line(TENANT, PROJECT, since=None, day=TODAY) is None
    # Mid-September the forecast had too little history to move.
    assert (
        await service.report_line(TENANT, PROJECT, since=date(2026, 9, 15), day=date(2026, 9, 16))
        is None
    )


# --- The read endpoint ---------------------------------------------------------------


def _client(registry: ServiceRegistry) -> TestClient:
    return TestClient(create_app(settings=registry.settings, registry=registry))


def _as(role: str, user: str = "someone") -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


async def test_the_endpoint_reads_as_the_service_and_refuses_a_developer() -> None:
    registry, _store = await _reconstructed()
    path = f"/projects/checkout/delivery/impact?as_of={TODAY.isoformat()}"
    with _client(registry) as client:
        po = client.get(path, headers=_as("po", "dev-mina"))
        exec_ = client.get(path, headers=_as("exec"))
        dev = client.get(path + "&pod_id=pod-pay", headers=_as("dev", "dev-asha"))
        sm_other = client.get(path + "&pod_id=pod-store", headers=_as("sm", "dev-dana"))
        both = client.get(path + "&pod_id=pod-pay&release_id=r", headers=_as("po"))
        missing = client.get(
            "/projects/nowhere/delivery/impact?as_of=2026-10-12", headers=_as("po")
        )
        from_without_day = client.get(path + "&period=from", headers=_as("po"))

    assert po.status_code == 200, po.text
    body = po.json()
    assert body["period"] == "since_baseline" and body["detail"] == "full"
    assert sum(driver["working_days"] for driver in body["drivers"]) == pytest.approx(
        body["moved_working_days"], abs=0.05
    )
    added_by = {
        item["key"]: item["added_by"]
        for driver in body["drivers"]
        for item in driver["items"]
        if item["group"]
    }
    assert added_by.get("CHK-21") == "Asha Rao"
    assert exec_.status_code == 200
    assert all(
        item["added_by"] is None for driver in exec_.json()["drivers"] for item in driver["items"]
    )
    assert dev.status_code == 403
    assert sm_other.status_code == 200 and sm_other.json()["detail"] == "dates"
    assert sm_other.json()["markers"] == []
    assert both.status_code == 422
    assert missing.status_code == 404
    assert from_without_day.status_code == 422


# --- Ora's tool -----------------------------------------------------------------------


def _tool(registry: ServiceRegistry, principal: Principal) -> DeliveryImpactTool:
    return DeliveryImpactTool(
        principal=principal,
        service=registry.delivery_impact_service(),
        forecasts=registry.forecast_service(),
        repository=registry.graph_repository(),
        as_of=TODAY,
    )


async def test_ora_reads_what_moved_the_date_since_planning_with_markers_and_link() -> None:
    registry, _store = await _reconstructed()

    payload = json.loads(
        await _tool(registry, _principal(Role.MGR, "dev-mina")).run({"node_id": "checkout"})
    )

    assert payload["scope"] == {
        "kind": "project",
        "id": "checkout",
        "name": "Checkout Revamp",
        "project_id": "checkout",
        "project_name": "Checkout Revamp",
    }
    assert payload["period"] == "since_baseline" and payload["planning_complete_on"] == "2026-10-01"
    assert payload["then"]["day"] == "2026-10-01" and payload["now"]["day"] == "2026-10-12"
    # The likely date and the committed date are named apart at both ends.
    assert set(payload["now"]) == {"day", "likely_by", "likely_around", "committed_date"}
    assert payload["now"]["committed_date"] == "2026-11-27"
    groups = {
        item.get("group") for driver in payload["drivers"] for item in driver.get("items", [])
    }
    assert {"change_request", "no_label"} <= groups
    assert {driver["kind"] for driver in payload["drivers"]} <= {
        "scope_added",
        "scope_removed",
        "team_speed",
    }
    assert payload["committed_changes"][0]["by"] == "Mina Patel"
    assert payload["evidence"]["late"][0]["key"] == "CHK-4"
    assert payload["link"] == "/projects/checkout?row=project:checkout#timeline"
    assert any(marker["kind"] == "scope_added" for marker in payload["markers"])
    # No person's id anywhere: names only, on the item's row.
    text = json.dumps(payload)
    assert "dev-asha" not in text and "dev-mina" not in text


async def test_ora_covers_a_pod_per_project_and_says_a_refusal_in_words() -> None:
    registry, _store = await _reconstructed()

    pod = json.loads(await _tool(registry, _principal(Role.PO)).run({"node_id": "pod-pay"}))
    refused = json.loads(
        await _tool(registry, _principal(Role.DEV, "dev-asha")).run({"node_id": "pod-pay"})
    )
    task = json.loads(await _tool(registry, _principal(Role.PO)).run({"node_id": "CHK-2"}))
    week = json.loads(
        await _tool(registry, _principal(Role.PO)).run(
            {"node_id": "checkout", "period": "last_7_days", "measure": "p50"}
        )
    )

    assert pod["pod_name"] == "Payments Pod"
    assert [project["scope"]["project_name"] for project in pod["projects"]] == ["Checkout Revamp"]
    assert refused == {"error": "Not allowed."}
    assert "error" in task
    assert week["period"] == "last_7_days" and week["measure"] == "p50"


async def test_the_change_label_is_the_tenants_own_word() -> None:
    registry, store = await _reconstructed()
    repository = registry.planning_baseline_repository()
    (baseline,) = await repository.list_for_scope(TENANT, BaselineScopeKind.PROJECT, "checkout")
    await repository.add(
        replace(
            baseline,
            baseline_id="base-2",
            change_label="AGREED",
            set_at=datetime(2026, 10, 2, 9, tzinfo=UTC),
        )
    )

    view = await registry.delivery_impact_service().impact(
        TENANT,
        PROJECT,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=TODAY,
        access=FULL,
    )

    (scope,) = [driver for driver in view.drivers if driver.kind == "scope_added"]
    # CHK-20 carries "CR", not this tenant's "AGREED": both additions are now unlabelled.
    assert {item.group for item in scope.items} == {ScopeGroup.NO_LABEL}
    assert view.change_label == "AGREED"
    assert scope.note == "2 without the AGREED label"


async def test_who_added_it_is_the_author_of_the_change_that_brought_it_in() -> None:
    registry, store = await _world()
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="issue_change",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-20"),
            payload={
                "key": "CHK-20",
                # It became a requirement type: that brought it into the project's scope.
                "field": "issue_type",
                "from": "Task",
                "to": "Story",
                "author_member_id": "dev-dana",
                "project_key": "CHK",
                "history_id": "h-1",
            },
            observed_at=datetime(2026, 10, 6, 8, tzinfo=UTC),
            correlation_id="issue_change:demo:CHK-20:h-1:issue_type",
        )
    )
    await registry.forecast_service().reconstruct_forecast_days(TENANT, TODAY, days=30)

    view = await registry.delivery_impact_service().impact(
        TENANT,
        PROJECT,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=TODAY,
        access=FULL,
    )

    items = {item.key: item for item in view.scope_changes.added}
    assert items["CHK-20"].added_by == "Dana Kim"


# --- What Ora is told -----------------------------------------------------------------


def test_ora_is_told_when_to_read_what_moved_the_date() -> None:
    assert "is answered from delivery_impact" in ASK_TOOL_GUIDANCE
    assert "never add up or rank what people added" in ASK_TOOL_GUIDANCE
    assert "A question about why a date moved" in STEP_GUIDANCE
    assert "for a question about why a date moved: what moved it" in PLAN_MORE_REMINDER


def test_no_scope_added_stands_only_on_a_finished_read_of_it() -> None:
    answer = "Checkout Revamp's date moved.\n• No scope was added since planning."

    assert without_unread_negatives(answer, read=["delivery_forecast"]) == (
        "Checkout Revamp's date moved."
    )
    assert without_unread_negatives(answer, read=["delivery_impact"]) == answer
    assert (
        without_unread_negatives(answer, read=["delivery_impact"], pending=["delivery_impact"])
        == "Checkout Revamp's date moved."
    )


def _llm_response(text: str = "", tool_calls: tuple[LlmToolCall, ...] = ()) -> LlmResponse:
    return LlmResponse(
        tenant_id=TENANT,
        text=text,
        model="test-model",
        usage=TokenUsage(
            prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0.0, latency_ms=1.0
        ),
        trace_id="trace-impact",
        tool_calls=tool_calls,
    )


async def test_ora_is_told_what_is_not_known_first_and_the_three_dates_apart() -> None:
    registry, _store = await _reconstructed()
    manager = _principal(Role.MGR, "dev-mina")

    def tool_on(day: date) -> DeliveryImpactTool:
        return DeliveryImpactTool(
            principal=manager,
            service=registry.delivery_impact_service(),
            forecasts=registry.forecast_service(),
            repository=registry.graph_repository(),
            as_of=day,
        )

    # Mid-September: no planning baseline yet, and too little history for a likely date.
    early = json.loads(await tool_on(date(2026, 9, 16)).run({"node_id": "checkout"}))
    first = early["summary"].splitlines()[0]
    # After the day it is read for, what is not known comes before anything else.
    assert list(early)[:4] == ["as_of", "not_known", "summary", "dates"]
    assert first.startswith(
        "Not known: planning is not marked complete yet, so this covers the last 30 days; "
        "no likely date yet: only 2 working days of history"
    )
    assert early["not_known"] == [
        "Planning is not marked complete yet, so this covers the last 30 days.",
        "No likely date yet: only 2 working days of history; a forecast needs 10.",
    ]
    # Today, with nothing unknown, the first line still says so.
    today = json.loads(await tool_on(TODAY).run({"node_id": "checkout"}))
    assert today["summary"].splitlines()[0] == (
        "Not known: nothing; the likely date is known at both ends of the period."
    )
    # The committed date, the likely date and the team's date are three named things.
    assert set(today["dates"]) == {"committed_date", "likely_date", "team_date"}
    assert "Only a person moves it" in today["dates"]["committed_date"]
    assert "neither the committed date nor the likely date" in today["dates"]["team_date"]
    assert today["dates"]["team_date"].startswith(
        "The team's own date: the latest finish date the team gave (ETA or due date) "
        "across the open requirements."
    )
    assert "say it as 'team's own date'" in ASK_TOOL_GUIDANCE
    assert {"team_date_then", "team_date_now"} <= set(today["team_date"])
    assert "p85" not in json.dumps(today["now"]) and "from" not in today
    # And the guidance says to lead with it and keep the dates apart.
    assert "begin the answer with that line, word for word, and never drop it" in (
        ASK_TOOL_GUIDANCE
    )
    assert "is never 'the project's date moved'" in ASK_TOOL_GUIDANCE
    assert "keeping the committed date, the likely date and the team's date apart" in (
        STEP_GUIDANCE
    )


async def test_ora_answers_what_moved_the_checkout_date_through_the_tool_loop() -> None:
    registry, store = await _reconstructed()
    llm = FakeLlmProvider(
        responses=[
            _llm_response(
                tool_calls=(
                    LlmToolCall(
                        id="call-1",
                        name="delivery_impact",
                        arguments={"node_id": "checkout", "period": "since_baseline"},
                    ),
                )
            ),
            _llm_response(
                text=json.dumps(
                    {
                        "answer": "Checkout Revamp's 85% date held since planning:\n"
                        "• Scope added: CHK-20 (change request), CHK-21 (no label)",
                        "references": ["checkout"],
                    }
                )
            ),
        ]
    )
    ask = AskService(
        llm_provider=llm,
        graph_repository=store,
        time_series_repository=store,
        flow_metrics_service=FlowMetricsService(
            graph_repository=store, time_series_repository=store
        ),
        persona_view_service=PersonaViewService(
            graph_repository=store,
            status_repository=store,
            rollup_repository=store,
            time_series_repository=store,
        ),
        risk_service=RiskService(
            graph_repository=store,
            time_series_repository=store,
            status_repository=store,
            blocker_resolution=BlockerResolutionService(store, store),
        ),
        forecast_service=registry.forecast_service(),
        delivery_scope_service=DeliveryScopeService(store, store, today=lambda: TODAY),
        release_readiness_service=registry.release_readiness_service(),
        impact_service=registry.delivery_impact_service(),
        model="test-model",
    )

    view = await ask.ask(
        principal=_principal(Role.PO, "dev-mina"),
        question="What moved the Checkout project's date since planning?",
        correlation_id="ask-impact",
        as_of=TODAY,
    )

    assert view.tools_used == ("delivery_impact",)
    assert "delivery_impact" in {tool.name for tool in llm.requests[0].tools}
    result = json.loads(llm.requests[1].tool_results[0].content)
    assert "Since planning (1 Oct) it did not move: added scope pushed it back" in result["summary"]
    scope = next(driver for driver in result["drivers"] if driver["kind"] == "scope_added")
    assert scope["note"] == "1 change request, 1 without the CR label"
    assert view.references == ("checkout",)


# --- A release ----------------------------------------------------------------------


async def test_a_release_reads_its_own_keys_and_a_fix_version_move_is_moved_in() -> None:
    registry, store = await _world()
    forecasts = registry.forecast_service()
    release = await forecasts.save_release(
        TENANT,
        "checkout",
        release_id=None,
        name="Release 1",
        match=ReleaseMatch(kind=ReleaseMatchKind.FIX_VERSION, value="R1"),
        actor="dev-mina",
    )
    in_release = {"CHK-2", "CHK-3", *(f"OLD-{n}" for n in range(1, 9))}
    for snapshot in await registry.requirements_snapshot_repository().list_between(
        TENANT, "checkout", TODAY - timedelta(days=40), TODAY
    ):
        keys = in_release | ({"CHK-4"} if snapshot.day >= date(2026, 10, 7) else set())
        await registry.requirements_snapshot_repository().save(
            replace(
                _snapshot(snapshot.day, {k: v for k, v in snapshot.items.items() if k in keys}),
                project_id=f"release:{release.release_id}",
            )
        )
    for key in in_release | {"CHK-4"}:
        task = await store.get_node(TENANT, key)
        assert task is not None
        extra = {"created_at": "2026-09-01T09:00:00+00:00"} if key == "CHK-4" else {}
        await store.upsert_node(
            replace(task, metadata={**task.metadata, "fix_versions": "R1", **extra})
        )
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="issue_change",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-4"),
            payload={
                "key": "CHK-4",
                "field": "fix_version",
                "from": None,
                "to": "R1",
                "author_member_id": "dev-dana",
                "project_key": "CHK",
                "history_id": "h-4",
            },
            observed_at=datetime(2026, 10, 7, 8, tzinfo=UTC),
            correlation_id="issue_change:demo:CHK-4:h-4:fix_version",
        )
    )
    await forecasts.reconstruct_forecast_days(TENANT, TODAY, days=30)
    scope = CommitmentScope(
        kind=CommitmentScopeKind.RELEASE, id=release.release_id, project_id="checkout"
    )

    view = await registry.delivery_impact_service().impact(
        TENANT,
        scope,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=TODAY,
        access=FULL,
    )

    assert view.name == "Release 1"
    added = {item.key: item for item in view.scope_changes.added}
    # CHK-20 and CHK-21 are not in the release; CHK-4 moved in on 7 Oct, by Dana's change.
    assert set(added) == {"CHK-4"}
    assert added["CHK-4"].group is ScopeGroup.MOVED_IN
    assert added["CHK-4"].added_by == "Dana Kim"
    assert view.link == f"/projects/checkout?row=release:{release.release_id}#timeline"


# --- The team's date ------------------------------------------------------------------


async def test_the_team_date_lane_says_which_requirement_moved_it_and_why() -> None:
    registry, store = await _world()
    for when, eta, fact_id in (
        (datetime(2026, 10, 6, 9, tzinfo=UTC), "2026-10-20", "eta-1"),
        (datetime(2026, 10, 8, 9, tzinfo=UTC), "2026-10-27", "eta-2"),
    ):
        await store.append_fact(
            FactEvent(
                tenant_id=TENANT,
                source=CHECKIN_DRIFT_FACT_SOURCE,
                entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-3"),
                payload={"kind": ETA_STATED, "issue_key": "CHK-3", "eta_date": eta},
                observed_at=when,
                correlation_id=fact_id,
            )
        )
    await registry.forecast_service().reconstruct_forecast_days(TENANT, TODAY, days=30)

    view = await registry.delivery_impact_service().impact(
        TENANT,
        PROJECT,
        period=ImpactPeriod(kind=PeriodKind.SINCE_BASELINE),
        measure=Measure.P85,
        as_of=TODAY,
        access=FULL,
    )

    team = view.team_date
    assert team is not None
    assert (team.from_date, team.from_key) == (date(2026, 10, 2), "CHK-4")
    assert (team.to_date, team.to_key) == (date(2026, 10, 27), "CHK-3")
    overtaken, moved = team.moves
    assert (overtaken.day, overtaken.cause, overtaken.to_key) == (
        date(2026, 10, 6),
        "overtaken",
        "CHK-3",
    )
    assert (moved.day, moved.cause) == (date(2026, 10, 8), "eta_moved")
    assert moved.words == "CHK-3's ETA moved 20 Oct → 27 Oct (stated 8 Oct)."
