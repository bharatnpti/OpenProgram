"""Delivery dates and forecasts: history, the team's dates, and who may commit what."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.checkin_drift import CHECKIN_DRIFT_FACT_SOURCE, ETA_STATED
from core.application.delivery_service import DeliveryService
from core.application.forecast_service import ForecastService
from core.domain.delivery import DeliveryStage, RequirementsSnapshot
from core.domain.errors import GraphNotFound
from core.domain.forecast import (
    CommitmentError,
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    HistoryForecast,
    OpenItem,
    Release,
    ReleaseMatch,
    ReleaseMatchKind,
    TeamForecast,
    Verdict,
    commitment_from_changes,
    daily_completions,
    history_forecast,
    next_working_day,
    team_forecast,
    verdict,
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
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry

TENANT = "demo"
TODAY = date(2026, 10, 5)  # Monday
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
S = DeliveryStage


def _snapshot(
    day: date, items: dict[str, DeliveryStage], project_id: str = "checkout"
) -> RequirementsSnapshot:
    counts = {stage: 0 for stage in S}
    for stage in items.values():
        counts[stage] += 1
    return RequirementsSnapshot(
        tenant_id=TENANT,
        project_id=project_id,
        day=day,
        stage_counts=counts,
        stage_points={stage: 0.0 for stage in S},
        has_points=False,
        excluded=0,
        unmapped_statuses=(),
        items=items,
        titles={},
        computed_at=NOW,
    )


# --- History ---------------------------------------------------------------------


def test_completions_count_arrivals_in_production_on_consecutive_working_days() -> None:
    friday = date(2026, 10, 2)
    monday = date(2026, 10, 5)
    tuesday = date(2026, 10, 6)
    thursday = date(2026, 10, 8)  # Wednesday is missing: no sample for Thursday
    snapshots = [
        _snapshot(friday, {"A": S.IN_TESTING, "B": S.IN_DEVELOPMENT}),
        _snapshot(monday, {"A": S.PRODUCTION, "B": S.IN_DEVELOPMENT}),
        _snapshot(tuesday, {"A": S.PRODUCTION, "B": S.PRODUCTION, "C": S.PRODUCTION}),
        _snapshot(thursday, {"A": S.PRODUCTION, "B": S.PRODUCTION, "C": S.PRODUCTION}),
    ]

    assert daily_completions(snapshots) == [1.0, 2.0]
    assert daily_completions(snapshots, keys=frozenset({"B"})) == [0.0, 1.0]


def test_the_simulation_is_fixed_by_its_seed_and_orders_its_percentiles() -> None:
    samples = [0.0, 1.0, 2.0, 1.0, 0.0, 3.0, 1.0, 1.0, 0.0, 2.0]

    first = history_forecast(samples, 10, start=TODAY, seed="checkout")
    again = history_forecast(samples, 10, start=TODAY, seed="checkout")

    assert first == again
    assert first.p50 is not None and first.p85 is not None
    assert TODAY < first.p50 <= first.p85
    assert first.p50.weekday() < 5 and first.p85.weekday() < 5


def test_the_simulation_says_why_it_cannot_forecast() -> None:
    none = history_forecast([], 5, start=TODAY, seed="x")
    one = history_forecast([1.0], 5, start=TODAY, seed="x")
    short = history_forecast([1.0] * 4, 5, start=TODAY, seed="x")
    idle = history_forecast([0.0] * 12, 5, start=TODAY, seed="x")
    done = history_forecast([1.0] * 12, 0, start=TODAY, seed="x")

    assert none.p50 is None and none.reason == (
        "No history yet: a forecast needs 10 working days of daily snapshots."
    )
    assert one.reason == "Only 1 working day of history; a forecast needs 10."
    assert short.p50 is None and short.reason == (
        "Only 4 working days of history; a forecast needs 10."
    )
    assert idle.reason == "Nothing reached production in the last 12 working days."
    assert done.p50 == TODAY and done.remaining == 0


def test_next_working_day_skips_the_weekend() -> None:
    assert next_working_day(date(2026, 10, 2)) == date(2026, 10, 5)


# --- The team's dates and the verdict -----------------------------------------------


def test_the_team_date_prefers_an_eta_over_a_due_date() -> None:
    team = team_forecast(
        [
            OpenItem(key="A", eta=date(2026, 10, 20), due=date(2026, 10, 10)),
            OpenItem(key="B", eta=None, due=date(2026, 10, 15)),
            OpenItem(key="C", eta=None, due=None),
        ]
    )

    assert team == TeamForecast(latest=date(2026, 10, 20), latest_key="A", dated=2, undated=1)


def _history(p50: date | None, p85: date | None, remaining: float = 5) -> HistoryForecast:
    return HistoryForecast(
        p50=p50,
        p85=p85,
        remaining=remaining,
        unit="requirements",
        sample_days=20,
        completed_in_sample=9,
    )


_NO_TEAM = TeamForecast(latest=None, latest_key=None, dated=0, undated=0)


@pytest.mark.parametrize(
    ("p50", "p85", "expected"),
    [
        (date(2026, 11, 1), date(2026, 11, 10), Verdict.ON_TRACK),
        (date(2026, 11, 1), date(2026, 11, 20), Verdict.AT_RISK),
        (date(2026, 11, 16), date(2026, 11, 20), Verdict.OFF_TRACK),
    ],
)
def test_history_decides_the_verdict_against_the_target(
    p50: date, p85: date, expected: Verdict
) -> None:
    assert verdict(date(2026, 11, 14), _history(p50, p85), _NO_TEAM) is expected


def test_without_history_the_team_dates_decide() -> None:
    target = date(2026, 11, 14)
    late = TeamForecast(latest=date(2026, 11, 20), latest_key="A", dated=1, undated=0)
    gaps = TeamForecast(latest=date(2026, 11, 1), latest_key="A", dated=1, undated=2)

    assert verdict(target, _history(None, None), late) is Verdict.OFF_TRACK
    assert verdict(target, _history(None, None), gaps) is Verdict.AT_RISK
    assert verdict(target, _history(None, None), _NO_TEAM) is Verdict.NOT_ENOUGH_DATA
    assert verdict(None, _history(None, None), late) is Verdict.NO_DATE
    assert verdict(target, _history(None, None, remaining=0), late) is Verdict.DONE


def test_a_commitment_remembers_its_first_date_and_how_often_it_moved() -> None:
    scope = CommitmentScope(kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout")

    def change(day: int, target: date | None) -> DateChange:
        return DateChange(
            tenant_id=TENANT,
            scope=scope,
            target_date=target,
            changed_at=NOW + timedelta(days=day),
            changed_by="po",
        )

    commitment = commitment_from_changes(
        scope,
        [
            change(2, date(2026, 11, 21)),
            change(0, date(2026, 11, 14)),
            change(1, date(2026, 11, 14)),
            change(3, date(2026, 11, 23)),
        ],
    )

    assert commitment.original_date == date(2026, 11, 14)
    assert commitment.target_date == date(2026, 11, 23)
    assert commitment.times_moved == 2
    assert commitment.moved_days == 9


def test_a_release_holds_the_issues_of_its_fix_version_or_label() -> None:
    by_version = Release(
        tenant_id=TENANT,
        release_id="r1",
        project_id="checkout",
        name="Checkout 1.0",
        match=ReleaseMatch(kind=ReleaseMatchKind.FIX_VERSION, value="r1.0"),
        updated_at=NOW,
        updated_by="po",
    )

    assert by_version.includes({"fix_versions": "R0.9, R1.0"})
    assert not by_version.includes({"fix_versions": "R1.01"})
    assert not by_version.includes({"labels": "R1.0"})


# --- The service over a graph ----------------------------------------------------------


async def _registry() -> tuple[ServiceRegistry, InMemoryGraphStore]:
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
        Developer(tenant_id=TENANT, id="dev-asha", name="Asha"),
        Developer(tenant_id=TENANT, id="dev-dana", name="Dana"),
    ):
        await store.upsert_node(node)
    for parent, child in (
        ("checkout", "pod-pay"),
        ("checkout", "pod-store"),
        ("pod-pay", "dev-asha"),
    ):
        await store.add_edge(_contains(parent, child))
    issues = [
        ("CHK-1", "Done", "done", "pod-pay", "R1", None),
        ("CHK-2", "In Progress", "in_progress", "pod-pay", "R1", "2026-10-30"),
        ("CHK-3", "In QA", "in_progress", "pod-pay", "R1", None),
        ("CHK-4", "To Do", "todo", "pod-store", "R2", "2026-11-20"),
    ]
    for key, status, state, pod, version, due in issues:
        metadata: dict[str, object] = {
            "key": key,
            "status": status,
            "state": state,
            "fix_versions": version,
            "fix_version_dates": json.dumps({"R1": "2026-11-13", "R2": "2026-12-11"}),
        }
        if due:
            metadata["due_date"] = due
        await store.upsert_node(
            Task(tenant_id=TENANT, id=key, name=f"{key} work", metadata=metadata)
        )  # type: ignore[arg-type]
        await store.add_edge(_contains(pod, key))
    await store.append_fact(
        FactEvent(
            tenant_id=TENANT,
            source=CHECKIN_DRIFT_FACT_SOURCE,
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-3"),
            payload={"kind": ETA_STATED, "issue_key": "CHK-3", "eta_date": "2026-11-04"},
            observed_at=NOW,
            correlation_id="eta-1",
        )
    )
    settings = Settings(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        runtime_mode="memory",
    )
    return ServiceRegistry(settings, graph_store=store), store


def _contains(parent: str, child: str) -> GraphEdge:
    return GraphEdge(
        tenant_id=TENANT, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
    )


def _service(registry: ServiceRegistry) -> ForecastService:
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
        clock=lambda: NOW,
        today=lambda: TODAY,
        new_id=lambda: "rel-1",
    )


def _project_scope() -> CommitmentScope:
    return CommitmentScope(kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout")


async def test_the_project_forecast_uses_the_teams_dates_until_history_builds_up() -> None:
    registry, _store = await _registry()
    service = _service(registry)
    await service.set_date(TENANT, _project_scope(), date(2026, 11, 14), note="", actor="po")

    view = await service.project_delivery(TENANT, "checkout", TODAY)

    project = view.project
    assert project.target == date(2026, 11, 14) and project.target_source == "committed"
    assert project.total == 4 and project.open == 3
    assert project.team == TeamForecast(
        latest=date(2026, 11, 20), latest_key="CHK-4", dated=3, undated=0
    )
    assert project.verdict is Verdict.OFF_TRACK
    assert "No history yet: a forecast needs 10 working days of daily snapshots." in project.reasons
    assert any("CHK-4" in reason for reason in project.reasons)
    # The pods split the project: Payments has CHK-1..3, Storefront CHK-4.
    pods = {pod.name: pod for pod in view.pods}
    assert pods["Payments Pod"].open == 2
    assert pods["Payments Pod"].team.latest == date(2026, 11, 4)
    assert pods["Storefront Pod"].open == 1


async def test_a_pod_committed_after_its_project_is_a_reason() -> None:
    registry, _store = await _registry()
    service = _service(registry)
    await service.set_date(TENANT, _project_scope(), date(2026, 11, 14), note="", actor="po")
    await service.set_date(
        TENANT,
        CommitmentScope(kind=CommitmentScopeKind.POD, id="pod-store", project_id="checkout"),
        date(2026, 11, 27),
        note="vendor delay",
        actor="sm",
    )

    view = await service.project_delivery(TENANT, "checkout", TODAY)

    assert (
        "Storefront Pod committed Fri 27 Nov 2026, after the project's Sat 14 Nov 2026."
        in view.project.reasons
    )


async def test_a_release_without_a_committed_date_uses_its_jira_release_date() -> None:
    registry, _store = await _registry()
    service = _service(registry)
    release = await service.save_release(
        TENANT,
        "checkout",
        release_id=None,
        name="Release 1",
        match=ReleaseMatch(kind=ReleaseMatchKind.FIX_VERSION, value="R1"),
        actor="po",
    )

    view = await service.project_delivery(TENANT, "checkout", TODAY)

    (r1,) = view.releases
    assert r1.scope.id == release.release_id
    assert r1.target == date(2026, 11, 13) and r1.target_source == "jira_release"
    assert r1.total == 3 and r1.open == 2
    assert r1.verdict is Verdict.ON_TRACK
    candidates = await service.release_candidates(TENANT, "checkout")
    assert [(item.value, item.issues, item.release_date) for item in candidates[:2]] == [
        ("R1", 3, date(2026, 11, 13)),
        ("R2", 1, date(2026, 12, 11)),
    ]


async def test_a_committed_release_date_that_disagrees_with_jira_is_named() -> None:
    registry, _store = await _registry()
    service = _service(registry)
    release = await service.save_release(
        TENANT,
        "checkout",
        release_id=None,
        name="Release 1",
        match=ReleaseMatch(kind=ReleaseMatchKind.FIX_VERSION, value="R1"),
        actor="po",
    )
    await service.set_date(
        TENANT,
        CommitmentScope(
            kind=CommitmentScopeKind.RELEASE, id=release.release_id, project_id="checkout"
        ),
        date(2026, 11, 20),
        note="",
        actor="po",
    )

    (r1,) = (await service.project_delivery(TENANT, "checkout", TODAY)).releases

    assert "Jira's release date is Fri 13 Nov 2026; the committed date is Fri 20 Nov 2026." in (
        r1.reasons
    )


async def test_history_forecasts_once_enough_days_are_kept() -> None:
    registry, _store = await _registry()
    service = _service(registry)
    snapshots = registry.requirements_snapshot_repository()
    day = TODAY - timedelta(days=28)
    done: dict[str, DeliveryStage] = {}
    index = 0
    while day < TODAY:
        if day.weekday() < 5:
            index += 1
            if index % 3 == 0:
                done[f"OLD-{index}"] = S.PRODUCTION
            await snapshots.save(_snapshot(day, {**done, "CHK-2": S.IN_DEVELOPMENT}))
        day += timedelta(days=1)
    await service.set_date(TENANT, _project_scope(), date(2027, 3, 1), note="", actor="po")

    project = (await service.project_delivery(TENANT, "checkout", TODAY)).project

    assert project.history.p50 is not None and project.history.p85 is not None
    assert project.history.sample_days >= 10
    assert project.verdict is Verdict.ON_TRACK


async def test_dates_are_only_set_on_scopes_that_exist() -> None:
    registry, _store = await _registry()
    service = _service(registry)

    with pytest.raises(GraphNotFound):
        await service.set_date(
            TENANT,
            CommitmentScope(kind=CommitmentScopeKind.POD, id="pod-nope", project_id="checkout"),
            date(2026, 12, 1),
            note="",
            actor="sm",
        )
    with pytest.raises(GraphNotFound):
        await service.set_date(
            TENANT,
            CommitmentScope(kind=CommitmentScopeKind.RELEASE, id="missing", project_id="checkout"),
            date(2026, 12, 1),
            note="",
            actor="po",
        )
    with pytest.raises(CommitmentError):
        await service.set_date(TENANT, _project_scope(), date(2024, 1, 1), note="", actor="po")


# --- API ----------------------------------------------------------------------------------


def _client(settings: Settings) -> tuple[TestClient, ServiceRegistry]:
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    return TestClient(app), app.state.registry


def _as(role: str, user: str = "someone") -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


def _seed(client: TestClient) -> None:
    assert (
        client.post("/config/projects", json={"id": "checkout", "name": "Checkout"}).status_code
        == 201
    )
    assert (
        client.post("/config/pods", json={"id": "pod-pay", "name": "Payments"}).status_code == 201
    )
    assert client.post("/config/pods/pod-pay/projects/checkout").status_code in {200, 201}


def test_api_product_owner_commits_the_project_date_and_everyone_reads_it(
    settings: Settings,
) -> None:
    client, _registry = _client(settings)
    with client:
        _seed(client)
        saved = client.put(
            "/projects/checkout/delivery-date",
            json={"target_date": "2026-11-14", "note": "agreed with sales"},
            headers=_as("po"),
        )
        moved = client.put(
            "/projects/checkout/delivery-date",
            json={"target_date": "2026-11-21"},
            headers=_as("po"),
        )
        read = client.get("/projects/checkout/delivery", headers=_as("exec"))
        exec_set = client.put(
            "/projects/checkout/delivery-date", json={"target_date": None}, headers=_as("exec")
        )
        dev_read = client.get("/projects/checkout/delivery", headers=_as("dev"))

    assert saved.status_code == 200
    assert moved.json()["times_moved"] == 1 and moved.json()["moved_days"] == 7
    assert read.status_code == 200
    body = read.json()
    assert body["project"]["target"] == "2026-11-21"
    assert body["project"]["commitment"]["changes"][0]["note"] == "agreed with sales"
    assert [pod["name"] for pod in body["pods"]] == ["Payments"]
    assert exec_set.status_code == 403
    assert dev_read.status_code == 403


def test_api_a_scrum_master_sets_only_their_own_pods_date(settings: Settings) -> None:
    client, registry = _client(settings)
    with client:
        _seed(client)
        client.put(
            "/config/pods/pod-pay",
            json={"name": "Payments", "metadata": {"escalation_sm_member_id": "dev-dana"}},
        )
        own = client.put(
            "/projects/checkout/pods/pod-pay/delivery-date",
            json={"target_date": "2026-11-10"},
            headers=_as("sm", "dev-dana"),
        )
        other = client.put(
            "/projects/checkout/pods/pod-pay/delivery-date",
            json={"target_date": "2026-11-10"},
            headers=_as("sm", "dev-zoe"),
        )
        pod_view = client.get("/pods/pod-pay/delivery", headers=_as("sm", "dev-dana"))
        manager = client.put(
            "/projects/checkout/pods/pod-pay/delivery-date",
            json={"target_date": "2026-11-12"},
            headers=_as("mgr", "dev-eli"),
        )

    assert own.status_code == 200, own.text
    assert other.status_code == 403
    assert pod_view.status_code == 200
    assert pod_view.json()["can_set_dates"] is True
    assert pod_view.json()["projects"][0]["pod"]["target"] == "2026-11-10"
    assert manager.status_code == 200


def test_api_releases_are_defined_listed_and_scope_the_requirements(settings: Settings) -> None:
    client, _registry = _client(settings)
    with client:
        _seed(client)
        created = client.post(
            "/projects/checkout/releases",
            json={"name": "Release 1", "match_kind": "fix_version", "match_value": "R1"},
            headers=_as("po"),
        )
        release_id = created.json()["release_id"]
        listed = client.get("/projects/checkout/releases", headers=_as("mgr"))
        requirements = client.get(
            f"/projects/checkout/requirements?release_id={release_id}", headers=_as("po")
        )
        unknown = client.get("/projects/checkout/requirements?release_id=nope", headers=_as("po"))
        dated = client.put(
            f"/projects/checkout/releases/{release_id}/delivery-date",
            json={"target_date": "2026-12-01"},
            headers=_as("po"),
        )
        forbidden = client.post(
            "/projects/checkout/releases",
            json={"name": "X", "match_kind": "label", "match_value": "x"},
            headers=_as("sm"),
        )
        removed = client.delete(f"/projects/checkout/releases/{release_id}", headers=_as("po"))

    assert created.status_code == 201
    assert [item["name"] for item in listed.json()] == ["Release 1"]
    assert requirements.json()["release_name"] == "Release 1"
    assert unknown.status_code == 404
    assert dated.status_code == 200
    assert forbidden.status_code == 403
    assert removed.status_code == 204
