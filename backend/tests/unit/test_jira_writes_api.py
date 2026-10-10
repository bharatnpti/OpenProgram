"""Admin › Jira writes over HTTP: who may, what comes back, and readiness's create behind it."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.graph import Developer

TENANT = "demo"
URL = "/config/tenant/jira-writes"


def _as(role: str, user: str = "someone") -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


CRITERION = {
    "name": "Security review",
    "evidence": "A security review of the release's changes.",
    "applies_to": "release",
    "matchers": [{"kind": "label", "value": "security-review", "strength": "evidence"}],
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
        asyncio.run(graph.upsert_node(Developer(tenant_id=TENANT, id="U1001", name="Asha Rao")))
        project = test_client.post(
            "/config/projects",
            json={"id": "checkout", "name": "Checkout Revamp", "jira_project_key": "CHK"},
        )
        assert project.status_code == 201, project.text
        yield test_client


def _kinds(body: dict[str, object]) -> dict[str, tuple[bool, str, bool]]:
    return {
        item["kind"]: (item["on"], item["source"], item["effective"])
        for item in body["kinds"]  # type: ignore[attr-defined]
    }


def test_nothing_set_reads_the_defaults_and_where_they_come_from(client: TestClient) -> None:
    body = client.get(URL).json()

    assert body["master"] == {"on": False, "source": "default"}
    assert _kinds(body) == {
        "checkin_updates": (True, "default", False),
        "console_moves": (True, "default", False),
        "readiness_create": (False, "default", False),
    }
    assert body["create_projects"] == {"own_project": True, "projects": [], "source": "default"}
    assert body["changes"] == []


def test_the_environment_is_named_as_the_master_switch_source(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"jira_writeback_enabled": True}))
    with TestClient(app) as client:
        body = client.get(URL).json()

    assert body["master"] == {"on": True, "source": "env"}
    assert _kinds(body)["checkin_updates"] == (True, "default", True)


def test_a_put_changes_only_what_it_gives_and_each_change_is_listed(client: TestClient) -> None:
    on = client.put(
        URL, json={"master": True, "readiness_create": False}, headers=_as("admin", "U1001")
    )
    projects = client.put(
        URL,
        json={"create_projects": {"own_project": True, "projects": ["sec", "SEC"]}},
        headers=_as("admin", "U1001"),
    )
    off = client.put(URL, json={"checkin_updates": False}, headers=_as("admin", "U1001"))

    assert on.status_code == 200, on.text
    assert on.json()["master"] == {"on": True, "source": "admin"}
    assert _kinds(on.json())["checkin_updates"] == (True, "default", True)
    assert _kinds(on.json())["readiness_create"] == (False, "admin", False)
    assert projects.json()["create_projects"] == {
        "own_project": True,
        "projects": ["SEC"],
        "source": "admin",
    }
    body = off.json()
    assert _kinds(body)["checkin_updates"] == (False, "admin", False)
    assert _kinds(body)["console_moves"] == (True, "default", True)
    newest = body["changes"][0]
    assert newest["setting"] == "checkin_updates"
    assert (newest["before_on"], newest["after_on"], newest["before_source"]) == (
        True,
        False,
        "default",
    )
    assert (newest["by"], newest["by_name"]) == ("U1001", "Asha Rao")
    assert [change["setting"] for change in body["changes"]] == [
        "checkin_updates",
        "create_projects",
        "readiness_create",
        "master",
    ]
    assert body["changes"][1]["after_projects"] == {"own_project": True, "projects": ["SEC"]}


def test_the_old_master_route_is_the_same_switch_and_is_audited(client: TestClient) -> None:
    put = client.put("/config/tenant/writeback", json={"enabled": True})
    old = client.get("/config/tenant/writeback")
    body = client.get(URL).json()

    assert put.json() == {"enabled": True, "source": "tenant"}
    assert old.json() == {"enabled": True, "source": "tenant"}
    assert body["master"] == {"on": True, "source": "admin"}
    assert [(c["setting"], c["before_on"], c["after_on"]) for c in body["changes"]] == [
        ("master", False, True)
    ]


@pytest.mark.parametrize("role", ["mgr", "po", "sm", "dev", "exec"])
def test_only_an_admin_reads_or_changes_it(client: TestClient, role: str) -> None:
    read = client.get(URL, headers=_as(role))
    write = client.put(URL, json={"master": True}, headers=_as(role))

    assert read.status_code == 403
    assert write.status_code == 403
    assert client.get(URL).json()["master"]["on"] is False


@pytest.mark.parametrize(
    ("body", "words"),
    [
        ({"gate_signoff": True}, None),
        ({"checkin_updates": "sometimes"}, None),
        ({"create_projects": {"own_project": True, "projects": ["SE C"]}}, "SE C"),
        ({"create_projects": {"own_project": False, "projects": []}}, "Allow at least one"),
    ],
)
def test_a_bad_key_or_project_is_a_422(
    client: TestClient, body: dict[str, object], words: str | None
) -> None:
    refused = client.put(URL, json=body)

    assert refused.status_code == 422
    if words is not None:
        assert words in refused.json()["detail"]
    assert client.get(URL).json()["changes"] == []


# ---- release readiness's create, behind its own switch --------------------------------------


def _draft(client: TestClient, **draft: object) -> tuple[dict[str, object], str]:
    settings = {
        "enabled": True,
        "auto_suggest": True,
        "issue_type": "Task",
        "labels": ["release-readiness"],
    }
    assert client.put("/config/readiness/settings", json=settings).status_code == 200
    criterion = {**CRITERION, "draft": {**CRITERION["draft"], **draft}}  # type: ignore[dict-item]
    assert client.put("/config/readiness/criteria", json=criterion).status_code == 200
    board = client.post("/projects/checkout/readiness/run", headers=_as("mgr")).json()["board"]
    security = next(
        item for item in board["findings"] if item["criterion"]["name"] == "Security review"
    )
    return security, security["suggestion"]["suggestion_id"]


def _create(client: TestClient, suggestion: str) -> object:
    return client.post(
        f"/readiness-suggestions/{suggestion}/create", json={"version": 1}, headers=_as("po")
    )


def test_create_is_refused_while_its_switch_is_off_even_with_the_master_on(
    client: TestClient,
) -> None:
    assert client.put(URL, json={"master": True}).status_code == 200
    security, suggestion = _draft(client)

    refused = _create(client, suggestion)
    assert client.put(URL, json={"readiness_create": True}).status_code == 200
    created = _create(client, suggestion)

    assert security["can"]["create"] is False
    assert security["can"]["create_off_reason"] == (
        "Creating release-readiness issues is off for this tenant."
    )
    assert refused.status_code == 409  # type: ignore[attr-defined]
    assert refused.json()["detail"] == (  # type: ignore[attr-defined]
        "Creating release-readiness issues is off for this tenant."
    )
    assert created.status_code == 201, created.text  # type: ignore[attr-defined]


def test_create_into_a_project_not_allowed_is_a_422_until_an_admin_allows_it(
    client: TestClient,
) -> None:
    client.put(URL, json={"master": True, "readiness_create": True})
    security, suggestion = _draft(client, project_key="SEC")

    refused = _create(client, suggestion)
    client.put(URL, json={"create_projects": {"own_project": True, "projects": ["SEC"]}})
    board = client.get("/projects/checkout/readiness", headers=_as("mgr")).json()
    created = _create(client, suggestion)

    sentence = (
        "This draft is for Jira project SEC, where OpenProgram may not create issues. "
        "It may create them in CHK."
    )
    assert security["suggestion"]["draft"]["project_key"] == "SEC"
    assert security["can"]["create_off_reason"] == sentence
    assert refused.status_code == 422  # type: ignore[attr-defined]
    assert refused.json()["detail"] == sentence  # type: ignore[attr-defined]
    assert board["findings"][0]["can"]["create"] is True
    assert created.status_code == 201, created.text  # type: ignore[attr-defined]


def test_the_readiness_settings_switch_is_the_same_one_and_left_out_keeps_it(
    client: TestClient,
) -> None:
    base = {"enabled": True, "auto_suggest": True, "issue_type": "Task", "labels": []}

    turned_on = client.put("/config/readiness/settings", json={**base, "create_in_jira": True})
    kept = client.put("/config/readiness/settings", json=base)
    client.put(URL, json={"readiness_create": False})
    config = client.get("/config/readiness").json()
    body = client.get(URL).json()

    assert turned_on.json()["create_in_jira"] is True
    assert kept.json()["create_in_jira"] is True
    assert config["settings"]["create_in_jira"] is False
    assert [(c["setting"], c["after_on"]) for c in body["changes"]] == [
        ("readiness_create", False),
        ("readiness_create", True),
    ]
