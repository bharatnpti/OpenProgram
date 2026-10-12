"""What the tracker says beside what people said: the "Jira today" task line, the
pod's coverage line, Ora's evidence and Admin's fill switch."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.ask_service import ASK_TOOL_GUIDANCE, PodCheckinsTool
from core.application.persona_views import PersonaViewService
from core.domain.delivery import DeliveryStage
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    JsonScalar,
    NodeKind,
    Pod,
    Task,
)
from core.domain.identity import IdentityLink
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore

AS_OF = date(2026, 10, 9)
NOON = datetime.combine(AS_OF, time(12, 0), tzinfo=UTC)


def _fact(source: str, key: str, payload: dict[str, JsonScalar], at: datetime) -> FactEvent:
    return FactEvent(
        tenant_id="demo",
        source=source,
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id=key),
        payload={"key": key, **payload},
        observed_at=at,
        correlation_id=f"{source}:{key}:{at.isoformat()}:{payload.get('field', '')}",
    )


async def _pod(store: InMemoryGraphStore) -> None:
    await store.upsert_node(Pod(tenant_id="demo", id="pod-pay", name="Payments Pod"))
    for member_id, name in (("dev-kai", "Kai"), ("dev-liam", "Liam"), ("dev-ira", "Ira")):
        await store.upsert_node(Developer(tenant_id="demo", id=member_id, name=name))
        await store.add_edge(
            GraphEdge(
                tenant_id="demo",
                from_node_id="pod-pay",
                to_node_id=member_id,
                kind=EdgeKind.CONTAINS,
            )
        )
    for key, status, state in (
        ("CHK-4", "In Review", "in_progress"),
        ("CHK-9", "In Progress", "in_progress"),
    ):
        await store.upsert_node(
            Task(
                tenant_id="demo",
                id=key,
                name=f"Made-up {key}",
                metadata={"key": key, "status": status, "state": state},
            )
        )
        await store.add_edge(
            GraphEdge(
                tenant_id="demo", from_node_id="pod-pay", to_node_id=key, kind=EdgeKind.CONTAINS
            )
        )
        await store.add_edge(
            GraphEdge(
                tenant_id="demo", from_node_id="dev-kai", to_node_id=key, kind=EdgeKind.ASSIGNED_TO
            )
        )
    # CHK-4 was moved to review by Liam this morning, and its due date moved.
    await store.append_fact(
        _fact(
            "issue",
            "CHK-4",
            {"state": "in_progress", "status": "In Review"},
            NOON - timedelta(hours=2),
        )
    )
    await store.append_fact(
        _fact(
            "issue_change",
            "CHK-4",
            {
                "field": "status",
                "from": "In Progress",
                "to": "In Review",
                "author_member_id": "dev-liam",
            },
            NOON - timedelta(hours=2),
        )
    )
    await store.append_fact(
        _fact(
            "issue_change",
            "CHK-4",
            {
                "field": "due_date",
                "from": "2026-10-10",
                "to": "2026-10-14",
                "author_member_id": "dev-kai",
            },
            NOON - timedelta(hours=1),
        )
    )
    # CHK-9 has not moved for 8 days.
    await store.append_fact(
        _fact(
            "issue",
            "CHK-9",
            {"state": "in_progress", "status": "In Progress"},
            NOON - timedelta(days=8),
        )
    )
    await store.append_fact(
        _fact(
            "issue_change",
            "CHK-9",
            {
                "field": "status",
                "from": "To Do",
                "to": "In Progress",
                "author_member_id": "dev-kai",
            },
            NOON - timedelta(days=8),
        )
    )


def _service(store: InMemoryGraphStore) -> PersonaViewService:
    return PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
        identity_link_repository=store,
    )


async def test_pod_tasks_carry_what_jira_says_today_beside_what_was_said() -> None:
    store = InMemoryGraphStore()
    await _pod(store)

    view = await _service(store).pod_tasks("demo", "pod-pay", AS_OF)

    tasks = {task.id: task for task in view.tasks}
    chk4 = tasks["CHK-4"].observed
    assert chk4 is not None
    assert (chk4.status, chk4.stage, chk4.since) == (
        "In Review",
        DeliveryStage.IN_DEVELOPMENT,
        NOON - timedelta(hours=2),
    )
    # Liam is not one of CHK-4's own people: the move names him, on the work item.
    assert chk4.moved_by == "Liam"
    assert chk4.changes == ("due date moved Oct 10 → Oct 14",)
    # Never merged into what anyone said.
    assert tasks["CHK-4"].last_update is None
    chk9 = tasks["CHK-9"].observed
    assert chk9 is not None and chk9.quiet_days == 8 and chk9.moved_by is None


async def test_the_developers_own_tasks_carry_the_line_and_their_own_move_names_nobody() -> None:
    store = InMemoryGraphStore()
    await _pod(store)

    focus = await _service(store).focus("demo", "dev-kai", AS_OF)

    lines = {task.id: task.observed for task in focus.tasks}
    assert lines["CHK-4"] is not None and lines["CHK-4"].moved_by == "Liam"
    assert lines["CHK-9"] is not None and lines["CHK-9"].moved_by is None


async def _statuses(store: InMemoryGraphStore) -> None:
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-kai",
            as_of=AS_OF,
            source=StatusSource.INFERRED,
            blockers=("no confirmed reply",),
            summary=(
                "No confirmed check-in after a nudge. Inferred from Jira and GitLab: "
                "CHK-4 moved to In Review (in development); no movement on CHK-9 for 8 days."
            ),
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-liam",
            as_of=AS_OF,
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Reviewed CHK-4.",
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-ira",
            as_of=AS_OF,
            source=StatusSource.UNKNOWN,
            blockers=("no confirmed reply",),
            summary="Not asked today. Current status is unknown.",
        )
    )
    await store.upsert_identity_link(
        IdentityLink(tenant_id="demo", developer_id="dev-ira", chat_user_id="U-ira")
    )


async def test_pod_checkins_count_how_the_day_was_captured_and_why_some_has_no_data() -> None:
    store = InMemoryGraphStore()
    await _pod(store)
    await _statuses(store)

    view = await _service(store).pod_checkins("demo", "pod-pay", AS_OF)

    coverage = view.coverage
    assert (coverage.replied, coverage.inferred, coverage.no_data, coverage.waiting) == (1, 1, 1, 0)
    assert coverage.causes == (("no_jira_link", 1),)
    people = {item.developer_id: item for item in view.developers}
    assert people["dev-kai"].evidence == (
        "CHK-4 moved to In Review (in development)",
        "no movement on CHK-9 for 8 days",
    )
    assert people["dev-ira"].no_data_cause == "no_jira_link"
    assert people["dev-liam"].evidence == () and people["dev-liam"].no_data_cause is None


async def test_ora_reads_an_inferred_days_evidence_and_is_told_it_is_not_their_word() -> None:
    store = InMemoryGraphStore()
    await _pod(store)
    await _statuses(store)
    tool = PodCheckinsTool(tenant_id="demo", service=_service(store), repository=store, as_of=AS_OF)

    payload = json.loads(await tool.run({"pod_id": "pod-pay"}))

    pod = payload["pods"][0]
    assert pod["coverage"] == {
        "replied": 1,
        "inferred": 1,
        "no_data": 1,
        "waiting": 0,
        "no_data_causes": {"no_jira_link": 1},
    }
    kai = next(item for item in pod["developers"] if item["developer_id"] == "dev-kai")
    assert kai["evidence"][0] == "CHK-4 moved to In Review (in development)"
    assert "not the person's word" in ASK_TOOL_GUIDANCE


# --- Admin › Check-ins: the fill switch -----------------------------------------


def _as(role: str) -> dict[str, str]:
    return {"x-openprogram-dev-user": "someone", "x-openprogram-dev-roles": role}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(
        settings=settings.model_copy(
            update={"demo_mode": True, "issue_tracker_provider": "fake", "tenant_id": "demo"}
        )
    )
    with TestClient(app) as test_client:
        graph = app.state.registry.graph_repository()
        asyncio.run(graph.upsert_node(Developer(tenant_id="demo", id="U1001", name="Asha Rao")))
        yield test_client


def test_the_fill_switch_is_on_by_default_and_an_admin_turns_it_off(client: TestClient) -> None:
    first = client.get("/config/checkin-fill", headers=_as("admin"))
    assert first.status_code == 200, first.text
    assert first.json()["enabled"] is True and first.json()["source"] == "default"

    saved = client.put("/config/checkin-fill", json={"enabled": False}, headers=_as("admin"))
    assert saved.status_code == 200, saved.text

    again = client.get("/config/checkin-fill", headers=_as("admin")).json()
    assert (again["enabled"], again["source"], again["updated_by"]) == (False, "tenant", "someone")
    assert again["deployment_enabled"] is True


@pytest.mark.parametrize("role", ["dev", "sm", "exec"])
def test_only_an_admin_reads_or_sets_the_fill_switch(client: TestClient, role: str) -> None:
    assert client.get("/config/checkin-fill", headers=_as(role)).status_code == 403
    assert (
        client.put("/config/checkin-fill", json={"enabled": False}, headers=_as(role)).status_code
        == 403
    )


def test_executives_still_read_no_pod_checkins(client: TestClient) -> None:
    response = client.get("/pods/pod-anything/checkins", headers=_as("exec"))

    assert response.status_code == 403
