"""Reads and checks that default to today use the tenant's today, not the server's.

Graph links are dated and read on the tenant's day (``ServiceRegistry.tenant_today``).
On a server whose timezone is not the tenant's, its ``date.today()`` is another day
for part of every day. Each test pins the tenant's day three days after the server's,
as for a tenant east of the server just after the tenant's midnight: a link made
through the config API starts on the tenant's day, so a read on the server's day
does not see it yet, and a route that echoes its day names the server's.

The workflows, and the services they run, took today as the UTC date instead; the
pinned day is never that either. The check-in fan-out keeps its UTC date: its cron
is registered in UTC.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.sync_targets import RuntimeSyncTargetResolver
from core.domain.cross_person import CrossPersonRequestStatus
from core.domain.graph import (
    Developer,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    Program,
    Project,
    Task,
)
from core.domain.llm import LlmRequest, LlmResponse
from core.domain.status import CheckIn, IssueClaim
from core.domain.sync_status import SyncSource
from infra.adapters.llm.fake import FakeLlmProvider
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from infra.workflows import rollup as rollup_workflow
from infra.workflows import runtime_sync
from tests.unit.qa2_checkout_slice import OMAR, checkout_slice
from tests.unit.test_attention import DAY as ATTENTION_DAY
from tests.unit.test_attention import _unanswered_day
from tests.unit.test_rollup_refresh import _to_omar
from tests.unit.test_task_update import _DEV, _team

TENANT = "demo"


@pytest.fixture
def tenant_day(monkeypatch: pytest.MonkeyPatch) -> date:
    day = date.today() + timedelta(days=3)
    monkeypatch.setattr(ServiceRegistry, "_tenant_today", lambda _self: day)
    return day


def _app(settings: Settings, **update: object) -> FastAPI:
    return create_app(
        settings=settings.model_copy(update={"demo_mode": True, "llm_provider": "fake", **update})
    )


def _as(role: str, user: str = "dev-eli") -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


def _seed(client: TestClient, app: FastAPI) -> None:
    """Commerce > Checkout > Payments with Eli; Checkout's Wallet workstream holds CHK-1.

    The issue arrives as a tracker sync leaves it; every link is made through the
    config API, so on the tenant's day.
    """
    for path, body in (
        ("/config/programs", {"id": "prog", "name": "Commerce"}),
        ("/config/projects", {"id": "checkout", "name": "Checkout"}),
        ("/config/pods", {"id": "pod-pay", "name": "Payments"}),
        ("/config/workstreams", {"id": "ws", "name": "Wallet"}),
        ("/config/members", {"id": "dev-eli", "name": "Eli"}),
    ):
        assert client.post(path, json=body).status_code == 201, path
    asyncio.run(
        app.state.registry.graph_repository().upsert_node(
            Task(
                tenant_id=TENANT,
                id="CHK-1",
                name="Card payments",
                metadata={"key": "CHK-1", "issue_type": "Story", "status": "In Progress"},
            )
        )
    )
    for path, link in (
        ("/config/projects/checkout/program", {"program_id": "prog"}),
        ("/config/pods/pod-pay/projects/checkout", None),
        ("/config/projects/checkout/workstreams/ws", None),
        ("/config/workstreams/ws/tasks/CHK-1", None),
        ("/config/pods/pod-pay/members/dev-eli", {"role": "engineer"}),
    ):
        assert client.post(path, json=link).status_code == 200, path


@pytest.fixture
def seeded(settings: Settings, tenant_day: date) -> Iterator[tuple[TestClient, FastAPI]]:
    app = _app(settings)
    with TestClient(app) as client:
        _seed(client, app)
        yield client, app


@pytest.mark.parametrize(
    ("path", "role"),
    [
        ("/me/focus", "dev"),
        ("/me/delivery-tree", "dev"),
        ("/pods/pod-pay/blockers", "admin"),
        ("/pods/pod-pay/checkins", "admin"),
        ("/pods/pod-pay/rollup", "admin"),
        ("/pods/pod-pay/tasks", "admin"),
        ("/projects/checkout/progress", "admin"),
        ("/workstreams/ws/progress", "admin"),
        ("/programs/prog/tree", "admin"),
        ("/portfolio/heatmap", "admin"),
        ("/portfolio/attention", "admin"),
        ("/workstreams/ws/flow", "admin"),
        ("/portfolio/flow", "admin"),
        ("/portfolio/pr-flow", "admin"),
        ("/projects/checkout/risks", "admin"),
        ("/portfolio/risks", "admin"),
        ("/projects/checkout/requirements", "admin"),
    ],
)
def test_a_read_with_no_as_of_answers_for_the_tenants_day(
    seeded: tuple[TestClient, FastAPI], tenant_day: date, path: str, role: str
) -> None:
    client, _app = seeded

    response = client.get(path, headers=_as(role))

    assert response.status_code == 200, response.text
    assert response.json()["as_of"] == tenant_day.isoformat()


def test_a_trend_with_no_as_of_ends_on_the_tenants_day(
    seeded: tuple[TestClient, FastAPI], tenant_day: date
) -> None:
    client, _app = seeded

    response = client.get("/persona/pod/pod-pay/trend")

    assert response.status_code == 200, response.text
    assert response.json()["end"] == tenant_day.isoformat()


def test_reads_with_no_as_of_see_the_links_made_on_the_tenants_day(
    seeded: tuple[TestClient, FastAPI],
) -> None:
    client, _app = seeded

    pods = client.get("/pods")
    projects = client.get("/projects")
    programs = client.get("/programs")
    workstreams = client.get("/workstreams")
    workstream = client.get("/workstreams/ws")
    project_workstreams = client.get("/projects/checkout/workstreams")
    tree = client.get("/graph/programs/prog/tree")
    project_delivery = client.get("/projects/checkout/delivery")
    pod_delivery = client.get("/pods/pod-pay/delivery")
    own_tree = client.get("/me/delivery-tree", headers=_as("dev"))
    dev_users = client.get("/api/v1/auth/dev-users")

    (pod,) = pods.json()
    assert (pod["project_ids"], pod["member_ids"]) == (["checkout"], ["dev-eli"])
    assert projects.json()[0]["program_ids"] == ["prog"]
    assert programs.json()[0]["project_ids"] == ["checkout"]
    assert [item["id"] for item in workstreams.json()] == ["ws"]
    assert workstream.json()["project_ids"] == ["checkout"]
    assert [item["id"] for item in project_workstreams.json()] == ["ws"]
    assert {node["id"] for node in tree.json()["nodes"]} >= {"prog", "checkout", "pod-pay"}
    assert [part["scope_id"] for part in project_delivery.json()["pods"]] == ["pod-pay"]
    assert [part["project_id"] for part in pod_delivery.json()["projects"]] == ["checkout"]
    assert [node["id"] for node in own_tree.json()["pods"]] == ["pod-pay"]
    eli = next(item for item in dev_users.json()["items"] if item["id"] == "dev-eli")
    assert eli["pods"] == ["Payments"]


def test_a_scrum_master_acts_on_the_pod_they_joined_on_the_tenants_day(
    seeded: tuple[TestClient, FastAPI], tenant_day: date
) -> None:
    client, _app = seeded

    pod_date = client.put(
        "/projects/checkout/pods/pod-pay/delivery-date",
        json={"target_date": (tenant_day + timedelta(days=30)).isoformat()},
        headers=_as("sm"),
    )
    readiness = client.get("/pods/pod-pay/readiness", headers=_as("sm"))

    assert pod_date.status_code == 200, pod_date.text
    assert readiness.status_code == 200, readiness.text


def test_the_gate_board_and_scan_cover_an_issue_linked_on_the_tenants_day(
    seeded: tuple[TestClient, FastAPI],
) -> None:
    client, _app = seeded

    board = client.get("/projects/checkout/gates")
    scan = client.post("/projects/checkout/gates/scan")

    assert [issue["key"] for issue in board.json()["issues"]] == ["CHK-1"]
    assert scan.status_code == 200, scan.text
    summary = scan.json()
    # The fake tracker has no CHK-1 to read, but the scan tried the one issue in scope.
    assert summary["read"] + summary["unchanged"] + summary["failed"] == 1


def test_a_task_update_and_my_status_are_for_the_tenants_day(
    settings: Settings, tenant_day: date
) -> None:
    app = _app(settings, dev_principal_roles="dev", dev_principal_subject=_DEV)
    with TestClient(app) as client:
        asyncio.run(_team(app.state.registry.graph_repository()))
        updated = client.post(
            "/me/tasks/CHK-4/update",
            json={"state": "in_review", "eta": tenant_day.isoformat()},
        )
        mine = client.get("/me/status")
        confirmed = client.post("/me/status/confirm")
        corrected = client.post("/me/status/correct", json={"summary": "Reviewing CHK-4"})

    assert updated.status_code == 200, updated.text
    assert updated.json()["status"]["status_as_of"] == tenant_day.isoformat()
    for response in (mine, confirmed, corrected):
        assert response.status_code == 200, response.text
        assert response.json()["status_as_of"] == tenant_day.isoformat()


def test_ask_and_investigate_answer_for_the_tenants_day(
    seeded: tuple[TestClient, FastAPI], tenant_day: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _app = seeded
    asked: list[LlmRequest] = []
    complete = FakeLlmProvider.complete

    async def recording(self: FakeLlmProvider, request: LlmRequest) -> LlmResponse:
        asked.append(request)
        return await complete(self, request)

    monkeypatch.setattr(FakeLlmProvider, "complete", recording)

    ask = client.post("/ask", json={"question": "How is Checkout doing?"})
    investigate = client.post("/ask/investigate", json={"question": "How is Checkout doing?"})

    assert (ask.status_code, investigate.status_code) == (200, 200)
    today_line = f"Today is {tenant_day.strftime('%A')} {tenant_day.isoformat()}."
    assert len(asked) == 2
    assert all(today_line in repr(request) for request in asked)


async def _sync_store(store: InMemoryGraphStore, day: date) -> None:
    """A Jira project and a pod with its own filter, linked to it on ``day``."""
    await store.upsert_node(
        GraphNode(
            tenant_id=TENANT,
            id="project-alpha",
            kind=NodeKind.PROJECT,
            name="Alpha",
            metadata={"jira_project_key": "PO"},
        )
    )
    await store.upsert_node(
        GraphNode(
            tenant_id=TENANT,
            id="pod-runtime",
            kind=NodeKind.POD,
            name="Runtime",
            metadata={"jira_filter_jql": "component = API"},
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT,
            from_node_id="project-alpha",
            to_node_id="pod-runtime",
            kind=EdgeKind.CONTAINS,
            valid_from=day,
        )
    )


async def test_sync_targets_are_resolved_on_the_given_today() -> None:
    day = date.today() + timedelta(days=3)
    store = InMemoryGraphStore()
    await _sync_store(store, day)

    targets = await RuntimeSyncTargetResolver(store, today=lambda: day).resolve(TENANT)

    assert {item.payload["target_node_id"] for item in targets.issue_dispatches} == {
        "project-alpha",
        "pod-runtime",
    }


async def test_the_runtime_sync_and_its_status_read_the_tenants_day(
    settings: Settings, tenant_day: date
) -> None:
    registry = ServiceRegistry(settings)
    await _sync_store(registry.graph_repository(), tenant_day)

    payload = runtime_sync.RuntimeSyncInput(tenant_id=TENANT)
    plan = await runtime_sync._resolve_plan(registry, payload)
    report = await registry.sync_status_service().status(TENANT)

    assert "pod-runtime" in {item.payload["target_node_id"] for item in plan.dispatches}
    issues = next(item for item in report.sources if item.source is SyncSource.ISSUE_TRACKER)
    assert "Pod Runtime" in {target.label for target in issues.targets}


# --- Workflows and the services they run ----------------------------------------------


def _offline(settings: Settings) -> Settings:
    """Every provider fake, so the registry builds the workflow-side services offline."""
    return settings.model_copy(
        update={
            "chat_provider": "fake",
            "directory_provider": "fake",
            "issue_tracker_provider": "fake",
            "vcs_provider": "fake",
            "calendar_provider": "fake",
            "llm_provider": "fake",
            "workflow_provider": "fake",
        }
    )


async def test_the_rollup_with_no_as_of_records_the_tenants_day(
    settings: Settings, tenant_day: date, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ServiceRegistry(_offline(settings))
    rollups = registry.rollup_repository()
    program = Program(tenant_id=TENANT, id="prog", name="Commerce")
    await registry.graph_repository().upsert_node(program)
    monkeypatch.setattr(rollup_workflow, "_service_registry", lambda: registry)

    await rollup_workflow.run_rollup_activity(
        rollup_workflow.RollupInput(tenant_id=TENANT, backfill_days=0)
    )

    recorded = await rollups.node_status_history(
        TENANT, program.ref, tenant_day - timedelta(days=7), tenant_day + timedelta(days=7)
    )
    assert [status.as_of for status in recorded] == [tenant_day]


async def test_a_resolved_wait_refreshes_the_rollup_of_the_tenants_day(
    settings: Settings, tenant_day: date
) -> None:
    store = await checkout_slice()
    registry = ServiceRegistry(_offline(settings), graph_store=store)
    service = registry.cross_person_request_service()
    assert service.blocker_settlement is not None
    refresher = service.blocker_settlement._rollup_refresher
    to_omar = await store.create(_to_omar())

    await service.update_status(TENANT, to_omar.id, CrossPersonRequestStatus.RESOLVED, actor=OMAR)

    assert refresher.requests == [(TENANT, tenant_day)]  # type: ignore[union-attr]


async def test_the_status_collector_reads_the_links_in_force_on_the_tenants_day(
    settings: Settings, tenant_day: date
) -> None:
    registry = ServiceRegistry(_offline(settings))
    store = registry.graph_repository()
    for node in (
        Project(tenant_id=TENANT, id="checkout", name="Checkout"),
        Task(tenant_id=TENANT, id="CHK-1", name="Card payments"),
        Task(tenant_id=TENANT, id="CHK-2", name="Refunds"),
        Developer(tenant_id=TENANT, id="dev-eli", name="Eli"),
        Developer(tenant_id=TENANT, id="dev-ira", name="Ira"),
    ):
        await store.upsert_node(node)
    for parent, child, kind in (
        ("checkout", "CHK-1", EdgeKind.CONTAINS),
        ("checkout", "CHK-2", EdgeKind.CONTAINS),
        ("dev-ira", "CHK-2", EdgeKind.ASSIGNED_TO),
    ):
        await store.add_edge(
            GraphEdge(
                tenant_id=TENANT,
                from_node_id=parent,
                to_node_id=child,
                kind=kind,
                valid_from=tenant_day,
            )
        )
    collector = registry.status_collector()
    undated = CheckIn(
        tenant_id=TENANT,
        developer_id="dev-eli",
        correlation_id="checkin-eli",
        asked_at=None,
        replied_at=None,
        raw_reply=None,
        signals=None,
        checkin_date=None,
    )

    keys = await collector._project_task_keys(TENANT)
    others = await collector._issues_of_others(undated, ["CHK-2"], ())

    assert keys["CHK-1"] == frozenset({"CHK-1", "CHK-2"})
    assert others == {"CHK-2"}


def test_the_heat_map_says_today_on_the_tenants_day(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A past tenant's day: the UTC date it fell back to is later, so its words said
    # "that day" about the tenant's today.
    monkeypatch.setattr(ServiceRegistry, "_tenant_today", lambda _self: ATTENTION_DAY)
    store = asyncio.run(_unanswered_day())
    app = create_app(settings=settings, registry=ServiceRegistry(settings, graph_store=store))
    with TestClient(app) as client:
        response = client.get("/portfolio/heatmap", params={"program_root_id": "prog"})

    assert response.status_code == 200, response.text
    reasons = {cell["entity_ref"]["id"]: cell["reason"] for cell in response.json()["cells"]}
    assert reasons["pod-web"] == "3 of 3 unanswered today"


async def test_a_tracker_comment_with_no_check_in_date_names_the_tenants_day(
    settings: Settings, tenant_day: date
) -> None:
    registry = ServiceRegistry(_offline(settings))

    comment = await registry.write_back_service()._write_back_comment(
        TENANT,
        "dev-eli",
        IssueClaim(issue_key="CHK-1", claimed_done=True),
        "Done",
        written=None,
        reported_on=None,
    )

    assert tenant_day.isoformat() in comment


def test_an_admin_check_in_with_no_date_is_for_the_tenants_day(
    settings: Settings, tenant_day: date
) -> None:
    app = _app(settings, workflow_provider="fake")
    with TestClient(app) as client:
        assert client.post("/config/members", json={"id": "dev-eli", "name": "Eli"}).status_code
        checkin = client.post(
            "/admin/workflows/checkin/dispatch",
            json={"tenant_id": TENANT, "developer_id": "dev-eli"},
        )

    assert checkin.status_code == 200, checkin.text
    assert checkin.json()["workflow_id"] == f"fake-checkin-demo-dev-eli-{tenant_day.isoformat()}"
