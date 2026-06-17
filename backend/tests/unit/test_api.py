from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings


def test_health_and_graph_routes(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        health_response = client.get("/health")
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        ready_response = client.get("/ready")
        assert ready_response.status_code == 200
        assert ready_response.json()["status"] == "ok"

        graph_response = client.get("/graph/programs/program-platform/tree")
        assert graph_response.status_code == 200
        body = graph_response.json()
        assert body["root"]["id"] == "program-platform"
        assert len(body["nodes"]) >= 5


def test_chat_webhook_route_uses_provider_mapper(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/chat/slack",
            json={
                "event": {
                    "user": "U123",
                    "text": "blocked on API",
                    "ts": "1700000000.000001",
                    "channel": "C123",
                }
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "message_id": "1700000000.000001",
    }


def test_chat_webhook_route_ignores_unsupported_provider(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.post("/webhooks/chat/unknown", json={"text": "ignored"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ignored",
        "message_id": "unsupported-provider",
    }


def test_metrics_endpoint_exposes_prometheus_metrics(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        client.get("/health")
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "pulseops_info" in response.text
    assert "pulseops_http_requests_total" in response.text


def test_cors_origins_are_configurable(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(update={"cors_origins": ("https://frontend.example",)})
    )
    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": "https://frontend.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.example"


def test_dev_focus_is_own_scope_and_omits_raw_replies(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "dev", "dev_principal_subject": "dev-asha"}
        )
    )
    with TestClient(app) as client:
        focus_response = client.get("/me/focus?as_of=2026-06-15")
        blocked_response = client.get("/pods/pod-runtime/blockers?as_of=2026-06-15")

    assert focus_response.status_code == 200
    body = focus_response.json()
    assert body["developer_id"] == "dev-asha"
    assert {task["id"] for task in body["tasks"]} == {"task-api"}
    serialized = json.dumps(body)
    assert "raw_reply" not in serialized
    assert "API shell is ready for review; no blockers." not in serialized
    assert blocked_response.status_code == 403


def test_persona_aggregate_routes_are_role_scoped(settings: Settings) -> None:
    sm_app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "sm"}))
    with TestClient(sm_app) as client:
        blockers = client.get("/pods/pod-runtime/blockers?as_of=2026-06-15")
        checkins = client.get("/pods/pod-runtime/checkins?as_of=2026-06-15")
        project_denied = client.get("/projects/project-foundations/progress?as_of=2026-06-15")

    assert blockers.status_code == 200
    assert blockers.json()["blockers"][0]["owner_id"] == "dev-liam"
    assert checkins.status_code == 200
    assert checkins.json()["stale"] >= 1
    assert project_denied.status_code == 403

    po_app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "po"}))
    with TestClient(po_app) as client:
        project = client.get("/projects/project-foundations/progress?as_of=2026-06-15")
        pod_denied = client.get("/pods/pod-runtime/checkins?as_of=2026-06-15")

    assert project.status_code == 200
    assert project.json()["total_tasks"] == 4
    assert pod_denied.status_code == 403

    exec_app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "exec"}))
    with TestClient(exec_app) as client:
        tree = client.get("/programs/program-platform/tree?as_of=2026-06-15")
        heatmap = client.get("/portfolio/heatmap?as_of=2026-06-15")
        project_denied = client.get("/projects/project-foundations/progress?as_of=2026-06-15")

    assert tree.status_code == 200
    assert tree.json()["root_id"] == "program-platform"
    assert heatmap.status_code == 200
    assert "raw_reply" not in json.dumps(heatmap.json())
    assert project_denied.status_code == 403
