"""``POST /me/tasks/{task_id}/update`` and the extended ``GET /me/focus``.

The route answers the task row, the day's status and the tracker outcome; a
task not in the caller's tree is a 404; a broken rule is a 422 in plain words.
"""

from __future__ import annotations

import asyncio
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.status_summaries import TASK_UPDATE_LEAD
from core.application.task_update_service import TASK_NOT_ASSIGNED
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.unit.test_task_update import _DEV, _team


def _app(settings: Settings) -> FastAPI:
    return create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "dev", "dev_principal_subject": _DEV}
        )
    )


def _seeded(app: FastAPI) -> InMemoryGraphStore:
    store: InMemoryGraphStore = app.state.registry.graph_repository()
    asyncio.run(_team(store))
    return store


def test_the_update_route_answers_the_task_the_status_and_the_tracker(
    settings: Settings,
) -> None:
    app = _app(settings)
    with TestClient(app) as client:
        _seeded(app)
        today = date.today().isoformat()
        response = client.post(
            "/me/tasks/CHK-4/update",
            json={
                "state": "in_review",
                "eta": today,
                "note": "  MR !3 open  ",
                "add_blocker": "Waiting on sandbox credentials",
                "move_in_tracker": False,
            },
        )
        focus = client.get("/me/focus")
        status = client.get("/me/status")
        cleared = client.post("/me/tasks/CHK-4/update", json={"eta": None})

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"task", "status", "tracker"}
    assert body["tracker"] is None
    task = body["task"]
    assert task["id"] == "CHK-4"
    assert task["my_eta"] == today
    assert task["last_update"]["state"] == "in_review"
    assert task["last_update"]["note"] == "MR !3 open"
    assert task["last_update"]["via"] == "console"
    assert task["tracker_status"] == "In Progress"
    assert len(task["blocker_ids"]) == 1
    assert body["status"]["source"] == "partial"
    assert body["status"]["summary"].startswith(TASK_UPDATE_LEAD)
    assert [item["blocker_id"] for item in body["status"]["blocker_details"]] == task["blocker_ids"]
    assert focus.status_code == 200
    assert focus.json()["write_back"] == "off"
    focus_task = next(item for item in focus.json()["tasks"] if item["id"] == "CHK-4")
    assert focus_task == task
    assert status.json()["source"] == "partial"
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["task"]["my_eta"] is None


def test_the_update_route_refuses_with_plain_words(settings: Settings) -> None:
    app = _app(settings)
    with TestClient(app) as client:
        _seeded(app)
        not_mine = client.post("/me/tasks/CHK-9/update", json={"state": "done"})
        empty = client.post("/me/tasks/CHK-4/update", json={})
        past = client.post("/me/tasks/CHK-4/update?as_of=2026-01-01", json={"state": "in_progress"})
        eta_past = client.post("/me/tasks/CHK-4/update", json={"eta": "2026-01-01"})
        blocked = client.post("/me/tasks/task-seed/update", json={"state": "blocked"})
        unknown_field = client.post("/me/tasks/CHK-4/update", json={"eta_change_days": 2})
        bad_state = client.post("/me/tasks/CHK-4/update", json={"state": "almost"})
        too_long = client.post("/me/tasks/CHK-4/update", json={"note": "x" * 501})

    assert (not_mine.status_code, not_mine.json()["detail"]) == (404, TASK_NOT_ASSIGNED)
    for response, words in (
        (empty, "Nothing to update"),
        (past, "today only"),
        (eta_past, "can't be before today"),
        (blocked, "Blocked needs a blocker"),
    ):
        assert response.status_code == 422, response.text
        assert words in response.json()["detail"]
    for response in (unknown_field, bad_state, too_long):
        assert response.status_code == 422


def test_the_pod_task_list_carries_the_owners_last_statement_and_eta(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "dev,mgr", "dev_principal_subject": _DEV}
        )
    )
    with TestClient(app) as client:
        _seeded(app)
        today = date.today().isoformat()
        updated = client.post(
            "/me/tasks/CHK-4/update",
            json={"state": "in_review", "note": "MR !3 open", "eta": today},
        )
        tasks = client.get("/pods/pod-pay/tasks")

    assert updated.status_code == 200, updated.text
    assert tasks.status_code == 200, tasks.text
    rows = {task["id"]: task for task in tasks.json()["tasks"]}
    chk4 = rows["CHK-4"]
    assert chk4["last_update"]["state"] == "in_review"
    assert chk4["last_update"]["note"] == "MR !3 open"
    assert chk4["last_update"]["via"] == "console"
    assert chk4["last_update_by"] == "Kai Thompson"
    assert chk4["eta"] == today
    assert chk4["eta_label"]
    assert (rows["CHK-9"]["last_update"], rows["CHK-9"]["eta"]) == (None, None)
