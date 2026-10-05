"""Requirements by delivery stage: placing statuses, daily snapshots, and the view over time."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.delivery_service import DeliveryService
from core.domain.delivery import (
    DEFAULT_STAGE_MAPPING,
    DeliveryStage,
    RequirementsSnapshot,
    StageMapping,
    StageMappingError,
    StageMove,
    place,
    stage_moves,
    validated_mapping,
)
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    Developer,
    EdgeKind,
    GraphEdge,
    Pod,
    Project,
    SprintNode,
    Task,
)
from infra.persistence.in_memory_delivery import (
    InMemoryDeliverySettingsRepository,
    InMemoryRequirementsSnapshotRepository,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_delivery import (
    PostgresRequirementsSnapshotRepository,
    mapping_from_json,
    mapping_to_json,
)

TENANT = "demo"
TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 17, 0, tzinfo=UTC)
S = DeliveryStage


# --- Placing a status --------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "state", "stage", "mapped"),
    [
        ("To Do", "todo", S.RAISED, True),
        ("ready for development", "todo", S.GROOMED, True),
        ("In  Review", "in_progress", S.IN_DEVELOPMENT, True),
        ("In QA", "in_progress", S.IN_TESTING, True),
        ("UAT", "in_progress", S.BUSINESS_TESTING, True),
        ("Released", "done", S.PRODUCTION, True),
        # Not in the mapping: the broad state decides, and it is reported unmapped.
        ("Waiting for Vendor", "blocked", S.IN_DEVELOPMENT, False),
        ("Parked", "todo", S.RAISED, False),
        (None, "done", S.PRODUCTION, False),
    ],
)
def test_a_status_lands_in_its_mapped_stage_or_falls_back_on_its_state(
    status: str | None, state: str, stage: DeliveryStage, mapped: bool
) -> None:
    placement = place(DEFAULT_STAGE_MAPPING, status=status, state=state)

    assert (placement.stage, placement.mapped) == (stage, mapped)


def test_an_excluded_status_counts_nowhere() -> None:
    placement = place(DEFAULT_STAGE_MAPPING, status="won't do", state="done")

    assert placement.stage is None
    assert placement.mapped is True


def test_a_mapping_is_tidied_and_refuses_a_status_in_two_places() -> None:
    mapping = validated_mapping(
        {S.RAISED: ["  Backlog ", "backlog", ""], S.PRODUCTION: ["Done"]},
        excluded_statuses=["Duplicate"],
        requirement_types=["Story", "Bug"],
    )
    assert mapping.statuses[S.RAISED] == ("Backlog",)
    assert mapping.statuses[S.GROOMED] == ()

    with pytest.raises(StageMappingError) as caught:
        validated_mapping(
            {S.IN_TESTING: ["QA"], S.BUSINESS_TESTING: ["qa"]},
            excluded_statuses=["Done"],
            requirement_types=[],
        )
    assert '"qa" is in both In testing and Business testing' in str(caught.value)

    with pytest.raises(StageMappingError, match='"done" is in Production and also not counted'):
        validated_mapping(
            {S.PRODUCTION: ["Done"]}, excluded_statuses=["done"], requirement_types=[]
        )


def test_requirement_types_limit_what_counts() -> None:
    mapping = StageMapping(statuses={}, requirement_types=("Story",))

    assert mapping.counts_type("story") is True
    assert mapping.counts_type("Sub-task") is False
    assert mapping.counts_type(None) is False
    assert StageMapping(statuses={}).counts_type(None) is True


def _snapshot(
    day: date,
    items: Mapping[str, DeliveryStage],
    *,
    project_id: str = "proj",
    titles: Mapping[str, str] | None = None,
) -> RequirementsSnapshot:
    counts = {stage: 0 for stage in DeliveryStage}
    for stage in items.values():
        counts[stage] += 1
    return RequirementsSnapshot(
        tenant_id=TENANT,
        project_id=project_id,
        day=day,
        stage_counts=counts,
        stage_points={stage: 0.0 for stage in DeliveryStage},
        has_points=False,
        excluded=0,
        unmapped_statuses=(),
        items=dict(items),
        titles=dict(titles) if titles is not None else {key: f"title {key}" for key in items},
        computed_at=NOW,
    )


def test_moves_name_each_change_and_a_first_snapshot_reports_none() -> None:
    before = _snapshot(
        date(2026, 10, 2), {"A-1": S.IN_DEVELOPMENT, "A-2": S.RAISED, "A-3": S.IN_TESTING}
    )
    after = _snapshot(TODAY, {"A-1": S.IN_TESTING, "A-2": S.RAISED, "A-4": S.RAISED})

    assert stage_moves(before, after) == (
        StageMove(key="A-1", title="title A-1", from_stage=S.IN_DEVELOPMENT, to_stage=S.IN_TESTING),
        StageMove(key="A-4", title="title A-4", from_stage=None, to_stage=S.RAISED),
        StageMove(key="A-3", title="title A-3", from_stage=S.IN_TESTING, to_stage=None),
    )
    assert stage_moves(None, after) == ()


def test_percent_complete_uses_points_only_when_every_requirement_has_them() -> None:
    by_count = _snapshot(
        TODAY, {"A-1": S.PRODUCTION, "A-2": S.RAISED, "A-3": S.RAISED, "A-4": S.RAISED}
    )
    assert by_count.percent_complete == 25.0

    by_points = RequirementsSnapshot(
        **{
            **by_count.__dict__,
            "has_points": True,
            "stage_points": {**by_count.stage_points, S.PRODUCTION: 8.0, S.RAISED: 2.0},
        }
    )
    assert by_points.percent_complete == 80.0
    assert _snapshot(TODAY, {}).percent_complete is None


# --- The service over a graph ------------------------------------------------------------


async def _graph() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    await store.upsert_node(Project(tenant_id=TENANT, id="checkout", name="Checkout Revamp"))
    await store.upsert_node(Project(tenant_id=TENANT, id="insights", name="Insights"))
    await store.upsert_node(SprintNode(tenant_id=TENANT, id="sprint-7", name="Sprint 7"))
    await store.upsert_node(Pod(tenant_id=TENANT, id="pod-pay", name="Payments Pod"))
    await store.upsert_node(Developer(tenant_id=TENANT, id="dev-asha", name="Asha"))
    await store.add_edge(_contains("checkout", "sprint-7"))
    await store.add_edge(_contains("checkout", "pod-pay"))
    issues = [
        ("CHK-1", "Story", "In QA", "in_progress", 3),
        ("CHK-2", "Story", "UAT", "in_progress", 5),
        ("CHK-3", "Story", "Done", "done", 8),
        ("CHK-4", "Story", "Waiting for Vendor", "blocked", None),
        ("CHK-5", "Story", "Won't Do", "done", 2),
        ("CHK-6", "Sub-task", "In Progress", "in_progress", None),
    ]
    for key, issue_type, status, state, points in issues:
        metadata: dict[str, object] = {
            "key": key,
            "status": status,
            "state": state,
            "issue_type": issue_type,
            "project_key": "CHK",
        }
        if points is not None:
            metadata["story_points"] = float(points)
        await store.upsert_node(
            Task(tenant_id=TENANT, id=key, name=f"{key} work", metadata=metadata)
        )  # type: ignore[arg-type]
        await store.add_edge(_contains("sprint-7", key))
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT, from_node_id="dev-asha", to_node_id="CHK-1", kind=EdgeKind.ASSIGNED_TO
        )
    )
    # Another project's ticket, reached only through a shared pod member, is not counted.
    await store.upsert_node(
        Task(
            tenant_id=TENANT,
            id="INS-1",
            name="insights",
            metadata={"key": "INS-1", "status": "In QA"},
        )
    )
    await store.add_edge(_contains("insights", "INS-1"))
    await store.add_edge(_contains("pod-pay", "dev-asha"))
    return store


def _contains(parent: str, child: str) -> GraphEdge:
    return GraphEdge(
        tenant_id=TENANT, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
    )


def _service(
    store: InMemoryGraphStore,
    snapshots: InMemoryRequirementsSnapshotRepository | None = None,
    settings: InMemoryDeliverySettingsRepository | None = None,
) -> DeliveryService:
    return DeliveryService(
        graph_repository=store,
        settings_repository=settings or InMemoryDeliverySettingsRepository(),
        snapshot_repository=snapshots or InMemoryRequirementsSnapshotRepository(),
        clock=lambda: NOW,
        today=lambda: TODAY,
    )


async def test_todays_requirements_are_counted_live_by_stage() -> None:
    view = await _service(await _graph()).requirements(TENANT, "checkout", TODAY)

    counts = {item.stage: item.count for item in view.stages}
    assert counts == {
        S.RAISED: 0,
        S.GROOMED: 0,
        S.IN_DEVELOPMENT: 2,  # CHK-4 by its blocked state, CHK-6 as In Progress
        S.IN_TESTING: 1,
        S.BUSINESS_TESTING: 1,
        S.PRODUCTION: 1,
    }
    assert view.live is True and view.available is True
    assert view.total == 5
    assert view.excluded == 1
    assert view.unmapped_statuses == ("Waiting for Vendor",)
    # Not every requirement has points, so completion is by count.
    assert view.has_points is False
    assert view.percent_complete == 20.0
    assert all(item.change is None for item in view.stages)
    first = next(item for item in view.requirements if item.item.key == "CHK-1")
    assert first.assignee_name == "Asha"
    assert first.item.story_points == 3.0
    assert "INS-1" not in {item.item.key for item in view.requirements}


async def test_requirement_types_and_points_follow_the_saved_mapping() -> None:
    store = await _graph()
    settings = InMemoryDeliverySettingsRepository()
    service = _service(store, settings=settings)
    mapping = validated_mapping(
        {**DEFAULT_STAGE_MAPPING.statuses, S.IN_DEVELOPMENT: ("In Progress", "Waiting for Vendor")},
        excluded_statuses=DEFAULT_STAGE_MAPPING.excluded_statuses,
        requirement_types=["Story"],
    )
    await service.save_stage_mapping(TENANT, mapping, actor="admin")

    view = await service.requirements(TENANT, "checkout", TODAY)

    assert view.total == 4
    assert view.unmapped_statuses == ()
    assert {item.item.key for item in view.requirements} == {"CHK-1", "CHK-2", "CHK-3", "CHK-4"}


async def test_snapshots_make_a_timeline_and_say_what_moved_since_the_last_one() -> None:
    store = await _graph()
    snapshots = InMemoryRequirementsSnapshotRepository()
    service = _service(store, snapshots)
    yesterday = date(2026, 10, 4)
    await snapshots.save(
        _snapshot(
            date(2026, 10, 3),
            {"CHK-1": S.IN_TESTING, "CHK-2": S.BUSINESS_TESTING},
            project_id="checkout",
        )
    )
    await snapshots.save(
        _snapshot(
            yesterday,
            {"CHK-1": S.IN_TESTING, "CHK-2": S.IN_TESTING, "CHK-9": S.RAISED},
            project_id="checkout",
            titles={"CHK-9": "dropped work"},
        )
    )

    view = await service.requirements(TENANT, "checkout", TODAY)

    assert [point.day for point in view.timeline] == [date(2026, 10, 3), yesterday, TODAY]
    assert view.previous_day == yesterday
    change = {item.stage: item.change for item in view.stages}
    assert change[S.IN_TESTING] == -1 and change[S.RAISED] == -1 and change[S.PRODUCTION] == 1
    moves = {move.key: (move.from_stage, move.to_stage) for move in view.moves}
    assert moves["CHK-2"] == (S.IN_TESTING, S.BUSINESS_TESTING)
    assert moves["CHK-9"] == (S.RAISED, None)
    assert moves["CHK-3"] == (None, S.PRODUCTION)
    since = {item.item.key: item.in_stage_since for item in view.requirements}
    # CHK-1 has been in testing since the 3rd; CHK-2 reached business testing today.
    assert since["CHK-1"] == date(2026, 10, 3)
    assert since["CHK-2"] == TODAY


async def test_an_earlier_day_reads_its_stored_snapshot_or_says_none_was_kept() -> None:
    store = await _graph()
    snapshots = InMemoryRequirementsSnapshotRepository()
    service = _service(store, snapshots)
    assert await service.record_snapshots(TENANT, TODAY) == 2

    later = DeliveryService(
        graph_repository=store,
        settings_repository=InMemoryDeliverySettingsRepository(),
        snapshot_repository=snapshots,
        clock=lambda: NOW,
        today=lambda: date(2026, 10, 9),
    )
    kept = await later.requirements(TENANT, "checkout", TODAY)
    missing = await later.requirements(TENANT, "checkout", date(2026, 10, 1))

    assert kept.live is False and kept.available is True
    assert kept.total == 5
    assert {item.item.key for item in kept.requirements} >= {"CHK-1", "CHK-3"}
    assert missing.available is False and missing.total == 0 and missing.percent_complete is None


async def test_only_a_project_has_requirements() -> None:
    service = _service(await _graph())

    with pytest.raises(GraphNotFound):
        await service.requirements(TENANT, "pod-pay", TODAY)


async def test_observed_statuses_show_where_each_lands_most_common_first() -> None:
    observed = await _service(await _graph()).observed_statuses(TENANT)

    by_status = {item.status: item for item in observed}
    assert by_status["In QA"].issues == 2
    assert observed[0].status == "In QA"
    assert by_status["Won't Do"].stage is None
    assert by_status["Waiting for Vendor"].mapped is False
    assert by_status["In Progress"].issue_types == ("Sub-task",)


# --- API --------------------------------------------------------------------------------


def _app(settings: Settings, **overrides: object) -> TestClient:
    return TestClient(create_app(settings=settings.model_copy(update=overrides)))


def test_api_saves_the_stage_mapping_and_previews_it(settings: Settings) -> None:
    with _app(settings) as client:
        default = client.get("/config/delivery/stages")
        saved = client.put(
            "/config/delivery/stages",
            json={
                "stages": {"raised": ["Backlog"], "production": ["Done", "Released"]},
                "excluded_statuses": ["Duplicate"],
                "requirement_types": ["Story"],
            },
        )
        conflict = client.put(
            "/config/delivery/stages",
            json={"stages": {"raised": ["Done"], "production": ["Done"]}},
        )
        preview = client.post(
            "/config/delivery/statuses/preview", json={"stages": {"production": ["Done"]}}
        )
        reread = client.get("/config/delivery/stages")

    assert default.json()["is_default"] is True
    assert [stage["stage"] for stage in default.json()["stages"]] == [
        "raised",
        "groomed",
        "in_development",
        "in_testing",
        "business_testing",
        "production",
    ]
    assert saved.status_code == 200
    assert reread.json()["is_default"] is False
    assert reread.json()["stages"][-1]["statuses"] == ["Done", "Released"]
    assert reread.json()["requirement_types"] == ["Story"]
    assert conflict.status_code == 422
    assert '"Done" is in both Raised and Production' in conflict.json()["detail"]
    assert preview.status_code == 200


def _create_project(client: TestClient) -> str:
    response = client.post("/config/projects", json={"id": "checkout", "name": "Checkout Revamp"})
    assert response.status_code == 201
    return "checkout"


def test_api_serves_a_projects_requirements_to_whoever_reads_its_progress(
    settings: Settings,
) -> None:
    with _app(settings) as client:
        project_id = _create_project(client)
        response = client.get(f"/projects/{project_id}/requirements")
        missing = client.get("/projects/no-such-project/requirements")

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == project_id
    assert body["project_name"] == "Checkout Revamp"
    assert [stage["count"] for stage in body["stages"]] == [0] * 6
    assert body["live"] is True
    assert body["percent_complete"] is None
    assert missing.status_code == 404


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("po", 200), ("mgr", 200), ("exec", 200), ("dev", 403), ("sm", 403)],
)
def test_requirements_need_project_progress_access(
    settings: Settings, role: str, allowed: int
) -> None:
    headers = {"x-openprogram-dev-user": "someone", "x-openprogram-dev-roles": role}
    with _app(settings, demo_mode=True) as client:
        project_id = _create_project(client)
        response = client.get(f"/projects/{project_id}/requirements", headers=headers)
        stages = client.get("/config/delivery/stages", headers=headers)

    assert response.status_code == allowed
    assert stages.status_code == 403


# --- Postgres -------------------------------------------------------------------------------


@dataclass
class _RecordingExecutor:
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


def test_the_mapping_round_trips_through_json() -> None:
    mapping = validated_mapping(
        {S.RAISED: ["Backlog"], S.PRODUCTION: ["Done"]},
        excluded_statuses=["Duplicate"],
        requirement_types=["Story"],
    )

    assert mapping_from_json(json.loads(json.dumps(mapping_to_json(mapping)))) == mapping


async def test_a_snapshot_round_trips_through_postgres() -> None:
    snapshot = _snapshot(TODAY, {"CHK-1": S.IN_TESTING, "CHK-3": S.PRODUCTION})
    executor = _RecordingExecutor()
    repository = PostgresRequirementsSnapshotRepository(executor)

    await repository.save(snapshot)
    query, params = executor.calls[0]
    assert "ON CONFLICT (tenant_id, project_id, day) DO UPDATE" in query
    executor.rows = [
        {
            "tenant_id": TENANT,
            "project_id": "proj",
            "day": TODAY,
            "payload": params[3],
            "computed_at": NOW,
        }
    ]
    loaded = await repository.get(TENANT, "proj", TODAY)

    assert loaded == snapshot
