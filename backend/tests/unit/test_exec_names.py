"""An executive reads no person's name on the Project page, Today and the briefs (D87, D98).

Each role reads the same routes; a manager, a product owner, a scrum master and
an admin keep the names, and an executive reads the role ("set by the product
owner") or the owning team ("asked by Storefront Pod") instead, decided on the
server for every client.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from api.dtos import (
    EntityRefDto,
    RequirementResponse,
    RiskEvidenceDto,
    RiskFindingResponse,
)
from api.main import create_app
from api.people_projection import for_reader
from config.settings import Settings
from core.application.people_words import people_words
from core.domain.brief import BriefKind, NarrativeBrief
from core.domain.gates import QuestionStatus, TrackedQuestion
from core.domain.graph import Developer, EdgeKind, GraphEdge, NodeKind, Pod, Task
from core.domain.rollup import Rag

T = "demo"
PEOPLE = ("Mina Patel", "Ira Novak", "Asha Rao", "Liam Chen", "Zoe Almeida")
#: (role header, member id) for each reader.
READERS = {
    "exec": "U1011",
    "mgr": "U1001",
    "po": "U1003",
    "sm": "U1006",
    "admin": "U1001",
}


def _as(role: str) -> dict[str, str]:
    return {"x-openprogram-dev-user": READERS[role], "x-openprogram-dev-roles": role}


def _named(body: object) -> list[str]:
    text = json.dumps(body)
    words = {part for name in PEOPLE for part in (name, *name.split())}
    return sorted(word for word in words if re.search(rf"\b{re.escape(word)}\b", text))


def _seed(client: TestClient) -> None:
    """Checkout with Payments Pod (Ira runs it, Liam in it) and Storefront Pod (Zoe)."""
    for path, body in (
        ("/config/projects", {"id": "checkout", "name": "Checkout"}),
        ("/config/pods", {"id": "pod-pay", "name": "Payments Pod"}),
        ("/config/pods", {"id": "pod-web", "name": "Storefront Pod"}),
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
    registry = client.app.state.registry  # type: ignore[attr-defined]
    graph = registry.graph_repository()
    _, _, questions, _ = registry.gate_repositories()

    async def add() -> None:
        await graph.upsert_node(
            Task(
                tenant_id=T,
                id="CHK-1",
                name="Checkout API",
                metadata={"key": "CHK-1", "status": "To Do", "state": "todo"},
            )
        )
        await graph.add_edge(
            GraphEdge(
                tenant_id=T, from_node_id="pod-pay", to_node_id="CHK-1", kind=EdgeKind.CONTAINS
            )
        )
        await questions.save(
            TrackedQuestion(
                tenant_id=T,
                question_id="q-1",
                issue_key="CHK-1",
                comment_ref="",
                asked_by="U1005",
                asked_by_name="Zoe Almeida",
                asked_to="U1002",
                asked_to_name="Liam Chen",
                asked_at=datetime(2026, 10, 6, 9, 0, tzinfo=UTC),
                summary="Liam Chen, can the API take a retry key?",
                status=QuestionStatus.NOT_YET,
                confirmed=True,
            )
        )
        await registry.narrative_brief_repository().record_brief(
            NarrativeBrief(
                tenant_id=T,
                kind=BriefKind.DAILY_POD,
                scope_id="pod-pay",
                title="Daily pod summary: Payments Pod",
                body=(
                    "Liam Chen is blocked on CHK-1.\n- Ira Novak asks Asha Rao to decide by Friday."
                ),
                generated_at=datetime(2026, 10, 7, 8, 0, tzinfo=UTC),
                sources=("pod:pod-pay", "developer:U1002", "work_item:CHK-1"),
            )
        )

    client.portal.call(add)  # type: ignore[union-attr]
    committed = client.put(
        "/projects/checkout/delivery-date",
        json={"target_date": "2026-12-15", "note": "Agreed with Asha Rao"},
        headers=_as("po"),
    )
    assert committed.status_code == 200, committed.text
    marked = client.post("/projects/checkout/baselines", json={}, headers=_as("po"))
    assert marked.status_code == 201, marked.text


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    return TestClient(app)


READS = (
    "/projects/checkout/delivery",
    "/projects/checkout/baselines",
    "/projects/checkout/gates",
    "/projects/checkout/delivery/impact",
    "/persona/briefs",
)


def test_every_role_but_the_executive_reads_the_names(client: TestClient) -> None:
    with client:
        _seed(client)
        bodies = {
            role: {path: client.get(path, headers=_as(role)) for path in READS}
            for role in ("mgr", "po", "admin")
        }
        sm = client.get("/projects/checkout/delivery", headers=_as("sm"))

    for role, reads in bodies.items():
        for path, response in reads.items():
            assert response.status_code == 200, (role, path, response.text)
        delivery = reads["/projects/checkout/delivery"].json()
        assert delivery["project"]["commitment"]["changes"][-1]["changed_by_name"] == "Mina Patel"
        assert any("by Mina Patel" in reason for reason in delivery["project"]["reasons"])
        question = reads["/projects/checkout/gates"].json()["questions"][0]
        assert (question["asked_by_name"], question["asked_to_name"]) == (
            "Zoe Almeida",
            "Liam Chen",
        )
        assert "Liam Chen" in reads["/persona/briefs"].json()["briefs"][0]["body"], role
    assert sm.status_code == 200, sm.text
    assert "Mina Patel" in _named(sm.json())


def test_the_executive_reads_the_role_or_the_team_and_no_name(client: TestClient) -> None:
    with client:
        _seed(client)
        reads = {path: client.get(path, headers=_as("exec")) for path in READS}

    for path, response in reads.items():
        assert response.status_code == 200, (path, response.text)
        assert _named(response.json()) == [], path

    delivery = reads["/projects/checkout/delivery"].json()["project"]
    change = delivery["commitment"]["changes"][-1]
    # The committed date's "set by" is the role (D98), and so is its id.
    assert (change["changed_by"], change["changed_by_name"]) == (
        "the product owner",
        "the product owner",
    )
    assert change["note"] == "Agreed with the manager"
    assert "Committed for Tue 15 Dec 2026 by the product owner." in delivery["reasons"]

    baseline = reads["/projects/checkout/baselines"].json()["active"]
    assert baseline["set_by_name"] == "the product owner"

    # A question's "asked by" and "asked of" are the owning teams; its words too.
    question = reads["/projects/checkout/gates"].json()["questions"][0]
    assert (question["asked_by_name"], question["asked_to_name"]) == (
        "Storefront Pod",
        "Payments Pod",
    )
    assert question["summary"] == "Payments Pod, can the API take a retry key?"

    impact = reads["/projects/checkout/delivery/impact"].json()
    assert [change["by"] for change in impact["committed_changes"]] == ["the product owner"]

    # A pod's brief reads with roles and teams, and lists no person among its sources.
    brief = reads["/persona/briefs"].json()["briefs"][0]
    assert brief["body"] == (
        "Payments Pod is blocked on CHK-1.\n- The scrum master asks the manager to decide "
        "by Friday."
    )
    assert brief["sources"] == ["pod:pod-pay", "work_item:CHK-1"]


def test_a_requirement_and_a_risk_name_nobody_to_an_executive() -> None:
    words = people_words(
        [
            Developer(tenant_id=T, id="U1002", name="Liam Chen"),
            Developer(tenant_id=T, id="U1003", name="Mina Patel", metadata={"app_roles": "po"}),
            Pod(tenant_id=T, id="pod-pay", name="Payments Pod"),
        ],
        [
            GraphEdge(
                tenant_id=T, from_node_id="pod-pay", to_node_id="U1002", kind=EdgeKind.CONTAINS
            )
        ],
    )
    requirement = RequirementResponse.model_construct(
        key="CHK-1", title="Checkout API for Mina Patel", assignee_name="Liam Chen"
    )
    risk = RiskFindingResponse(
        rule_id="pr_age",
        severity=Rag.AMBER,
        entity_ref=EntityRefDto(tenant_id=T, kind=NodeKind.DEVELOPER, id="U1002"),
        workstream_id=None,
        reason="Liam Chen's merge request has waited 6 days for review.",
        evidence=RiskEvidenceDto(identifier="web#7", url=None, url_is_user_supplied=False),
        age_days=6,
        detected_at=datetime(2026, 10, 7, tzinfo=UTC),
        status="open",
        owner_id="U1002",
        owner_status_summary="Liam: waiting on review, back Monday",
        owner_status_source=None,
        owner_status_as_of=date(2026, 10, 7),
        owner_status_has_blockers=False,
        is_watermelon=False,
        person_name="Liam Chen",
    )

    seen_requirement = for_reader(requirement, words)
    seen_risk = for_reader(risk, words)

    assert seen_requirement.assignee_name == "Payments Pod"
    assert seen_requirement.title == "Checkout API for the product owner"
    assert (seen_risk.owner_id, seen_risk.person_name) == ("Payments Pod", "Payments Pod")
    assert seen_risk.reason == "Payments Pod's merge request has waited 6 days for review."
    # A person's own status is not an executive's to read (D87).
    assert (seen_risk.owner_status_summary, seen_risk.owner_status_as_of) == (None, None)
    # The entity is a work reference, kept; anyone who reads names gets it as built.
    assert seen_risk.entity_ref.id == "U1002"
    assert for_reader(risk, None) is risk
