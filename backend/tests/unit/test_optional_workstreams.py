"""Workstreams are optional: the persona views show only the ones in use.

Pods are the one grouping a tenant must set up. A workstream is in use on a
day when it contains a task or work item; an empty one -- however many pods
serve it, whatever repos or owner it has -- is in no navigator, heat row,
headline count, program panel, link chip, Ask answer or flow row, and never
changes its project's colour. A direct read still opens it and says no work
is in it yet. Admin and the config API keep every workstream.

One small tenant throughout: Commerce Program > Checkout project, Web Pod
(Ada, confirmed) in it. Payments is a workstream holding one ticket; Search
is linked to the project and served by Web Pod, but holds nothing.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.ask_service import GraphNeighborsTool, SearchGraphNodesTool
from core.application.config_service import DirectoryService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.persona_views import PersonaViewService
from core.application.rollup_service import NO_WORK_REASON, RollupService
from core.domain.graph import (
    Developer,
    EdgeKind,
    GraphEdge,
    Pod,
    Program,
    Project,
    Task,
    WorkItem,
    Workstream,
    workstreams_in_use,
)
from core.domain.rollup import Rag
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore

DAY = date(2026, 3, 9)


def _edge(
    parent: str,
    child: str,
    kind: EdgeKind = EdgeKind.CONTAINS,
    *,
    valid_from: date | None = None,
    valid_to: date | None = None,
) -> GraphEdge:
    return GraphEdge(
        tenant_id="demo",
        from_node_id=parent,
        to_node_id=child,
        kind=kind,
        valid_from=valid_from,
        valid_to=valid_to,
    )


async def _tenant() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for node in (
        Program(tenant_id="demo", id="prog", name="Commerce Program"),
        Project(tenant_id="demo", id="proj", name="Checkout"),
        Pod(tenant_id="demo", id="pod-web", name="Web Pod"),
        Developer(tenant_id="demo", id="dev-ada", name="Ada Lind"),
        Workstream(tenant_id="demo", id="ws-pay", name="Payments"),
        # A near target date: still no status for a workstream with no work.
        Workstream(
            tenant_id="demo",
            id="ws-search",
            name="Search",
            metadata={"github_repos": "acme/search", "target_date": "2026-03-12"},
        ),
        Task(tenant_id="demo", id="SHOP-1", name="Refund form", metadata={"status": "green"}),
    ):
        await store.upsert_node(node)
    for edge in (
        _edge("prog", "proj"),
        _edge("proj", "pod-web"),
        _edge("pod-web", "dev-ada"),
        _edge("proj", "ws-pay"),
        _edge("proj", "ws-search"),
        _edge("ws-pay", "SHOP-1"),
        _edge("pod-web", "ws-pay", EdgeKind.ASSIGNED_TO),
        _edge("pod-web", "ws-search", EdgeKind.ASSIGNED_TO),
    ):
        await store.add_edge(edge)
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-ada",
            as_of=DAY,
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="On track.",
        )
    )
    return store


def _personas(store: InMemoryGraphStore) -> PersonaViewService:
    return PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )


# ---- what puts a workstream in use ------------------------------------------------------


def test_only_work_it_holds_on_the_day_puts_a_workstream_in_use() -> None:
    nodes = [
        Workstream(tenant_id="demo", id="ws-task", name="Task"),
        Workstream(tenant_id="demo", id="ws-item", name="Item"),
        Workstream(tenant_id="demo", id="ws-later", name="Later"),
        Workstream(tenant_id="demo", id="ws-ended", name="Ended"),
        Workstream(tenant_id="demo", id="ws-team", name="Team only"),
        Task(tenant_id="demo", id="T-1", name="Task"),
        WorkItem(tenant_id="demo", id="WI-1", name="Item"),
        Pod(tenant_id="demo", id="pod-1", name="Pod"),
        Developer(tenant_id="demo", id="dev-1", name="Dev"),
    ]
    edges = [
        _edge("ws-task", "T-1"),
        _edge("ws-item", "WI-1"),
        _edge("ws-later", "T-1", valid_from=DAY + timedelta(days=1)),
        _edge("ws-ended", "WI-1", valid_to=DAY),
        # Pods and people do not put one in use, contained or assigned.
        _edge("ws-team", "pod-1"),
        _edge("ws-team", "dev-1"),
        _edge("pod-1", "ws-team", EdgeKind.ASSIGNED_TO),
        _edge("ws-team", "T-1", EdgeKind.ASSIGNED_TO),
    ]

    assert workstreams_in_use(nodes, edges, DAY) == {"ws-task", "ws-item"}
    assert "ws-later" in workstreams_in_use(nodes, edges, DAY + timedelta(days=1))


# ---- directory: navigator, heat rows, palette and link chips -------------------------------


async def test_the_directory_lists_only_workstreams_in_use() -> None:
    store = await _tenant()
    directory = DirectoryService(store, store)

    workstreams = await directory.list_workstreams("demo", DAY)
    [project] = await directory.list_projects("demo", DAY)
    [pod] = await directory.list_pods("demo", DAY)
    [program] = await directory.list_programs("demo", DAY)

    assert [item.id for item in workstreams] == ["ws-pay"]
    assert workstreams[0].in_use
    assert [item.id for item in await directory.list_project_workstreams("demo", "proj", DAY)] == [
        "ws-pay"
    ]
    # No chip leads to the empty one: every item names only workstreams in use.
    assert project.workstream_ids == ("ws-pay",)
    assert pod.workstream_ids == ("ws-pay",)
    assert program.workstream_ids == ()


async def test_a_direct_read_still_opens_an_empty_workstream() -> None:
    store = await _tenant()

    item = await DirectoryService(store, store).get_workstream("demo", "ws-search", DAY)

    assert item.id == "ws-search"
    assert item.in_use is False
    assert item.project_ids == ("proj",)
    assert item.pod_ids == ("pod-web",)


async def test_a_workstream_shows_once_work_is_put_in_it() -> None:
    store = await _tenant()
    await store.upsert_node(WorkItem(tenant_id="demo", id="WI-9", name="Search index"))
    await store.add_edge(_edge("ws-search", "WI-9", valid_from=DAY))
    directory = DirectoryService(store, store)

    assert [item.id for item in await directory.list_workstreams("demo", DAY)] == [
        "ws-pay",
        "ws-search",
    ]
    assert [item.id for item in await directory.list_workstreams("demo", DAY - timedelta(1))] == [
        "ws-pay"
    ]


# ---- rollups, heat and the program panel --------------------------------------------------


async def test_an_empty_workstream_never_changes_its_projects_colour() -> None:
    store = await _tenant()
    tree = await store.get_program_tree("demo", "prog", DAY)

    by_id = {
        status.entity_ref.id: status
        for status in await RollupService(store, store).compute(tree, DAY)
    }

    assert by_id["proj"].rag is Rag.GREEN
    assert by_id["prog"].rag is Rag.GREEN
    assert all("ws-search" != factor.source_ref.id for factor in by_id["proj"].factors)
    # It keeps its own status, which says plainly why it has none.
    assert by_id["ws-search"].rag is Rag.UNKNOWN
    assert [factor.description for factor in by_id["ws-search"].factors] == [NO_WORK_REASON]


async def test_the_heat_map_has_no_cell_for_an_empty_workstream() -> None:
    store = await _tenant()

    view = await _personas(store).portfolio_heatmap("demo", DAY, "prog", today=DAY)

    assert [cell.entity_ref.id for cell in view.cells if cell.row == "workstream"] == ["ws-pay"]
    assert "ws-search" not in view.columns


async def test_no_workstream_row_when_none_is_in_use() -> None:
    store = await _tenant()
    await store.remove_edge(_edge("ws-pay", "SHOP-1"))

    view = await _personas(store).portfolio_heatmap("demo", DAY, "prog", today=DAY)
    attention = await _personas(store).portfolio_attention(
        "demo", DAY, "prog", None, today=DAY, clock=False
    )

    assert "workstream" not in view.rows
    # The team reported; empty workstreams are no "nothing reported" to count.
    assert attention.rag is Rag.GREEN
    assert attention.headline == "Green: the one person confirmed with no open blockers."


async def test_the_program_tree_leaves_out_empty_workstreams_and_their_links() -> None:
    store = await _tenant()

    tree = await _personas(store).program_tree("demo", "prog", DAY)
    root = await _personas(store).program_tree("demo", "ws-search", DAY)

    ids = {node.id for node in tree.nodes}
    assert "ws-pay" in ids
    assert "ws-search" not in ids
    assert all("ws-search" not in (edge.from_node_id, edge.to_node_id) for edge in tree.edges)
    # Asked about by itself, it is the root and is kept.
    assert [node.id for node in root.nodes] == ["ws-search"]


async def test_an_empty_workstreams_panel_says_no_work_is_in_it_yet() -> None:
    store = await _tenant()

    view = await _personas(store).workstream_progress("demo", "ws-search", DAY)

    # The near target date does not colour it: there is no work to be late.
    assert view.rag is Rag.UNKNOWN
    assert view.source is StatusSource.UNKNOWN
    assert (view.total_tasks, view.percent_complete, view.tasks) == (0, 0.0, ())
    assert [factor.description for factor in view.factors] == [NO_WORK_REASON]
    # One in use reads as it always did.
    in_use = await _personas(store).workstream_progress("demo", "ws-pay", DAY)
    assert (in_use.rag, in_use.total_tasks, in_use.percent_complete) == (Rag.GREEN, 1, 100.0)


# ---- Ask and flow -------------------------------------------------------------------------


async def test_ask_finds_an_empty_workstream_only_when_named_exactly() -> None:
    store = await _tenant()
    search = SearchGraphNodesTool(tenant_id="demo", repository=store, as_of=DAY)
    neighbors = GraphNeighborsTool(tenant_id="demo", repository=store, as_of=DAY)

    listed = json.loads(await search.run({"kinds": ["workstream"]}))
    named = json.loads(await search.run({"query": "search", "kinds": ["workstream"]}))
    edges = json.loads(await neighbors.run({"node_id": "proj", "direction": "out"}))

    assert [match["id"] for match in listed] == ["ws-pay"]
    assert "in_use" not in listed[0]
    assert named == [
        {
            "id": "ws-search",
            "kind": "workstream",
            "name": "Search",
            "metadata": {"github_repos": "acme/search", "target_date": "2026-03-12"},
            "in_use": False,
            "note": NO_WORK_REASON,
        }
    ]
    assert [edge["neighbor_id"] for edge in edges["edges"]] == ["pod-web", "ws-pay"]


async def test_flow_has_no_row_for_an_empty_workstream() -> None:
    store = await _tenant()
    await store.upsert_node(WorkItem(tenant_id="demo", id="WI-1", name="Refunds"))
    await store.add_edge(_edge("ws-pay", "WI-1"))

    view = await FlowMetricsService(store, store).portfolio_flow("demo", DAY)

    assert [row.workstream_id for row in view.workstreams] == ["ws-pay"]


# ---- the API: persona reads filter, config keeps every one --------------------------------


def test_config_lists_every_workstream_while_the_directory_shows_those_in_use(
    settings: Settings,
) -> None:
    as_of = DAY.isoformat()
    app = create_app(settings=settings)
    with TestClient(app) as client:
        client.post("/config/projects", json={"id": "proj", "name": "Checkout"})
        for workstream_id, name in (("ws-pay", "Payments"), ("ws-search", "Search")):
            client.post("/config/workstreams", json={"id": workstream_id, "name": name})
            client.post(f"/config/projects/proj/workstreams/{workstream_id}")
        client.post(
            "/config/work-items",
            json={"id": "WI-1", "name": "Refunds", "workstream_id": "ws-pay"},
        )

        config = client.get("/config/workstreams")
        listed = client.get(f"/workstreams?as_of={as_of}")
        project = client.get(f"/projects?as_of={as_of}")
        project_workstreams = client.get(f"/projects/proj/workstreams?as_of={as_of}")
        empty = client.get(f"/workstreams/ws-search?as_of={as_of}")
        progress = client.get(f"/workstreams/ws-search/progress?as_of={as_of}")
        deleted = client.delete("/config/workstreams/ws-search")

    assert sorted(item["id"] for item in config.json()) == ["ws-pay", "ws-search"]
    assert [(item["id"], item["in_use"]) for item in listed.json()] == [("ws-pay", True)]
    assert project.json()[0]["workstream_ids"] == ["ws-pay"]
    assert [item["id"] for item in project_workstreams.json()] == ["ws-pay"]
    assert empty.status_code == 200
    assert (empty.json()["id"], empty.json()["in_use"]) == ("ws-search", False)
    assert progress.json()["factors"][0]["description"] == NO_WORK_REASON
    assert deleted.status_code == 204
