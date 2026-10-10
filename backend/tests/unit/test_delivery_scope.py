"""A person's own part of the delivery tree, the reads scoped to it, and its listing.

The demo fixture: Foundations holds the Runtime Pod (Asha, Liam, Omar) and the
Experience Pod (Liam, Maya, Noah); Insights holds the Data Pod (Zoe, Ira, Kai).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.delivery_scope import (
    POD_DATES_OUTSIDE_SCOPE,
    POD_OUTSIDE_SCOPE,
    PROJECT_OUTSIDE_SCOPE,
    DeliveryScopeService,
    MemberScope,
    member_scope,
)
from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied
from core.domain.graph import Developer, EdgeKind, GraphEdge, Pod, Project
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.fixtures.demo_graph import populate_demo_graph

AS_OF = "2026-06-15"


def _as(role: str, user: str) -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    registry = app.state.registry
    asyncio.run(
        populate_demo_graph(
            registry.graph_repository(), registry.time_series_repository(), settings.tenant_id
        )
    )
    with TestClient(app) as test_client:
        yield test_client


# --- whose part is it -----------------------------------------------------------------------


def test_member_scope_reads_membership_the_scrum_master_contact_and_the_project_owner() -> None:
    nodes = [
        Developer(tenant_id="t", id="dev-ira", name="Ira", metadata={"chat_external_id": "U-IRA"}),
        Pod(tenant_id="t", id="pod-a", name="A"),
        Pod(tenant_id="t", id="pod-b", name="B", metadata={"escalation_sm_member_id": "dev-ira"}),
        Pod(tenant_id="t", id="pod-c", name="C"),
        Project(tenant_id="t", id="proj-a", name="Alpha"),
        Project(tenant_id="t", id="proj-b", name="Beta"),
        Project(tenant_id="t", id="proj-c", name="Gamma"),
        # Admins type person fields by hand: a chat id names the member too.
        Project(tenant_id="t", id="proj-owned", name="Owned", metadata={"owner_id": "U-IRA"}),
    ]

    def contains(source: str, target: str) -> GraphEdge:
        return GraphEdge(
            tenant_id="t", from_node_id=source, to_node_id=target, kind=EdgeKind.CONTAINS
        )

    edges = [
        contains("pod-a", "dev-ira"),
        contains("proj-a", "pod-a"),
        contains("proj-b", "pod-b"),
        contains("proj-c", "pod-c"),
        # Not a pod: a project containing the person directly makes nothing theirs.
        contains("proj-c", "dev-ira"),
    ]

    scope = member_scope(nodes, edges, "dev-ira")

    assert scope == MemberScope(
        pods=frozenset({"pod-a", "pod-b"}),
        projects=frozenset({"proj-a", "proj-b", "proj-owned"}),
    )
    assert member_scope(nodes, edges, "nobody") == MemberScope()


async def test_membership_is_read_for_today_and_an_ended_link_no_longer_counts() -> None:
    store = InMemoryGraphStore()
    for node in (
        Developer(tenant_id="t", id="dev-kai", name="Kai"),
        Pod(tenant_id="t", id="pod-a", name="A"),
        Project(tenant_id="t", id="proj-a", name="Alpha"),
    ):
        await store.upsert_node(node)
    await store.add_edge(
        GraphEdge(tenant_id="t", from_node_id="proj-a", to_node_id="pod-a", kind=EdgeKind.CONTAINS)
    )
    membership = GraphEdge(
        tenant_id="t",
        from_node_id="pod-a",
        to_node_id="dev-kai",
        kind=EdgeKind.CONTAINS,
        valid_from=date(2026, 1, 1),
    )
    await store.add_edge(membership)
    await store.end_edge(membership, date(2026, 3, 1))
    kai = Principal(tenant_id="t", subject="dev-kai", roles=frozenset({Role.DEV}))

    before = DeliveryScopeService(store, store, today=lambda: date(2026, 2, 1))
    after = DeliveryScopeService(store, store, today=lambda: date(2026, 4, 1))

    assert (await before.scope_of("t", "dev-kai")).pods == {"pod-a"}
    assert (await after.scope_of("t", "dev-kai")).pods == frozenset()
    await before.ensure_pod_detail(kai, "pod-a")
    with pytest.raises(AuthorizationDenied, match="not your pod"):
        await after.ensure_pod_detail(kai, "pod-a")


# --- the developer's own pod ----------------------------------------------------------------


def test_a_developer_reads_their_own_pods_panel_and_dates(client: TestClient) -> None:
    asha = _as("dev", "dev-asha")
    for read in ("rollup", "checkins", "blockers", "tasks"):
        response = client.get(f"/pods/pod-runtime/{read}?as_of={AS_OF}", headers=asha)
        assert response.status_code == 200, (read, response.text)
        assert response.json()["pod_name"] == "Runtime Pod"
    dates = client.get(f"/pods/pod-runtime/delivery?as_of={AS_OF}", headers=asha)
    assert dates.status_code == 200, dates.text
    assert dates.json()["can_set_dates"] is False
    assert [item["project_id"] for item in dates.json()["projects"]] == ["project-foundations"]


def test_a_developer_reading_another_pod_is_refused_in_plain_words(client: TestClient) -> None:
    asha = _as("dev", "dev-asha")
    for read in ("rollup", "checkins", "blockers", "tasks"):
        response = client.get(f"/pods/pod-experience/{read}?as_of={AS_OF}", headers=asha)
        assert response.status_code == 403, read
        assert response.json()["detail"] == POD_OUTSIDE_SCOPE
    dates = client.get(f"/pods/pod-experience/delivery?as_of={AS_OF}", headers=asha)
    assert dates.status_code == 403
    assert dates.json()["detail"] == POD_DATES_OUTSIDE_SCOPE
    # Pod routes still answer only for a pod: a project id is no pod of theirs.
    assert (
        client.get(f"/pods/project-foundations/rollup?as_of={AS_OF}", headers=asha).status_code
        == 403
    )
    # No project-level data for a developer, their own project included.
    progress = client.get(f"/projects/project-foundations/progress?as_of={AS_OF}", headers=asha)
    delivery = client.get(f"/projects/project-foundations/delivery?as_of={AS_OF}", headers=asha)
    assert progress.status_code == 403
    assert progress.json()["detail"] == "dev-asha is not authorized for read_project_progress"
    assert delivery.status_code == 403


# --- the scrum master's projects ------------------------------------------------------------


def test_a_scrum_master_reads_a_project_one_of_their_pods_works_on(client: TestClient) -> None:
    zoe = _as("sm", "dev-zoe")
    progress = client.get(f"/projects/project-insights/progress?as_of={AS_OF}", headers=zoe)
    delivery = client.get(f"/projects/project-insights/delivery?as_of={AS_OF}", headers=zoe)

    assert progress.status_code == 200, progress.text
    assert progress.json()["project_name"] == "Insights"
    assert delivery.status_code == 200, delivery.text
    assert [pod["name"] for pod in delivery.json()["pods"]] == ["Data Pod"]


def test_a_scrum_master_reading_another_project_is_refused_in_plain_words(
    client: TestClient,
) -> None:
    zoe = _as("sm", "dev-zoe")
    for path in ("progress", "delivery"):
        response = client.get(f"/projects/project-foundations/{path}?as_of={AS_OF}", headers=zoe)
        assert response.status_code == 403, path
        assert response.json()["detail"] == PROJECT_OUTSIDE_SCOPE
    # Unchanged: a scrum master reads every pod's detail, as before this read
    # was scoped; Delivery only lists their own projects' pods.
    assert client.get(f"/pods/pod-runtime/checkins?as_of={AS_OF}", headers=zoe).status_code == 200
    # Unchanged: the project's requirements and forecast history stay with
    # its progress readers everywhere, outside the Delivery panel.
    assert (
        client.get(
            f"/projects/project-insights/requirements?as_of={AS_OF}", headers=zoe
        ).status_code
        == 403
    )


# --- everyone else, unchanged ---------------------------------------------------------------


def test_other_roles_keep_the_reads_they_had(client: TestClient) -> None:
    maya = _as("po", "dev-maya")
    # A product owner reads every project, theirs or not, and no pod's detail.
    for project in ("project-foundations", "project-insights"):
        assert (
            client.get(f"/projects/{project}/progress?as_of={AS_OF}", headers=maya).status_code
            == 200
        )
    refused = client.get(f"/pods/pod-experience/checkins?as_of={AS_OF}", headers=maya)
    assert refused.status_code == 403
    assert refused.json()["detail"] == "dev-maya is not authorized for read_pod_checkins"
    assert client.get(f"/pods/pod-data/delivery?as_of={AS_OF}", headers=maya).status_code == 200

    elena = _as("exec", "elena")
    assert client.get(f"/pods/pod-runtime/blockers?as_of={AS_OF}", headers=elena).status_code == 403
    assert client.get(f"/pods/pod-runtime/tasks?as_of={AS_OF}", headers=elena).status_code == 403

    for role in ("mgr", "admin"):
        who = _as(role, "someone")
        assert client.get(f"/pods/pod-data/rollup?as_of={AS_OF}", headers=who).status_code == 200
        assert (
            client.get(
                f"/projects/project-insights/progress?as_of={AS_OF}", headers=who
            ).status_code
            == 200
        )


# --- the listing ----------------------------------------------------------------------------


def _tree(client: TestClient, role: str, user: str) -> dict[str, list[dict[str, object]]]:
    response = client.get(f"/me/delivery-tree?as_of={AS_OF}", headers=_as(role, user))
    assert response.status_code == 200, response.text
    body: dict[str, list[dict[str, object]]] = response.json()
    return body


def _rows(items: list[dict[str, object]]) -> list[tuple[object, ...]]:
    return [(item["name"], item["access"], item["rag"], item["own"]) for item in items]


def test_a_developer_lists_every_pod_of_their_project_with_data_only_for_their_own(
    client: TestClient,
) -> None:
    tree = _tree(client, "dev", "dev-asha")

    assert _rows(tree["programs"]) == [("Platform Program", "name", None, True)]
    assert _rows(tree["projects"]) == [("Foundations", "name", None, True)]
    assert tree["projects"][0]["parent_ids"] == ["program-platform"]
    assert _rows(tree["pods"]) == [
        ("Experience Pod", "name", None, False),
        ("Runtime Pod", "panel", "amber", True),
    ]
    assert [pod["parent_ids"] for pod in tree["pods"]] == [
        ["project-foundations"],
        ["project-foundations"],
    ]


def test_a_scrum_master_and_a_product_owner_list_their_projects_and_all_their_pods(
    client: TestClient,
) -> None:
    sm = _tree(client, "sm", "dev-asha")
    assert _rows(sm["projects"]) == [("Foundations", "panel", "unknown", True)]
    assert _rows(sm["pods"]) == [
        ("Experience Pod", "dates", "unknown", False),
        ("Runtime Pod", "panel", "amber", True),
    ]

    po = _tree(client, "po", "dev-maya")
    assert _rows(po["projects"]) == [("Foundations", "panel", "unknown", True)]
    assert _rows(po["pods"]) == [
        ("Experience Pod", "dates", "unknown", True),
        ("Runtime Pod", "dates", "amber", False),
    ]
    # The program is a heading above their projects: no rollup of it is theirs to read.
    assert _rows(po["programs"]) == [("Platform Program", "name", None, True)]


def test_someone_in_no_pod_lists_nothing_and_a_manager_opens_every_pod_of_theirs(
    client: TestClient,
) -> None:
    nobody = _tree(client, "exec", "elena")
    assert (nobody["programs"], nobody["projects"], nobody["pods"]) == ([], [], [])
    manager = _tree(client, "mgr", "dev-liam")
    assert [(pod["name"], pod["access"]) for pod in manager["pods"]] == [
        ("Experience Pod", "panel"),
        ("Runtime Pod", "panel"),
    ]
    assert manager["programs"][0]["access"] == "panel"
