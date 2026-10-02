"""The reasons behind a node's colour, as the Delivery panels read them.

Project and workstream reasons ride on their progress responses; a pod's come
from ``/pods/{id}/rollup``. Each is gated by the capability that already
guards that panel's detail, so nobody reads a reason they could not before.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.persona_views import PersonaViewService
from core.domain.errors import GraphNotFound
from core.domain.graph import Developer, EdgeKind, EntityRef, GraphEdge, NodeKind, Pod, Project
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.fixtures.demo_graph import populate_demo_graph

AS_OF = "2026-06-15"


@pytest.mark.parametrize("roles", ["sm", "admin"])
def test_pod_rollup_is_readable_with_the_pod_capabilities(settings: Settings, roles: str) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": roles}))
    with TestClient(app) as client:
        _populate(app, settings)
        response = client.get(f"/pods/pod-runtime/rollup?as_of={AS_OF}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pod_id"] == "pod-runtime"
    assert body["pod_name"] == "Runtime Pod"
    # The fixture's stored rollup for the day: amber, citing a task by id.
    assert body["rag"] == "amber"
    assert [factor["description"] for factor in body["factors"]] == [
        "Graph persistence has an unresolved schema review."
    ]
    assert body["factors"][0]["source_ref"]["id"] == "task-graph"
    assert body["source_names"] == {"task-graph": "Graph persistence"}


@pytest.mark.parametrize("roles", ["po", "mgr", "exec", "dev"])
def test_pod_rollup_is_denied_without_the_pod_capabilities(settings: Settings, roles: str) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": roles}))
    with TestClient(app) as client:
        _populate(app, settings)
        response = client.get(f"/pods/pod-runtime/rollup?as_of={AS_OF}")

    assert response.status_code == 403


def test_pod_rollup_answers_only_for_a_pod(settings: Settings) -> None:
    # The tree lookup takes any node id. A project's reasons need project
    # progress, which a scrum master does not hold, so asking for a project
    # through the pod route must not answer.
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "sm"}))
    with TestClient(app) as client:
        _populate(app, settings)
        project = client.get(f"/pods/project-foundations/rollup?as_of={AS_OF}")
        missing = client.get(f"/pods/pod-nowhere/rollup?as_of={AS_OF}")

    assert project.status_code == 404
    assert missing.status_code == 404


def test_progress_names_the_nodes_its_factors_cite(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "po"}))
    with TestClient(app) as client:
        _populate(app, settings)
        project = client.get(f"/projects/project-foundations/progress?as_of={AS_OF}")
        workstream = client.get(f"/workstreams/workstream-jira-git-sync/progress?as_of={AS_OF}")

    for response in (project, workstream):
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["factors"], body
        cited = {factor["source_ref"]["id"] for factor in body["factors"]}
        # Every name given belongs to a cited node, and no cited node in the
        # tree is left as a bare id.
        assert set(body["source_names"]) == cited, body
    assert "Liam" in project.json()["source_names"].values()


async def test_pod_rollup_computes_when_nothing_is_stored_and_names_people() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    await _pod_with_two_developers(store)
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=as_of - timedelta(days=3),
            source=StatusSource.STALE,
            blockers=(),
            summary="Status summary.",
        )
    )

    view = await _service(store).pod_rollup("demo", "pod-a", as_of)

    # A stale check-in outranks a missing one: the pod is amber.
    assert view.rag is Rag.AMBER
    descriptions = {factor.description: factor.source_ref.id for factor in view.factors}
    assert descriptions == {
        "Developer status is stale and needs confirmation.": "dev-1",
        "No developer status data is available.": "dev-2",
    }
    assert view.source_names == {"dev-1": "Asha", "dev-2": "Ben"}
    # A read never records: the computed rollup leaves no stored row behind.
    assert await store.latest_node_status("demo", _pod_ref(), as_of) is None


async def test_pod_rollup_leaves_a_cited_node_outside_the_tree_unnamed() -> None:
    store = InMemoryGraphStore()
    as_of = date(2026, 1, 10)
    await _pod_with_two_developers(store)
    await store.record_node_status(
        NodeStatus(
            entity_ref=_pod_ref(),
            rag=Rag.RED,
            source=StatusSource.CONFIRMED,
            factors=(
                RollupFactor(
                    description="Task Gone is blocked.",
                    contributes=Rag.RED,
                    source_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-gone"),
                ),
            ),
            as_of=as_of,
        )
    )

    view = await _service(store).pod_rollup("demo", "pod-a", as_of)

    assert view.rag is Rag.RED
    assert [factor.source_ref.id for factor in view.factors] == ["task-gone"]
    assert view.source_names == {}


async def test_pod_rollup_rejects_a_node_that_is_not_a_pod() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Project(tenant_id="demo", id="project-a", name="Project A"))

    with pytest.raises(GraphNotFound):
        await _service(store).pod_rollup("demo", "project-a", date(2026, 1, 10))


def _populate(app: FastAPI, settings: Settings) -> None:
    asyncio.run(
        populate_demo_graph(
            app.state.registry.graph_repository(),
            app.state.registry.time_series_repository(),
            settings.tenant_id,
        )
    )


def _service(store: InMemoryGraphStore) -> PersonaViewService:
    return PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )


def _pod_ref() -> EntityRef:
    return EntityRef(tenant_id="demo", kind=NodeKind.POD, id="pod-a")


async def _pod_with_two_developers(store: InMemoryGraphStore) -> None:
    await store.upsert_node(Pod(tenant_id="demo", id="pod-a", name="Pod A"))
    for developer_id, name in (("dev-1", "Asha"), ("dev-2", "Ben")):
        await store.upsert_node(Developer(tenant_id="demo", id=developer_id, name=name))
        await store.add_edge(
            GraphEdge(
                tenant_id="demo",
                from_node_id="pod-a",
                to_node_id=developer_id,
                kind=EdgeKind.CONTAINS,
            )
        )
