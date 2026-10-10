"""The release readiness routes: who may read and act, and every refusal in its sentence."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.graph import Developer, EdgeKind, GraphEdge, Pod, Task

TENANT = "demo"


def _as(role: str, user: str = "someone") -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


CRITERION = {
    "name": "Security review",
    "evidence": "A security review of the release's changes.",
    "applies_to": "release",
    "matchers": [
        {"kind": "label", "value": "security-review", "strength": "evidence"},
        {"kind": "title_phrase", "value": "security review", "strength": "evidence"},
    ],
    "draft": {"labels": ["security-review"]},
}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(
        settings=settings.model_copy(
            update={"demo_mode": True, "issue_tracker_provider": "fake", "tenant_id": TENANT}
        )
    )
    with TestClient(app) as test_client:
        graph = app.state.registry.graph_repository()

        async def seed() -> None:
            for node in (
                Developer(tenant_id=TENANT, id="U-SM", name="Ira Novak"),
                Developer(tenant_id=TENANT, id="U-PO", name="Mina Patel"),
                Pod(
                    tenant_id=TENANT,
                    id="pod-pay",
                    name="Payments Pod",
                    metadata={"escalation_sm_member_id": "U-SM"},
                ),
                Task(
                    tenant_id=TENANT,
                    id="CHK-9",
                    name="Capture retry runbook",
                    metadata={"key": "CHK-9", "status": "In Progress", "state": "in_progress"},
                ),
            ):
                await graph.upsert_node(node)
            for parent, child in (("checkout", "pod-pay"), ("checkout", "CHK-9")):
                await graph.add_edge(
                    GraphEdge(
                        tenant_id=TENANT,
                        from_node_id=parent,
                        to_node_id=child,
                        kind=EdgeKind.CONTAINS,
                    )
                )

        project = test_client.post(
            "/config/projects",
            json={"id": "checkout", "name": "Checkout Revamp", "jira_project_key": "CHK"},
        )
        assert project.status_code == 201, project.text
        asyncio.run(seed())
        yield test_client


def _set_up(client: TestClient, **settings: object) -> dict[str, object]:
    body = {
        "enabled": True,
        "auto_suggest": True,
        "create_in_jira": False,
        "issue_type": "Task",
        "labels": ["release-readiness"],
        **settings,
    }
    assert client.put("/config/readiness/settings", json=body).status_code == 200
    saved = client.put("/config/readiness/criteria", json=CRITERION)
    assert saved.status_code == 200, saved.text
    return saved.json()


def _security(board: dict[str, object]) -> dict[str, object]:
    return next(
        item
        for item in board["findings"]  # type: ignore[attr-defined]
        if item["criterion"]["name"] == "Security review"
    )


def test_config_is_admin_only_and_offers_the_examples(client: TestClient) -> None:
    config = client.get("/config/readiness")
    refused = client.get("/config/readiness", headers=_as("mgr"))
    bad = client.put(
        "/config/readiness/criteria",
        json={
            **CRITERION,
            "matchers": [{"kind": "label", "value": "two words", "strength": "evidence"}],
        },
    )
    unknown = client.put("/config/readiness/criteria", json={**CRITERION, "criterion_id": "nope"})

    assert config.status_code == 200
    body = config.json()
    assert body["is_default"] is True
    assert body["settings"]["enabled"] is False
    assert len(body["examples"]) == 6
    assert body["waive_roles"] == ["mgr"]
    assert refused.status_code == 403
    assert bad.status_code == 422
    assert "has a space" in bad.json()["detail"]
    assert unknown.status_code == 404


def test_the_board_by_role(client: TestClient) -> None:
    _set_up(client)
    run = client.post("/projects/checkout/readiness/run", headers=_as("po", "U-PO"))
    reads = {
        role: client.get("/projects/checkout/readiness", headers=_as(role, user))
        for role, user in (
            ("po", "U-PO"),
            ("mgr", "U-MGR"),
            ("exec", "U-EX"),
            ("dev", "U-DEV"),
            ("sm", "U-SM"),
        )
    }
    pod = client.get("/pods/pod-pay/readiness", headers=_as("sm", "U-SM"))
    pod_po = client.get("/pods/pod-pay/readiness", headers=_as("po", "U-PO"))
    missing = client.get("/projects/nope/readiness", headers=_as("mgr"))

    assert run.status_code == 200, run.text
    assert run.json()["run"]["status"] == "ok"
    security = _security(run.json()["board"])
    assert security["state"] == "missing"
    assert security["suggestion"]["draft"]["project_key"] == "CHK"
    assert security["can"]["create"] is False
    assert security["can"]["create_off_reason"] == (
        "Creating issues from OpenProgram is off for this tenant."
    )
    assert security["can"]["not_applicable"] is False  # blocking: a manager or admin only
    assert reads["po"].status_code == 200
    assert _security(reads["mgr"].json())["can"]["not_applicable"] is True
    executive = _security(reads["exec"].json())
    assert executive["suggestion"] is None and executive["can"]["link"] is False
    assert reads["dev"].status_code == 403
    assert reads["sm"].status_code == 200 and reads["sm"].json()["can_run"] is True
    assert reads["sm"].json()["agent"]["enabled"] is True
    assert {item["scope"]["kind"] for item in reads["sm"].json()["findings"]} == {"project"}
    assert pod.status_code == 200
    assert pod_po.status_code == 403
    assert missing.status_code == 404


def test_a_scrum_master_outside_the_project_is_refused_in_words(client: TestClient) -> None:
    _set_up(client)

    read = client.get("/projects/checkout/readiness", headers=_as("sm", "U-OTHER"))
    run = client.post("/projects/checkout/readiness/run", headers=_as("sm", "U-OTHER"))

    assert read.status_code == 403
    # A read says what is read, as every scoped project read does; an action what is done.
    assert read.json()["detail"] == (
        "You read the projects your own pods work on, and this is not one of them."
    )
    assert run.status_code == 403
    assert run.json()["detail"] == (
        "You can act on readiness only for pods you run and the projects they work on."
    )


def test_run_now_is_refused_while_the_agent_is_off(client: TestClient) -> None:
    _set_up(client, enabled=False)

    refused = client.post("/projects/checkout/readiness/run", headers=_as("mgr"))

    assert refused.status_code == 409
    assert refused.json()["detail"] == "The readiness agent is off for this tenant."


def test_actions_and_their_refusals(client: TestClient) -> None:
    _set_up(client)
    board = client.post("/projects/checkout/readiness/run", headers=_as("mgr")).json()["board"]
    security = _security(board)
    finding = security["finding_id"]
    suggestion = security["suggestion"]["suggestion_id"]

    waive_po = client.post(
        f"/readiness-findings/{finding}/not-applicable",
        json={"reason": "Internal API only"},
        headers=_as("po", "U-PO"),
    )
    short = client.post(
        f"/readiness-findings/{finding}/not-applicable",
        json={"reason": "no"},
        headers=_as("mgr"),
    )
    both = client.post(
        f"/readiness-findings/{finding}/link",
        json={"issue_key": "CHK-9", "evidence_url": "https://x.example"},
        headers=_as("po"),
    )
    unknown_key = client.post(
        f"/readiness-findings/{finding}/link", json={"issue_key": "CHK-404"}, headers=_as("po")
    )
    not_https = client.post(
        f"/readiness-findings/{finding}/link",
        json={"evidence_url": "http://records.example/1"},
        headers=_as("po"),
    )
    dev = client.post(
        f"/readiness-findings/{finding}/link", json={"issue_key": "CHK-9"}, headers=_as("dev")
    )
    stale = client.put(
        f"/readiness-suggestions/{suggestion}",
        json={"version": 9, "draft": security["suggestion"]["draft"]},
        headers=_as("po"),
    )
    off = client.post(
        f"/readiness-suggestions/{suggestion}/create", json={"version": 1}, headers=_as("po")
    )
    dismissed = client.post(
        f"/readiness-suggestions/{suggestion}/dismiss",
        json={"reason": "Done elsewhere"},
        headers=_as("po", "U-PO"),
    )
    edit_dismissed = client.put(
        f"/readiness-suggestions/{suggestion}",
        json={"version": 1, "draft": security["suggestion"]["draft"]},
        headers=_as("po"),
    )
    reopened = client.post(f"/readiness-findings/{finding}/reopen", headers=_as("po"))
    waived = client.post(
        f"/readiness-findings/{finding}/not-applicable",
        json={"reason": "Internal API only"},
        headers=_as("mgr", "U-MGR"),
    )
    history = client.get(f"/readiness-findings/{finding}/history", headers=_as("po"))
    no_such = client.post("/readiness-findings/rf_nope/reopen", headers=_as("po"))

    assert waive_po.status_code == 403
    assert waive_po.json()["detail"] == (
        "Only a manager or an admin marks a blocking criterion not applicable."
    )
    assert short.status_code == 422
    assert short.json()["detail"] == "Give a reason of 3 to 300 characters."
    assert both.status_code == 422
    assert unknown_key.status_code == 422
    assert unknown_key.json()["detail"] == "CHK-404 is not among Jira's synced issues."
    assert not_https.status_code == 422
    assert dev.status_code == 403
    assert stale.status_code == 409
    assert stale.json()["detail"] == "The draft changed since you opened it; check it again."
    assert off.status_code == 409
    assert dismissed.status_code == 200
    assert dismissed.json()["suggestion"]["dismissed"]["by_name"] == "Mina Patel"
    assert edit_dismissed.status_code == 409
    assert edit_dismissed.json()["detail"] == "This draft was dismissed; reopen it first."
    assert reopened.json()["suggestion"]["status"] == "open"
    assert waived.json()["state"] == "not_applicable"
    actions = [entry["action"] for entry in history.json()["entries"]]
    assert {"drafted", "dismissed", "reopened", "not_applicable"} <= set(actions)
    assert no_such.status_code == 404


def test_create_in_jira_through_both_switches(client: TestClient) -> None:
    _set_up(client, create_in_jira=True)
    board = client.post("/projects/checkout/readiness/run", headers=_as("mgr")).json()["board"]
    security = _security(board)
    suggestion = security["suggestion"]["suggestion_id"]
    assert security["can"]["create_off_reason"] == (
        "Creating issues from OpenProgram needs Jira write-back on."
    )

    assert client.put("/config/tenant/writeback", json={"enabled": True}).status_code == 200
    created = client.post(
        f"/readiness-suggestions/{suggestion}/create", json={"version": 1}, headers=_as("po")
    )
    again = client.post(
        f"/readiness-suggestions/{suggestion}/create", json={"version": 1}, headers=_as("po")
    )

    assert created.status_code == 201, created.text
    assert created.json()["finding"]["state"] == "covered"
    assert again.status_code == 200
    assert again.json()["issue_key"] == created.json()["issue_key"]


def test_preview_saves_nothing(client: TestClient) -> None:
    runbook = {
        **CRITERION,
        "name": "Runbook",
        "applies_to": "project",
        "matchers": [{"kind": "title_phrase", "value": "runbook", "strength": "evidence"}],
    }

    preview = client.post(
        "/config/readiness/criteria/preview", json={"project_id": "checkout", "criterion": runbook}
    )
    config = client.get("/config/readiness").json()

    assert preview.status_code == 200, preview.text
    [row] = preview.json()["rows"]
    assert (row["scope"]["name"], row["state"]) == ("Checkout Revamp", "covered")
    assert row["evidence"][0]["issue_key"] == "CHK-9"
    assert config["criteria"] == []
