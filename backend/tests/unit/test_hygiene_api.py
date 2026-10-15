"""The Jira hygiene routes: who reads and acts, an executive's counts, every refusal in words."""

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


NO_POINTS = {
    "name": "Story without an estimate",
    "condition": {"kind": "missing_field", "field": "story_points"},
    "owner": {"kind": "assignee"},
    "suggested_fix": "Estimate {key} before it starts.",
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
                Developer(tenant_id=TENANT, id="U-DEV", name="Kai Lund"),
                Pod(
                    tenant_id=TENANT,
                    id="pod-pay",
                    name="Payments Pod",
                    metadata={
                        "escalation_sm_chat_external_id": "C-SM",
                        "escalation_sm_display_name": "Ira Novak",
                        "escalation_sm_member_id": "U-SM",
                    },
                ),
                Task(
                    tenant_id=TENANT,
                    id="CHK-9",
                    name="Capture retry",
                    metadata={
                        "key": "CHK-9",
                        "status": "In Progress",
                        "state": "in_progress",
                        "issue_type": "Story",
                    },
                ),
                Task(
                    tenant_id=TENANT,
                    id="CHK-10",
                    name="Card art",
                    metadata={
                        "key": "CHK-10",
                        "status": "To Do",
                        "state": "todo",
                        "issue_type": "Story",
                        "story_points": 3,
                    },
                ),
            ):
                await graph.upsert_node(node)
            for parent, child, kind in (
                ("checkout", "pod-pay", EdgeKind.CONTAINS),
                ("checkout", "CHK-9", EdgeKind.CONTAINS),
                ("checkout", "CHK-10", EdgeKind.CONTAINS),
                ("pod-pay", "U-DEV", EdgeKind.CONTAINS),
                ("U-DEV", "CHK-9", EdgeKind.ASSIGNED_TO),
            ):
                await graph.add_edge(
                    GraphEdge(tenant_id=TENANT, from_node_id=parent, to_node_id=child, kind=kind)
                )

        project = test_client.post(
            "/config/projects",
            json={"id": "checkout", "name": "Checkout Revamp", "jira_project_key": "CHK"},
        )
        assert project.status_code == 201, project.text
        asyncio.run(seed())
        yield test_client


def _set_up(client: TestClient, *, enabled: bool = True, **rule: object) -> dict[str, object]:
    settings = client.put(
        "/config/hygiene/settings",
        json={"enabled": enabled, "change_labels": ["CR"], "stale_working_days": 5},
    )
    assert settings.status_code == 200, settings.text
    saved = client.put("/config/hygiene/rules", json={**NO_POINTS, **rule})
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_config_is_admin_only_and_offers_examples_that_list_only(client: TestClient) -> None:
    config = client.get("/config/hygiene")
    refused = client.get("/config/hygiene", headers=_as("mgr"))
    bad = client.put(
        "/config/hygiene/rules",
        json={**NO_POINTS, "suggested_fix": "Ask {customer} about {key}"},
    )
    unknown = client.put("/config/hygiene/rules", json={**NO_POINTS, "rule_id": "nope"})

    assert config.status_code == 200
    body = config.json()
    assert body["is_default"] is True
    assert body["settings"]["enabled"] is False
    assert body["settings"]["change_labels"] == ["CR"]
    assert len(body["examples"]) == 8
    assert {item["severity"] for item in body["examples"]} == {"list_only"}
    assert "key" in body["placeholders"]
    assert refused.status_code == 403
    assert bad.status_code == 422
    assert "{customer}" in bad.json()["detail"]
    assert unknown.status_code == 404


def test_an_example_is_added_by_its_id_and_removed(client: TestClient) -> None:
    example = next(
        item
        for item in client.get("/config/hygiene").json()["examples"]
        if item["rule_id"] == "added-after-planning-without-change-label"
    )

    added = client.put("/config/hygiene/rules", json=example)
    again = client.get("/config/hygiene").json()
    removed = client.delete(f"/config/hygiene/rules/{example['rule_id']}")

    assert added.status_code == 200, added.text
    assert added.json()["version"] == 1
    assert [item["rule_id"] for item in again["rules"]] == [example["rule_id"]]
    assert example["rule_id"] not in {item["rule_id"] for item in again["examples"]}
    assert removed.status_code == 204
    assert client.get("/config/hygiene").json()["rules"] == []


def test_the_board_by_role(client: TestClient) -> None:
    _set_up(client)
    run = client.post("/projects/checkout/hygiene/run", headers=_as("po", "U-PO"))
    reads = {
        role: client.get("/projects/checkout/hygiene", headers=_as(role, user))
        for role, user in (
            ("po", "U-PO"),
            ("mgr", "U-MGR"),
            ("exec", "U-EX"),
            ("dev", "U-DEV"),
        )
    }
    pod = client.get("/pods/pod-pay/hygiene", headers=_as("mgr"))
    pod_po = client.get("/pods/pod-pay/hygiene", headers=_as("po", "U-PO"))
    missing = client.get("/projects/nope/hygiene", headers=_as("mgr"))

    assert run.status_code == 200, run.text
    assert run.json()["run"]["opened"] == 1
    board = reads["po"].json()
    [finding] = board["findings"]
    assert finding["issue_key"] == "CHK-9"
    assert finding["title"] == "Capture retry"
    assert finding["what"] == "No story points"
    assert finding["owner"] == {"name": "Kai Lund", "via": "assignee", "profile_label": None}
    assert finding["suggested_fix"] == "Estimate CHK-9 before it starts."
    assert finding["can"]["waive"] is True
    assert board["rules"][0]["open"] == 1
    assert board["rules"][0]["stated"] == ("Requirements without story points; lists them.")
    executive = reads["exec"].json()
    assert executive["counts_only"] is True
    assert executive["findings"] == []
    assert executive["rules"][0]["open"] == 1
    assert executive["can_run"] is False
    assert reads["dev"].status_code == 403
    assert pod.status_code == 200
    assert pod_po.status_code == 403
    assert missing.status_code == 404


def test_check_now_is_refused_while_the_scan_is_off_and_for_an_executive(
    client: TestClient,
) -> None:
    _set_up(client, enabled=False)
    off = client.post("/projects/checkout/hygiene/run", headers=_as("mgr"))
    _set_up(client)
    executive = client.post("/projects/checkout/hygiene/run", headers=_as("exec", "U-EX"))

    assert off.status_code == 409
    assert off.json()["detail"] == "The Jira hygiene scan is off for this tenant."
    assert executive.status_code == 403


def test_actions_and_their_refusals(client: TestClient) -> None:
    _set_up(client, blocking_before="in_testing")
    board = client.post("/projects/checkout/hygiene/run", headers=_as("mgr")).json()["board"]
    finding = board["findings"][0]["finding_id"]

    short = client.post(
        f"/hygiene-findings/{finding}/waive", json={"reason": "no"}, headers=_as("po")
    )
    na_po = client.post(
        f"/hygiene-findings/{finding}/not-applicable",
        json={"reason": "Spike work"},
        headers=_as("po", "U-PO"),
    )
    dev = client.post(
        f"/hygiene-findings/{finding}/waive", json={"reason": "Spike work"}, headers=_as("dev")
    )
    waived = client.post(
        f"/hygiene-findings/{finding}/waive",
        json={"reason": "Spike work"},
        headers=_as("po", "U-PO"),
    )
    twice = client.post(
        f"/hygiene-findings/{finding}/waive",
        json={"reason": "Spike work"},
        headers=_as("po", "U-PO"),
    )
    reopened = client.post(f"/hygiene-findings/{finding}/reopen", headers=_as("mgr", "U-MGR"))
    rechecked = client.post(f"/hygiene-findings/{finding}/recheck", headers=_as("mgr", "U-MGR"))
    history = client.get(f"/hygiene-findings/{finding}/history", headers=_as("po"))
    exec_history = client.get(f"/hygiene-findings/{finding}/history", headers=_as("exec"))
    no_such = client.post("/hygiene-findings/hf_nope/reopen", headers=_as("po"))

    assert short.status_code == 422
    assert na_po.status_code == 403
    assert na_po.json()["detail"] == (
        "Only a manager or an admin marks a finding of a blocking rule not applicable."
    )
    assert dev.status_code == 403
    assert waived.status_code == 200, waived.text
    assert waived.json()["state"] == "waived"
    assert waived.json()["decision"]["reason"] == "Spike work"
    assert waived.json()["can"]["reopen"] is True
    assert twice.status_code == 409
    assert reopened.json()["state"] == "open"
    assert rechecked.json()["state"] == "open"
    actions = [entry["action"] for entry in history.json()["entries"]]
    assert actions[:4] == ["opened", "waived", "reopened", "changed"]
    assert exec_history.status_code == 403
    assert no_such.status_code == 404


def test_try_on_a_project_saves_nothing(client: TestClient) -> None:
    preview = client.post(
        "/config/hygiene/rules/preview", json={"project_id": "checkout", "rule": NO_POINTS}
    )
    refused = client.post(
        "/config/hygiene/rules/preview",
        json={"project_id": "checkout", "rule": NO_POINTS},
        headers=_as("mgr"),
    )
    config = client.get("/config/hygiene").json()

    assert preview.status_code == 200, preview.text
    project = next(row for row in preview.json()["scopes"] if row["scope"]["kind"] == "project")
    assert (project["looked_at"], project["found"]) == (2, 1)
    assert project["issues"][0]["key"] == "CHK-9"
    assert project["issues"][0]["owner_name"] == "Kai Lund"
    assert refused.status_code == 403
    assert config["rules"] == []
