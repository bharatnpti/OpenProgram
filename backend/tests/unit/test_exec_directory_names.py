"""An executive reads no person from the directory lists, the review flow or the feed (D87, D103).

``GET /pods`` named each pod's escalation contacts and listed its member ids; the
programs, projects and workstreams lists named their owner, TPM and scrum master;
the review flow named each request's author and the feed each person's event. Each
role reads the same routes: a manager, a product owner, a scrum master, an admin
and a developer keep them as built, and an executive reads the role ("the scrum
master") or nothing, decided on the server for every client.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.dtos import DirectoryItemResponse, DirectoryPersonResponse
from api.main import create_app
from api.people_projection import for_reader
from config.settings import Settings
from core.application.people_words import people_words
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    NodeKind,
    Pod,
    Task,
)

T = "demo"
PEOPLE = ("Mina Patel", "Ira Novak", "Asha Rao", "Liam Chen", "Zoe Almeida")
#: Everything that names a member without a name: their node ids and chat ids.
IDS = ("U1001", "U1002", "U1003", "U1005", "U1006", "W0CHATSM", "W0CHATMGR")
READERS = {
    "exec": "U1011",
    "mgr": "U1001",
    "po": "U1003",
    "sm": "U1006",
    "admin": "U1001",
    "dev": "U1002",
}
LISTS = (
    "/pods",
    "/projects",
    "/programs",
    "/workstreams",
    "/workstreams/ws-pay",
    "/projects/checkout/workstreams",
)


def _as(role: str, subject: str | None = None) -> dict[str, str]:
    """``role`` may be several ("exec,mgr"): the first one's member reads."""
    return {
        "x-openprogram-dev-user": subject or READERS[role.split(",")[0]],
        "x-openprogram-dev-roles": role,
    }


def _said(body: object, words: set[str]) -> list[str]:
    text = json.dumps(body)
    return sorted(word for word in words if re.search(rf"\b{re.escape(word)}\b", text))


def _named(body: object) -> list[str]:
    """The seeded members' names (and first or last names) that the body says."""
    return _said(body, {part for name in PEOPLE for part in (name, *name.split())})


def _leaks(body: object) -> list[str]:
    """Their names and ids, node and chat."""
    return _said(body, {part for name in PEOPLE for part in (name, *name.split())} | set(IDS))


def _seed(client: TestClient) -> None:
    """Checkout (owned by Mina) in Platform, on Payments Pod (Ira runs it, Liam in it) and
    Storefront Pod (Zoe); Payments Pod names Ira and Asha as its escalation contacts."""
    for path, body in (
        ("/config/programs", {"id": "prog-1", "name": "Platform"}),
        ("/config/projects", {"id": "checkout", "name": "Checkout"}),
        ("/config/pods", {"id": "pod-pay", "name": "Payments Pod"}),
        ("/config/pods", {"id": "pod-web", "name": "Storefront Pod"}),
        ("/config/workstreams", {"id": "ws-pay", "name": "Payment API"}),
    ):
        assert client.post(path, json=body).status_code == 201, path
    for member, name, roles in (
        ("U1003", "Mina Patel", "po"),
        ("U1006", "Ira Novak", "sm"),
        ("U1001", "Asha Rao", "mgr,admin"),
        ("U1011", "Elena Fischer", "exec"),
        ("U1002", "Liam Chen", "dev"),
        ("U1005", "Zoe Almeida", "dev"),
    ):
        created = client.post(
            "/config/members",
            json={"id": member, "name": name, "metadata": {"app_roles": roles}},
        )
        assert created.status_code == 201, created.text
    for pod, member in (("pod-pay", "U1006"), ("pod-pay", "U1002"), ("pod-web", "U1005")):
        linked = client.post(f"/config/pods/{pod}/members/{member}", json={"role": "member"})
        assert linked.status_code in {200, 201}, linked.text
    for pod in ("pod-pay", "pod-web"):
        assert client.post(f"/config/pods/{pod}/projects/checkout").status_code in {200, 201}
    assert client.post(
        "/config/projects/checkout/program", json={"program_id": "prog-1"}
    ).is_success
    assert client.post("/config/projects/checkout/workstreams/ws-pay").status_code in {200, 201}
    owners = {"owner_id": "U1003", "tpm_id": "U1001", "sm_id": "U1006"}
    for kind, node, extra in (
        ("pods", "pod-pay", {"phase": "build", "lead": "Asha Rao"}),
        ("pods", "pod-web", {}),
        ("projects", "checkout", {"jira_project_key": "CHK"}),
        ("programs", "prog-1", {}),
        ("workstreams", "ws-pay", {}),
    ):
        updated = client.put(
            f"/config/{kind}/{node}",
            json={"metadata": {**owners, **extra}, "description": "Owned by Mina Patel"},
        )
        assert updated.status_code == 200, updated.text
    for member, chat in (("U1006", "W0CHATSM"), ("U1001", "W0CHATMGR")):
        linked = client.put(f"/config/members/{member}/identity-link", json={"chat_user_id": chat})
        assert linked.status_code == 200, linked.text
    contacts = client.put(
        "/config/pods/pod-pay/escalation-contacts",
        json={"scrum_master": {"member_id": "U1006"}, "manager": {"member_id": "U1001"}},
    )
    assert contacts.status_code == 200, contacts.text
    registry = client.app.state.registry  # type: ignore[attr-defined]
    graph = registry.graph_repository()

    async def add() -> None:
        await graph.upsert_node(
            Task(tenant_id=T, id="CHK-1", name="Checkout API", metadata={"key": "CHK-1"})
        )
        await graph.add_edge(
            GraphEdge(
                tenant_id=T, from_node_id="ws-pay", to_node_id="CHK-1", kind=EdgeKind.CONTAINS
            )
        )
        # An open merge request Liam wrote, Liam's check-in, a request Liam raised of Zoe
        # and a work item that moved, as the review flow and the feed read them.
        now = datetime.now(tz=UTC)
        for source, kind, entity, payload in (
            (
                "vcs_pull_request",
                NodeKind.DEVELOPER,
                "U1002",
                {
                    "repo": "acme/checkout-api",
                    "id": "7",
                    "title": "Retry key for Liam Chen's API",
                    "state": "open",
                    "merged": False,
                    "draft": False,
                    "author_name": "Liam Chen",
                    "opened_at": now.isoformat(),
                },
            ),
            (
                "checkin",
                NodeKind.DEVELOPER,
                "U1002",
                {"developer_name": "Liam Chen", "status_source": "confirmed", "blocker_count": 1},
            ),
            (
                "cross_person_request",
                NodeKind.DEVELOPER,
                "U1002",
                {
                    "request_id": "req-1",
                    "reporter_id": "U1002",
                    "reporter_name": "Liam Chen",
                    "referenced_person_id": "U1005",
                    "referenced_person_name": "Zoe Almeida",
                    "transition": "opened",
                    "summary": "a review",
                },
            ),
            (
                "work_item",
                NodeKind.TASK,
                "CHK-1",
                {"name": "Checkout API", "from_state": "todo", "to_state": "in_progress"},
            ),
        ):
            await graph.append_fact(
                FactEvent(
                    tenant_id=T,
                    source=source,
                    entity_ref=EntityRef(tenant_id=T, kind=kind, id=entity),
                    payload=payload,
                    observed_at=now,
                    correlation_id=f"{source}:{entity}",
                )
            )

    client.portal.call(add)  # type: ignore[union-attr]


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    return TestClient(app)


def _pod(client: TestClient, role: str, pod_id: str = "pod-pay") -> dict[str, Any]:
    response = client.get("/pods", headers=_as(role))
    assert response.status_code == 200, response.text
    return next(pod for pod in response.json() if pod["id"] == pod_id)


def test_the_executive_reads_no_member_from_any_directory_list(client: TestClient) -> None:
    with client:
        _seed(client)
        reads = {path: client.get(path, headers=_as("exec")) for path in LISTS}

    for path, response in reads.items():
        assert response.status_code == 200, (path, response.text)
        body = response.json()
        assert _leaks(body) == [], path
        for item in body if isinstance(body, list) else [body]:
            assert (item["member_ids"], item["people"]) == ([], []), path
            assert not {"owner_id", "tpm_id", "sm_id", "lead"} & set(item["metadata"]), path
            # A description that names a person says their role instead.
            assert item["description"] == "Owned by the product owner", path


def test_a_pods_escalation_contacts_read_as_their_roles_to_the_executive(
    client: TestClient,
) -> None:
    with client:
        _seed(client)
        pod = _pod(client, "exec")
        other = _pod(client, "exec", "pod-web")

    assert pod["metadata"] == {
        "phase": "build",
        "description": "Owned by the product owner",
        "escalation_sm_display_name": "the scrum master",
        "escalation_manager_display_name": "the manager",
    }
    # What is not a person stays: the team, the projects it works on, its work.
    assert (pod["name"], pod["project_ids"]) == ("Payments Pod", ["checkout"])
    assert pod["member_ids"] == [] and pod["people"] == []
    # A pod with no contact says none, and still names nobody.
    assert not {key for key in other["metadata"] if key.startswith("escalation_")}


@pytest.mark.parametrize("role", ["mgr", "po", "sm", "admin", "dev"])
def test_every_other_role_reads_the_directory_as_built(client: TestClient, role: str) -> None:
    with client:
        _seed(client)
        pod = _pod(client, role)
        reads = {path: client.get(path, headers=_as(role)).json() for path in LISTS}

    assert pod["member_ids"] == ["U1002", "U1006"]
    assert [(person["key"], person["name"]) for person in pod["people"]] == [
        ("owner_id", "Mina Patel"),
        ("tpm_id", "Asha Rao"),
        ("sm_id", "Ira Novak"),
    ]
    metadata = pod["metadata"]
    assert metadata["owner_id"] == "U1003" and metadata["lead"] == "Asha Rao"
    assert metadata["escalation_sm_display_name"] == "Ira Novak"
    assert metadata["escalation_manager_display_name"] == "Asha Rao"
    assert metadata["escalation_sm_chat_external_id"] == "W0CHATSM"
    assert metadata["escalation_manager_member_id"] == "U1001"
    for path, body in reads.items():
        items = body if isinstance(body, list) else [body]
        assert items, path
        assert all(item["description"] == "Owned by Mina Patel" for item in items), path
        assert all(item["people"][0]["name"] == "Mina Patel" for item in items), path


@pytest.mark.parametrize("roles", ["exec,mgr", "exec,po", "exec,sm", "exec,admin", "exec,dev"])
def test_an_executive_who_holds_another_role_reads_names_as_that_role(
    client: TestClient, roles: str
) -> None:
    with client:
        _seed(client)
        pod = _pod(client, roles)

    assert pod["member_ids"] == ["U1002", "U1006"]
    assert pod["metadata"]["escalation_sm_display_name"] == "Ira Novak"
    assert pod["people"][2]["name"] == "Ira Novak"


def test_the_review_flow_and_the_feed_name_nobody_to_the_executive(client: TestClient) -> None:
    with client:
        _seed(client)
        flow = {
            role: client.get("/portfolio/pr-flow", headers=_as(role)) for role in ("exec", "mgr")
        }
        feed = {role: client.get("/portfolio/feed", headers=_as(role)) for role in ("exec", "mgr")}

    for response in (*flow.values(), *feed.values()):
        assert response.status_code == 200, response.text
    request = flow["exec"].json()["items"][0]
    assert (request["author_name"], request["title"]) == (
        "Payments Pod",
        "Retry key for Payments Pod's API",
    )
    [event] = [item for item in feed["exec"].json()["items"] if item["source"] == "checkin"]
    assert event["person_name"] == "Payments Pod"
    assert event["summary"].startswith("Check-in updated for Payments Pod: confirmed")
    # No name and no member id, node or chat, is left in either read.
    for response in (flow["exec"], feed["exec"]):
        assert _leaks(response.json()) == []
    # A manager reads who wrote the request and whose check-in it was.
    assert flow["mgr"].json()["items"][0]["author_name"] == "Liam Chen"
    [seen] = [item for item in feed["mgr"].json()["items"] if item["source"] == "checkin"]
    assert seen["person_name"] == "Liam Chen"


def test_a_feed_event_about_a_person_has_no_entity_for_the_executive(client: TestClient) -> None:
    with client:
        _seed(client)
        exec_feed = client.get("/portfolio/feed", headers=_as("exec")).json()["items"]
        mgr_feed = client.get("/portfolio/feed", headers=_as("mgr")).json()["items"]
        mixed = client.get("/portfolio/feed", headers=_as("exec,mgr")).json()["items"]

    by_source = {item["source"]: item for item in exec_feed}
    # An event about a person holds no reference to them, whatever its source.
    for source in ("checkin", "vcs_pull_request", "cross_person_request"):
        assert by_source[source]["entity_ref"] is None, source
    # One about a work item keeps it: the thing moved, not the person.
    assert by_source["work_item"]["entity_ref"] == {"tenant_id": T, "kind": "task", "id": "CHK-1"}
    # The people a request names read as their teams, by id and by name.
    request = by_source["cross_person_request"]
    assert (request["details"]["reporter_id"], request["details"]["reporter_name"]) == (
        "Payments Pod",
        "Payments Pod",
    )
    assert (
        request["details"]["referenced_person_id"],
        request["details"]["referenced_person_name"],
    ) == ("Storefront Pod", "Storefront Pod")
    assert request["summary"] == (
        "Cross-person request opened: Payments Pod needs Storefront Pod for a review"
    )
    # A manager, and an executive who is also one, read the entity and the ids as built.
    for items in (mgr_feed, mixed):
        by_kind = {item["source"]: item for item in items}
        assert by_kind["checkin"]["entity_ref"]["id"] == "U1002"
        assert by_kind["cross_person_request"]["details"]["referenced_person_id"] == "U1005"
        assert by_kind["cross_person_request"]["details"]["reporter_name"] == "Liam Chen"


def _seed_pods_of(client: TestClient, sizes: dict[str, int]) -> None:
    """A pod for each id of ``sizes``, with that many members, working on its own project."""
    for pod, size in sizes.items():
        assert client.post("/config/pods", json={"id": pod, "name": pod.title()}).status_code == 201
        for number in range(size):
            member = f"{pod}-m{number}"
            created = client.post(
                "/config/members",
                json={
                    "id": member,
                    "name": f"Member {pod} {number}",
                    "metadata": {"app_roles": "dev"},
                },
            )
            assert created.status_code == 201, created.text
            linked = client.post(f"/config/pods/{pod}/members/{member}", json={"role": "member"})
            assert linked.status_code in {200, 201}, linked.text


def _member_count(client: TestClient, roles: str, **scope: str) -> int | None:
    response = client.get("/portfolio/pr-flow", params=scope, headers=_as(roles))
    assert response.status_code == 200, response.text
    return response.json()["scope"]["member_count"]


def test_the_review_flow_counts_no_pod_of_fewer_than_five_for_the_executive(
    client: TestClient,
) -> None:
    sizes = {"pod-one": 1, "pod-four": 4, "pod-five": 5, "pod-six": 6}
    with client:
        _seed_pods_of(client, sizes)
        counts = {
            role: {pod: _member_count(client, role, pod_id=pod) for pod in sizes}
            for role in ("exec", "mgr", "exec,mgr", "exec,sm", "admin", "po", "sm")
        }
        # No scope, as before: the whole tenant has no count to give.
        tenant = _member_count(client, "exec")

    # The executive reads a count only of a group of five or more (D99, D104).
    assert counts["exec"] == {"pod-one": None, "pod-four": None, "pod-five": 5, "pod-six": 6}
    # Everyone else, an executive who holds another role too among them, reads each count.
    for role in ("mgr", "exec,mgr", "exec,sm", "admin", "po", "sm"):
        assert counts[role] == sizes, role
    assert tenant is None


def test_a_contact_saved_with_only_a_chat_id_still_reads_as_its_role() -> None:
    words = people_words(
        [
            Developer(tenant_id=T, id="U1006", name="Ira Novak", metadata={"app_roles": "sm"}),
            Pod(tenant_id=T, id="pod-pay", name="Payments Pod"),
        ],
        [],
    )
    item = DirectoryItemResponse(
        id="pod-pay",
        kind=NodeKind.POD,
        name="Payments Pod",
        description=None,
        code=None,
        metadata={
            "escalation_sm_chat_external_id": "W0CHATSM",
            "escalation_sm_display_name": None,
            "escalation_manager_display_name": None,
            "ref": "U1006",
            "phase": "build",
        },
        rag=None,
        source=None,
        program_ids=[],
        project_ids=[],
        workstream_ids=[],
        pod_ids=[],
        member_ids=["U1006"],
        task_ids=[],
        people=[
            DirectoryPersonResponse(key="sm_id", id="U1006", member_id="U1006", name="Ira Novak")
        ],
    )

    seen = for_reader(item, words)

    assert seen.metadata == {
        "escalation_sm_display_name": "the scrum master",
        "escalation_manager_display_name": None,
        "phase": "build",
    }
    assert (seen.member_ids, seen.people, seen.description) == ([], [], None)
    # Anyone who reads names gets the item as built.
    assert for_reader(item, None) is item
