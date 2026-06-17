from __future__ import annotations

import asyncio
from datetime import UTC, datetime

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


def test_chat_webhook_route_processes_correlated_reply(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={
                "chat_provider": "fake",
                "issue_tracker_provider": "fake",
                "llm_provider": "fake",
            }
        )
    )
    with TestClient(app) as client:
        asyncio.run(
            app.state.registry.status_collector().start_checkin(
                tenant_id=settings.tenant_id,
                developer_id="dev-1",
                chat_external_id="U123",
                correlation_id="corr-route",
                asked_at=datetime.now(tz=UTC),
            )
        )
        response = client.post(
            "/webhooks/chat/fake",
            json={
                "user_id": "U123",
                "text": "blocked on API",
                "message_id": "msg-reply-1",
            },
        )
        duplicate_response = client.post(
            "/webhooks/chat/fake",
            json={
                "user_id": "U123",
                "text": "duplicate reply should be ignored",
                "message_id": "msg-reply-2",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "processed",
        "message_id": "msg-reply-1",
    }
    assert duplicate_response.status_code == 200
    assert duplicate_response.json() == {
        "status": "duplicate",
        "message_id": "msg-reply-2",
    }


def test_chat_webhook_route_ignores_uncorrelated_reply(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"chat_provider": "fake"}))
    with TestClient(app) as client:
        response = client.post("/webhooks/chat/fake", json={"text": "ignored"})

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


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


def test_dev_focus_is_own_scope(settings: Settings) -> None:
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
    assert project_denied.status_code == 403


def test_admin_workflow_dispatch_routes_are_admin_only(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"workflow_provider": "fake"}))
    with TestClient(app) as client:
        checkin = client.post(
            "/admin/workflows/checkin/dispatch",
            json={
                "tenant_id": "demo",
                "developer_id": "dev-1",
                "checkin_date": "2026-01-10",
            },
        )
        jira = client.post(
            "/admin/workflows/sync/jira",
            json={"tenant_id": "demo", "project_key": "PO"},
        )
        github = client.post(
            "/admin/workflows/sync/github",
            json={"tenant_id": "demo", "repo_name": "oneai/program-manager"},
        )
        calendar = client.post(
            "/admin/workflows/sync/calendar",
            json={
                "tenant_id": "demo",
                "user_id": "dev-1",
                "start": "2026-01-10",
                "end": "2026-01-11",
            },
        )

    assert checkin.status_code == 200
    assert checkin.json()["workflow_id"] == "fake-checkin-demo-dev-1-2026-01-10"
    assert jira.status_code == 200
    assert jira.json()["workflow_id"] == "fake-sync-issue-project-PO"
    assert github.status_code == 200
    assert github.json()["workflow_id"] == "fake-sync-vcs-repo-oneai-program-manager"
    assert calendar.status_code == 200
    assert calendar.json()["workflow_id"] == "fake-sync-calendar-user-dev-1"

    dev_app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "dev", "workflow_provider": "fake"}
        )
    )
    with TestClient(dev_app) as client:
        denied = client.post(
            "/admin/workflows/checkin/dispatch",
            json={"tenant_id": "demo", "developer_id": "dev-1"},
        )

    assert denied.status_code == 403


def test_checkin_preference_routes_merge_and_validate(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={
                "dev_principal_roles": "dev",
                "dev_principal_subject": "dev-asha",
                "tenant_default_timezone": "Asia/Kolkata",
                "checkin_reply_wait_seconds": 60,
                "checkin_final_reply_wait_seconds": 120,
            }
        )
    )
    with TestClient(app) as client:
        default_response = client.get("/me/checkin-preference")
        updated_response = client.put(
            "/me/checkin-preference",
            json={
                "local_time": "10:15:00",
                "weekdays": [0, 2, 4],
                "reply_wait_seconds": 30,
            },
        )
        invalid_weekday = client.put(
            "/me/checkin-preference",
            json={"weekdays": [7]},
        )
        invalid_wait = client.put(
            "/me/checkin-preference",
            json={"reply_wait_seconds": -1},
        )

    assert default_response.status_code == 200
    assert default_response.json() == {
        "developer_id": "dev-asha",
        "local_time": "09:30:00",
        "timezone": "Asia/Kolkata",
        "weekdays": [0, 1, 2, 3, 4],
        "reply_wait_seconds": 60,
        "final_reply_wait_seconds": 120,
    }
    assert updated_response.status_code == 200
    assert updated_response.json() == {
        "developer_id": "dev-asha",
        "local_time": "10:15:00",
        "timezone": "Asia/Kolkata",
        "weekdays": [0, 2, 4],
        "reply_wait_seconds": 30,
        "final_reply_wait_seconds": 120,
    }
    assert invalid_weekday.status_code == 422
    assert invalid_wait.status_code == 422


def test_portfolio_heatmap_accepts_program_root_id(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "exec"}))
    with TestClient(app) as client:
        response = client.get(
            "/portfolio/heatmap?as_of=2026-06-15&program_root_id=program-platform"
        )

    assert response.status_code == 200
    assert response.json()["as_of"] == "2026-06-15"
