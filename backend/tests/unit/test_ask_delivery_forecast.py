"""Ask's delivery_forecast: a project's or pod's date and forecast, read as Delivery reads it.

Commerce holds Checkout Revamp (the Payments Pod, run by Dana with Asha in it,
and the Storefront Pod) and Loyalty (the Loyalty Pod). Checkout's requirements
are CHK-1 (done), CHK-2 and CHK-3 (Payments) and CHK-4 (Storefront).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from config.settings import Settings
from core.application.ask_service import ASK_SYSTEM_PROMPT, DeliveryForecastTool
from core.application.delivery_scope import PROJECT_OUTSIDE_SCOPE, DeliveryScopeService
from core.application.delivery_service import DeliveryService
from core.application.forecast_service import ForecastService
from core.domain.auth import Principal, Role
from core.domain.delivery import DeliveryStage, RequirementsSnapshot
from core.domain.errors import GraphNotFound
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    ReleaseMatch,
    ReleaseMatchKind,
)
from core.domain.graph import Developer, EdgeKind, GraphEdge, Pod, Program, Project, Task
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry

TENANT = "demo"
TODAY = date(2026, 10, 5)  # Monday
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
S = DeliveryStage


async def _forecasts() -> tuple[ForecastService, InMemoryGraphStore, ServiceRegistry]:
    store = InMemoryGraphStore()
    for node in (
        Program(tenant_id=TENANT, id="program-commerce", name="Commerce"),
        Project(tenant_id=TENANT, id="checkout", name="Checkout Revamp"),
        Project(tenant_id=TENANT, id="loyalty", name="Loyalty"),
        Pod(
            tenant_id=TENANT,
            id="pod-pay",
            name="Payments Pod",
            metadata={"escalation_sm_member_id": "dev-dana"},
        ),
        Pod(tenant_id=TENANT, id="pod-store", name="Storefront Pod"),
        Pod(tenant_id=TENANT, id="pod-loyal", name="Loyalty Pod"),
        Developer(tenant_id=TENANT, id="dev-asha", name="Asha"),
        Developer(tenant_id=TENANT, id="dev-dana", name="Dana"),
    ):
        await store.upsert_node(node)
    for parent, child in (
        ("program-commerce", "checkout"),
        ("program-commerce", "loyalty"),
        ("checkout", "pod-pay"),
        ("checkout", "pod-store"),
        ("loyalty", "pod-loyal"),
        ("pod-pay", "dev-asha"),
    ):
        await store.add_edge(_contains(parent, child))
    for key, status, state, pod, due in (
        ("CHK-1", "Done", "done", "pod-pay", None),
        ("CHK-2", "In Progress", "in_progress", "pod-pay", "2026-10-30"),
        ("CHK-3", "In QA", "in_progress", "pod-pay", None),
        ("CHK-4", "To Do", "todo", "pod-store", "2026-11-20"),
    ):
        metadata: dict[str, object] = {
            "key": key,
            "status": status,
            "state": state,
            "fix_versions": "R1",
        }
        if due:
            metadata["due_date"] = due
        await store.upsert_node(
            Task(tenant_id=TENANT, id=key, name=f"{key} work", metadata=metadata)  # type: ignore[arg-type]
        )
        await store.add_edge(_contains(pod, key))
    registry = ServiceRegistry(
        Settings(
            _env_file=None,
            secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
            runtime_mode="memory",
        ),
        graph_store=store,
    )
    delivery = DeliveryService(
        graph_repository=store,
        settings_repository=registry.delivery_settings_repository(),
        snapshot_repository=registry.requirements_snapshot_repository(),
        release_repository=registry.release_repository(),
        clock=lambda: NOW,
        today=lambda: TODAY,
    )
    service = ForecastService(
        graph_repository=store,
        delivery_service=delivery,
        commitment_repository=registry.commitment_repository(),
        release_repository=registry.release_repository(),
        time_series_repository=store,
        clock=lambda: NOW,
        today=lambda: TODAY,
        new_id=lambda: "rel-1",
    )
    return service, store, registry


def _contains(parent: str, child: str) -> GraphEdge:
    return GraphEdge(
        tenant_id=TENANT, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
    )


async def _keep_history(registry: ServiceRegistry) -> None:
    """Four weeks of working-day snapshots: one requirement reaches production every third day."""
    snapshots = registry.requirements_snapshot_repository()
    day = TODAY - timedelta(days=28)
    done: dict[str, DeliveryStage] = {}
    index = 0
    while day < TODAY:
        if day.weekday() < 5:
            index += 1
            if index % 3 == 0:
                done[f"OLD-{index}"] = S.PRODUCTION
            items = {**done, "CHK-2": S.IN_DEVELOPMENT, "CHK-3": S.IN_TESTING}
            counts = {stage: 0 for stage in S}
            for stage in items.values():
                counts[stage] += 1
            await snapshots.save(
                RequirementsSnapshot(
                    tenant_id=TENANT,
                    project_id="checkout",
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
            )
        day += timedelta(days=1)


async def _commit(
    service: ForecastService, kind: CommitmentScopeKind, scope_id: str, day: date
) -> None:
    await service.set_date(
        TENANT,
        CommitmentScope(kind=kind, id=scope_id, project_id="checkout"),
        day,
        note="vendor delay, see thread",
        actor="dev-dana",
    )


def _tool(
    service: ForecastService,
    store: InMemoryGraphStore,
    role: Role,
    subject: str = "someone",
    as_of: date = TODAY,
) -> DeliveryForecastTool:
    return DeliveryForecastTool(
        principal=Principal(tenant_id=TENANT, subject=subject, roles=frozenset({role})),
        service=service,
        scope=DeliveryScopeService(store, store, today=lambda: TODAY),
        repository=store,
        as_of=as_of,
    )


async def _run(tool: DeliveryForecastTool, **arguments: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(await tool.run(arguments))
    return result


async def test_a_project_with_enough_history_gives_its_date_forecast_and_verdict() -> None:
    service, store, registry = await _forecasts()
    await _keep_history(registry)
    await _commit(service, CommitmentScopeKind.PROJECT, "checkout", date(2026, 11, 14))
    await _commit(service, CommitmentScopeKind.PROJECT, "checkout", date(2027, 3, 1))
    await service.save_release(
        TENANT,
        "checkout",
        release_id=None,
        name="Release 1",
        match=ReleaseMatch(kind=ReleaseMatchKind.FIX_VERSION, value="R1"),
        actor="po",
    )
    view = (await service.project_delivery(TENANT, "checkout", TODAY)).project
    assert view.history.p50 is not None and view.history.p85 is not None

    answer = await _run(_tool(service, store, Role.MGR), node_id="checkout")

    assert answer["as_of"] == "2026-10-05"
    (project,) = answer["projects"]
    assert project["project_id"] == "checkout"
    assert project["project_name"] == "Checkout Revamp"
    assert project["committed_date"] == "2027-03-01"
    assert project["first_committed_date"] == "2026-11-14"
    assert project["times_moved"] == 1
    assert project["target_source"] == "committed date"
    assert project["verdict"] == "on track"
    assert project["verdict_rests_on"] == "history forecast"
    assert project["forecast_p50"] == view.history.p50.isoformat()
    assert project["forecast_p85"] == view.history.p85.isoformat()
    assert project["enough_history"] is True
    assert project["history_working_days"] >= project["history_working_days_needed"] == 10
    assert project["no_forecast_reason"] is None
    assert project["open_requirements"] == 3 and project["requirements"] == 4
    assert project["team_latest_issue"] == "CHK-4"
    assert project["reasons"] == list(view.reasons)
    # Every part comes with its name; a release, which is no node, by name alone.
    assert [(pod["pod_id"], pod["pod_name"]) for pod in project["pods"]] == [
        ("pod-pay", "Payments Pod"),
        ("pod-store", "Storefront Pod"),
    ]
    assert [release["release_name"] for release in project["releases"]] == ["Release 1"]
    assert "release_id" not in project["releases"][0]
    # A date's note is a person's own words: it never reaches the model.
    assert "vendor delay" not in json.dumps(answer)


async def test_too_little_history_is_said_and_the_team_dates_decide() -> None:
    service, store, _registry = await _forecasts()
    await _commit(service, CommitmentScopeKind.PROJECT, "checkout", date(2026, 11, 14))

    answer = await _run(_tool(service, store, Role.PO), node_id="checkout")

    (project,) = answer["projects"]
    assert project["enough_history"] is False
    assert project["history_working_days"] == 0
    assert project["forecast_p50"] is None and project["forecast_p85"] is None
    assert project["no_forecast_reason"] == (
        "No history yet: a forecast needs 10 working days of daily snapshots."
    )
    # CHK-4 is due 20 Nov, after the committed 14 Nov.
    assert project["verdict"] == "off track"
    assert project["verdict_rests_on"] == "team ETAs and due dates"
    assert project["team_latest_date"] == "2026-11-20"


async def test_no_committed_date_says_so_and_judges_nothing() -> None:
    service, store, _registry = await _forecasts()

    (project,) = (await _run(_tool(service, store, Role.EXEC), node_id="checkout"))["projects"]

    assert project["committed_date"] is None and project["target_date"] is None
    assert project["verdict"] == "no delivery date set"
    assert project["verdict_rests_on"] is None
    assert "No delivery date is committed yet." in project["reasons"]


async def test_a_part_with_no_requirements_counted_has_nothing_to_forecast() -> None:
    service, store, _registry = await _forecasts()

    (project,) = (await _run(_tool(service, store, Role.MGR), node_id="loyalty"))["projects"]

    # Nothing is open, so the forecast reads done; the console says nothing to forecast.
    assert project["requirements"] == 0
    assert project["verdict"] == "nothing to forecast: no requirements counted"
    assert project["verdict_rests_on"] is None
    assert project["forecast_p50"] is None
    assert project["reasons"] == ["No requirements are counted for it yet."]


async def test_an_earlier_day_is_read_as_it_stood_and_a_later_one_falls_back() -> None:
    service, store, registry = await _forecasts()
    await _keep_history(registry)
    tool = _tool(service, store, Role.MGR)

    today = (await _run(tool, node_id="checkout"))["projects"][0]
    earlier = await _run(tool, node_id="checkout", as_of="2026-09-21")
    later = await _run(tool, node_id="checkout", as_of="2026-12-01")

    assert earlier["as_of"] == "2026-09-21"
    assert earlier["projects"][0]["history_working_days"] < today["history_working_days"]
    assert later["as_of"] == "2026-10-05"


async def test_a_pod_gives_its_part_of_each_project_it_works_on() -> None:
    service, store, _registry = await _forecasts()
    await _commit(service, CommitmentScopeKind.PROJECT, "checkout", date(2026, 11, 14))
    await _commit(service, CommitmentScopeKind.POD, "pod-pay", date(2026, 11, 6))

    answer = await _run(_tool(service, store, Role.DEV, subject="dev-asha"), node_id="pod-pay")

    assert answer["pod_id"] == "pod-pay" and answer["pod_name"] == "Payments Pod"
    (part,) = answer["projects"]
    assert part["project_id"] == "checkout"
    assert part["project_name"] == "Checkout Revamp"
    assert part["project_target_date"] == "2026-11-14"
    assert part["committed_date"] == "2026-11-06"
    assert part["open_requirements"] == 2
    assert "pods" not in part


async def test_a_program_lists_its_projects_and_says_which_the_asker_cannot_read() -> None:
    service, store, _registry = await _forecasts()
    await _commit(service, CommitmentScopeKind.PROJECT, "checkout", date(2026, 11, 14))

    manager = await _run(_tool(service, store, Role.MGR), node_id="program-commerce")
    dana = await _run(
        _tool(service, store, Role.SM, subject="dev-dana"), node_id="program-commerce"
    )

    assert manager["program_id"] == "program-commerce"
    assert manager["program_name"] == "Commerce"
    assert [item["project_name"] for item in manager["projects"]] == [
        "Checkout Revamp",
        "Loyalty",
    ]
    assert "not_shown" not in manager
    # Dana runs the Payments Pod, so only Checkout Revamp is hers to read.
    assert [item["project_name"] for item in dana["projects"]] == ["Checkout Revamp"]
    assert dana["not_shown"] == "1 project outside what the asker reads is left out."


async def test_with_no_node_every_project_the_asker_reads_is_listed_by_name() -> None:
    service, store, _registry = await _forecasts()

    every = await _run(_tool(service, store, Role.PO))
    dana = await _run(_tool(service, store, Role.SM, subject="dev-dana"))
    asha = await _run(_tool(service, store, Role.DEV, subject="dev-asha"))

    assert [item["project_id"] for item in every["projects"]] == ["checkout", "loyalty"]
    assert [item["project_id"] for item in dana["projects"]] == ["checkout"]
    assert asha["projects"] == []
    assert asha["not_shown"] == "2 projects outside what the asker reads are left out."


async def test_a_read_the_route_refuses_comes_back_in_the_routes_words() -> None:
    service, store, _registry = await _forecasts()

    answer = await _run(_tool(service, store, Role.SM, subject="dev-dana"), node_id="loyalty")

    assert answer == {"error": PROJECT_OUTSIDE_SCOPE}


async def test_an_unknown_id_or_another_kind_of_node_is_an_error() -> None:
    service, store, _registry = await _forecasts()
    tool = _tool(service, store, Role.MGR)

    with pytest.raises(GraphNotFound, match="search_graph_nodes"):
        await tool.run({"node_id": "nope"})
    assert await _run(tool, node_id="CHK-2") == {
        "error": "CHK-2 is a task; delivery_forecast covers a project, pod or program"
    }


def test_the_system_prompt_sends_date_questions_to_the_forecast() -> None:
    assert "Whether a project or pod will make its date is answered from delivery_forecast" in (
        ASK_SYSTEM_PROMPT
    )
    assert "A colour is not a forecast" in ASK_SYSTEM_PROMPT
