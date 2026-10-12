"""Planning complete: what a baseline freezes, how a new one replaces the old, who sets it."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.baseline_service import BaselineService
from core.application.delivery_service import DeliveryService
from core.domain.baseline import (
    DEFAULT_CHANGE_LABEL,
    BaselineError,
    BaselineItem,
    BaselineKind,
    BaselineScopeKind,
    PlanningBaseline,
    since_baseline,
    validated_change_label,
    validated_grace,
    validated_planned_on,
)
from core.domain.delivery import DeliveryStage
from core.domain.errors import GraphNotFound
from core.domain.forecast import ReleaseMatch, ReleaseMatchKind
from core.domain.graph import Developer, EntityRef, FactEvent, NodeKind, Task
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_baselines import _baseline, payload_to_json
from infra.registry import ServiceRegistry
from tests.unit.test_forecast import NOW, TENANT, TODAY, _as, _contains, _registry, _seed, _snapshot

S = DeliveryStage


def _service(registry: ServiceRegistry, *, now: datetime = NOW) -> BaselineService:
    ids = itertools.count(1)
    clock = iter(now + timedelta(minutes=minute) for minute in itertools.count())
    delivery = DeliveryService(
        graph_repository=registry.graph_repository(),
        settings_repository=registry.delivery_settings_repository(),
        snapshot_repository=registry.requirements_snapshot_repository(),
        release_repository=registry.release_repository(),
        clock=lambda: now,
        today=lambda: TODAY,
    )
    return BaselineService(
        graph_repository=registry.graph_repository(),
        delivery_service=delivery,
        release_repository=registry.release_repository(),
        baseline_repository=registry.planning_baseline_repository(),
        time_series_repository=registry.time_series_repository(),
        clock=lambda: next(clock),
        today=lambda: TODAY,
        new_id=lambda: f"bl-{next(ids)}",
    )


async def _sized(store: InMemoryGraphStore) -> None:
    """Story points, a type and a creation day on the requirements; CHK-4 carries the label CR."""
    for node in await store.list_nodes(TENANT, NodeKind.TASK):
        key = str(node.metadata["key"])
        extra: dict[str, object] = {
            "story_points": {"CHK-1": 3, "CHK-2": 5, "CHK-3": 2, "CHK-4": 8}[key],
            "issue_type": "Story",
            "created_at": f"2026-09-{ {'CHK-1': 1, 'CHK-2': 1, 'CHK-3': 20, 'CHK-4': 28}[key]:02d}"
            "T09:00:00+00:00",
        }
        if key == "CHK-4":
            extra["labels"] = "CR, ux"
        await store.upsert_node(
            Task(
                tenant_id=TENANT,
                id=node.id,
                name=node.name,
                metadata={**node.metadata, **extra},  # type: ignore[arg-type]
            )
        )


# --- The rules ---------------------------------------------------------------------------


def test_the_change_label_is_one_jira_label_and_defaults_to_the_generic_one() -> None:
    assert validated_change_label(None) == DEFAULT_CHANGE_LABEL == "CR"
    assert validated_change_label("  change-request ") == "change-request"
    with pytest.raises(BaselineError, match="no spaces"):
        validated_change_label("change request")
    with pytest.raises(BaselineError):
        validated_change_label("x" * 65)


def test_planning_is_complete_today_or_on_an_earlier_day_within_a_year() -> None:
    assert validated_planned_on(None, TODAY) == TODAY
    assert validated_planned_on(date(2026, 10, 1), TODAY) == date(2026, 10, 1)
    with pytest.raises(BaselineError, match="today or on an earlier day"):
        validated_planned_on(TODAY + timedelta(days=1), TODAY)
    with pytest.raises(BaselineError, match="a year"):
        validated_planned_on(TODAY - timedelta(days=366), TODAY)
    assert validated_grace(0) == 0
    with pytest.raises(BaselineError):
        validated_grace(21)


def test_since_a_baseline_lists_keys_added_with_and_without_the_label_and_keys_gone() -> None:
    baseline = PlanningBaseline(
        tenant_id=TENANT,
        baseline_id="b",
        scope_kind=BaselineScopeKind.PROJECT,
        scope_id="checkout",
        project_id="checkout",
        kind=BaselineKind.SNAPSHOT,
        planned_on=TODAY,
        items={"CHK-1": BaselineItem(stage=S.RAISED), "CHK-2": BaselineItem(stage=S.RAISED)},
        reconstructed=False,
        snapshot_day=None,
        change_label="cr",
        grace_working_days=2,
        set_by="po",
        set_at=NOW,
    )

    since = since_baseline(
        baseline, ["CHK-2", "CHK-10", "CHK-9"], {"CHK-10": ("CR",), "CHK-9": ("other",)}
    )

    assert since.added == ("CHK-9", "CHK-10")
    assert since.added_with_label == ("CHK-10",)
    assert since.removed == ("CHK-1",)


def test_a_baseline_round_trips_through_its_stored_row() -> None:
    baseline = PlanningBaseline(
        tenant_id=TENANT,
        baseline_id="b",
        scope_kind=BaselineScopeKind.RELEASE,
        scope_id="rel-1",
        project_id="checkout",
        kind=BaselineKind.DATE,
        planned_on=date(2026, 10, 1),
        items={
            "CHK-1": BaselineItem(stage=S.GROOMED, points=3.0, due=date(2026, 11, 2), type="Story"),
            "CHK-2": BaselineItem(stage=None),
        },
        reconstructed=True,
        snapshot_day=date(2026, 9, 30),
        change_label="CR",
        grace_working_days=2,
        set_by="po",
        set_at=NOW,
        note="Agreed at the review",
    )
    row = {
        "id": "b",
        "tenant_id": TENANT,
        "scope_kind": "release",
        "scope_id": "rel-1",
        "project_id": "checkout",
        "kind": "date",
        "planned_on": date(2026, 10, 1),
        "payload": json.dumps(payload_to_json(baseline)),
        "change_label": "CR",
        "grace_working_days": 2,
        "set_by": "po",
        "set_at": NOW,
        "note": "Agreed at the review",
        "superseded_at": None,
    }

    assert _baseline(row) == baseline


# --- Marking planning complete -----------------------------------------------------------


async def test_planning_complete_today_freezes_the_requirements_with_their_size_and_dates() -> None:
    registry, store = await _registry()
    await _sized(store)
    service = _service(registry)

    view = await service.mark_planning_complete(TENANT, "checkout", actor="dev-asha")

    baseline = view.baseline
    assert (baseline.kind, baseline.planned_on, baseline.reconstructed) == (
        BaselineKind.SNAPSHOT,
        TODAY,
        False,
    )
    assert baseline.items == {
        "CHK-1": BaselineItem(stage=S.PRODUCTION, points=3.0, type="Story"),
        "CHK-2": BaselineItem(
            stage=S.IN_DEVELOPMENT, points=5.0, due=date(2026, 10, 30), type="Story"
        ),
        "CHK-3": BaselineItem(stage=S.IN_TESTING, points=2.0, type="Story"),
        "CHK-4": BaselineItem(stage=S.RAISED, points=8.0, due=date(2026, 11, 20), type="Story"),
    }
    assert baseline.points == 18.0 and baseline.open_keys == {"CHK-2", "CHK-3", "CHK-4"}
    assert (baseline.change_label, baseline.grace_working_days) == ("CR", 2)
    assert (view.number, view.active, view.set_by_name) == (1, True, "Asha")
    assert view.since.added == () and view.since.removed == ()


async def test_a_new_baseline_replaces_the_active_one_and_keeps_it() -> None:
    registry, store = await _registry()
    service = _service(registry)
    await service.mark_planning_complete(TENANT, "checkout", actor="po")
    await store.upsert_node(
        Task(
            tenant_id=TENANT,
            id="CHK-5",
            name="Added after planning",
            metadata={"key": "CHK-5", "status": "To Do", "state": "todo", "labels": "CR"},
        )
    )
    await store.add_edge(_contains("pod-pay", "CHK-5"))

    second = await service.mark_planning_complete(
        TENANT, "checkout", change_label="change", note="Two changes approved", actor="po"
    )
    view = await service.baselines(TENANT, "checkout", as_of=TODAY)

    assert [item.number for item in view.baselines] == [2, 1]
    assert [item.active for item in view.baselines] == [True, False]
    assert view.active is not None and view.active.baseline == second.baseline
    first = view.first
    assert first is not None
    assert first.baseline.superseded_at == second.baseline.set_at
    # Against the first baseline CHK-5 is added scope, with the first's label "CR".
    assert (first.since.added, first.since.added_with_label) == (("CHK-5",), ("CHK-5",))
    assert second.since.added == () and second.baseline.note == "Two changes approved"
    assert await service.active_baseline(TENANT, BaselineScopeKind.PROJECT, "checkout") == (
        second.baseline
    )


async def test_planning_complete_on_an_earlier_day_freezes_that_days_snapshot() -> None:
    registry, store = await _registry()
    snapshots = registry.requirements_snapshot_repository()
    friday = date(2026, 10, 2)
    await snapshots.save(_snapshot(friday, {"CHK-1": S.IN_TESTING, "CHK-2": S.GROOMED}))
    await registry.time_series_repository().append_fact(
        FactEvent(
            tenant_id=TENANT,
            source="issue",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-2"),
            payload={"key": "CHK-2", "story_points": 5, "issue_type": "Story"},
            observed_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
            correlation_id="issue-chk-2-1",
        )
    )
    await registry.time_series_repository().append_fact(
        FactEvent(
            tenant_id=TENANT,
            source="issue",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-2"),
            payload={"key": "CHK-2", "story_points": 8, "issue_type": "Story"},
            observed_at=datetime(2026, 10, 3, 9, tzinfo=UTC),
            correlation_id="issue-chk-2-2",
        )
    )
    service = _service(registry)

    # Sunday the 4th kept no snapshot: Friday's is the one before it.
    view = await service.mark_planning_complete(
        TENANT, "checkout", planned_on=date(2026, 10, 4), actor="po"
    )

    baseline = view.baseline
    assert (baseline.kind, baseline.planned_on, baseline.snapshot_day) == (
        BaselineKind.DATE,
        date(2026, 10, 4),
        friday,
    )
    assert not baseline.reconstructed
    # Its stage is the snapshot's; its points the ones synced by the end of the day.
    assert baseline.items == {
        "CHK-1": BaselineItem(stage=S.IN_TESTING),
        "CHK-2": BaselineItem(stage=S.GROOMED, points=8.0, type="Story"),
    }
    # Today CHK-3 and CHK-4 are in scope too: added since planning.
    assert view.since.added == ("CHK-3", "CHK-4")


async def test_before_snapshots_were_kept_the_issues_created_by_then_stand_in() -> None:
    registry, store = await _registry()
    await _sized(store)
    service = _service(registry)

    view = await service.mark_planning_complete(
        TENANT, "checkout", planned_on=date(2026, 9, 25), actor="po"
    )

    assert view.baseline.reconstructed and view.baseline.snapshot_day is None
    # CHK-4 was created on the 28th; no issue fact says where the others stood then.
    assert view.baseline.items == {
        "CHK-1": BaselineItem(stage=None),
        "CHK-2": BaselineItem(stage=None),
        "CHK-3": BaselineItem(stage=None),
    }
    with pytest.raises(BaselineError, match="nothing to freeze"):
        await service.mark_planning_complete(
            TENANT, "checkout", planned_on=date(2026, 8, 1), actor="po"
        )


async def test_a_release_baseline_freezes_only_its_requirements() -> None:
    registry, store = await _registry()
    release = await registry.forecast_service().save_release(
        TENANT,
        "checkout",
        release_id=None,
        name="Release 1",
        match=ReleaseMatch(kind=ReleaseMatchKind.FIX_VERSION, value="R1"),
        actor="po",
    )
    snapshots = registry.requirements_snapshot_repository()
    friday = date(2026, 10, 2)
    await snapshots.save(
        _snapshot(friday, {"CHK-1": S.IN_TESTING, "CHK-2": S.GROOMED, "CHK-4": S.RAISED})
    )
    service = _service(registry)

    now = await service.mark_planning_complete(
        TENANT, "checkout", release_id=release.release_id, actor="po"
    )
    # The release was defined after Friday: the project's snapshot, kept to its requirements.
    earlier = await service.mark_planning_complete(
        TENANT, "checkout", release_id=release.release_id, planned_on=friday, actor="po"
    )

    assert now.baseline.scope_kind is BaselineScopeKind.RELEASE
    assert now.baseline.scope_id == release.release_id
    assert set(now.baseline.items) == {"CHK-1", "CHK-2", "CHK-3"}
    assert earlier.baseline.reconstructed
    assert set(earlier.baseline.items) == {"CHK-1", "CHK-2"}
    assert earlier.since.added == ("CHK-3",)
    project = await service.baselines(TENANT, "checkout", as_of=TODAY)
    assert project.baselines == ()
    with pytest.raises(GraphNotFound):
        await service.mark_planning_complete(TENANT, "checkout", release_id="nope", actor="po")


async def test_a_past_day_lists_the_baselines_set_by_then() -> None:
    registry, _store = await _registry()
    early = _service(registry, now=NOW - timedelta(days=3))
    await early.mark_planning_complete(TENANT, "checkout", actor="po")
    await _service(registry).mark_planning_complete(TENANT, "checkout", actor="po")

    friday = await early.baselines(TENANT, "checkout", as_of=TODAY - timedelta(days=3))
    today = await early.baselines(TENANT, "checkout", as_of=TODAY)

    assert [(item.number, item.active) for item in friday.baselines] == [(1, True)]
    assert [(item.number, item.active) for item in today.baselines] == [(2, True), (1, False)]


async def test_an_empty_scope_has_nothing_to_freeze() -> None:
    registry, store = await _registry()
    for node in await store.list_nodes(TENANT, NodeKind.TASK):
        await store.delete_node(TENANT, node.id, on=TODAY - timedelta(days=1))
    service = _service(registry)

    with pytest.raises(BaselineError, match="nothing to freeze"):
        await service.mark_planning_complete(TENANT, "checkout", actor="po")


async def test_who_set_a_baseline_is_named_by_member_or_chat_id_else_a_team_member() -> None:
    registry, store = await _registry()
    await store.upsert_node(
        Developer(
            tenant_id=TENANT,
            id="dev-mina",
            name="Mina",
            metadata={"chat_external_id": "U0TEST0MINA1"},
        )
    )
    service = _service(registry)

    by_chat_id = await service.mark_planning_complete(TENANT, "checkout", actor="U0TEST0MINA1")
    by_member_id = await service.mark_planning_complete(TENANT, "checkout", actor="dev-mina")
    # A principal no member record names (the dev sign-in's default subject): never its id.
    nobody = await service.mark_planning_complete(TENANT, "checkout", actor="dev-user")

    assert [by_chat_id.set_by_name, by_member_id.set_by_name, nobody.set_by_name] == [
        "Mina",
        "Mina",
        "a team member",
    ]


# --- The API -----------------------------------------------------------------------------


def _app(settings: Settings) -> TestClient:
    return TestClient(create_app(settings=settings.model_copy(update={"demo_mode": True})))


def _one_requirement(client: TestClient) -> None:
    """CHK-1 under the Payments pod, as the issue sync would store it."""
    graph = client.app.state.registry.graph_repository()  # type: ignore[attr-defined]

    async def add() -> None:
        await graph.upsert_node(
            Task(
                tenant_id=TENANT,
                id="CHK-1",
                name="Checkout API",
                metadata={"key": "CHK-1", "status": "To Do", "state": "todo"},
            )
        )
        await graph.add_edge(_contains("pod-pay", "CHK-1"))

    client.portal.call(add)  # type: ignore[union-attr]


def test_api_a_product_owner_or_manager_marks_planning_complete(settings: Settings) -> None:
    with _app(settings) as client:
        _seed(client)
        _one_requirement(client)
        marked = {
            role: client.post(
                "/projects/checkout/baselines", json={"note": f"by {role}"}, headers=_as(role)
            )
            for role in ("po", "mgr", "admin", "sm", "dev", "exec")
        }
        read = client.get("/projects/checkout/baselines", headers=_as("exec"))
        dev_read = client.get("/projects/checkout/baselines", headers=_as("dev"))
        spaced = client.post(
            "/projects/checkout/baselines", json={"change_label": "a b"}, headers=_as("po")
        )
        later = client.post(
            "/projects/checkout/baselines", json={"planned_on": "2999-01-01"}, headers=_as("po")
        )
        long_grace = client.post(
            "/projects/checkout/baselines", json={"grace_working_days": 21}, headers=_as("po")
        )
        no_release = client.post(
            "/projects/checkout/baselines", json={"release_id": "nope"}, headers=_as("po")
        )
        no_project = client.get("/projects/nope/baselines", headers=_as("po"))

    assert [marked[role].status_code for role in ("po", "mgr", "admin")] == [201, 201, 201]
    assert [marked[role].status_code for role in ("sm", "dev", "exec")] == [403, 403, 403]
    body = read.json()
    assert read.status_code == 200, read.text
    assert (body["scope_kind"], body["release_id"], body["in_scope"]) == ("project", None, 1)
    assert [item["number"] for item in body["baselines"]] == [3, 2, 1]
    assert body["active"]["note"] == "by admin" and body["active"]["requirements"] == 1
    assert body["default_change_label"] == "CR" and body["default_grace_working_days"] == 2
    assert body["baselines"][2]["superseded_at"] is not None
    assert dev_read.status_code == 403
    assert spaced.status_code == 422 and "no spaces" in spaced.json()["detail"]
    assert later.status_code == 422
    assert long_grace.status_code == 422
    assert no_release.status_code == 404 and no_project.status_code == 404


def test_api_the_person_acting_is_named_on_the_baseline(settings: Settings) -> None:
    with _app(settings) as client:
        _seed(client)
        _one_requirement(client)
        assert client.post(
            "/config/members", json={"id": "U1003", "name": "Mina Patel"}
        ).status_code in {200, 201}
        marked = client.post("/projects/checkout/baselines", json={}, headers=_as("po", "U1003"))
        read = client.get("/projects/checkout/baselines", headers=_as("exec", "U1011"))

    assert marked.status_code == 201, marked.text
    assert (marked.json()["set_by"], marked.json()["set_by_name"]) == ("U1003", "Mina Patel")
    assert read.json()["active"]["set_by_name"] == "Mina Patel"


def test_api_nothing_to_freeze_is_said_in_words(settings: Settings) -> None:
    with _app(settings) as client:
        _seed(client)
        empty = client.post("/projects/checkout/baselines", json={}, headers=_as("po"))

    assert empty.status_code == 422
    assert empty.json()["detail"] == (
        "No requirements are in scope yet, so there is nothing to freeze."
    )
