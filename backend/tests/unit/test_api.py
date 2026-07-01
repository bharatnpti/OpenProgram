from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_directory_sync_service
from api.main import create_app
from config.settings import Settings
from core.domain.errors import ProviderConfigurationError
from core.domain.graph import Task
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.workflows import (
    CheckinScheduleConfig,
    ConversationPurgeScheduleConfig,
    DeveloperCheckinDispatch,
    ScheduleBootstrapResult,
    SyncDispatchInput,
    SyncScheduleConfig,
)
from core.ports.llm import LlmProvider
from infra.registry import ServiceRegistry
from tests.fixtures.demo_graph import populate_demo_graph


def test_health_and_graph_routes(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        health_response = client.get("/health")
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "ok"

        ready_response = client.get("/ready")
        assert ready_response.status_code == 200
        assert ready_response.json()["status"] == "ok"

        _populate_graph_fixture(app, settings)
        graph_response = client.get("/graph/programs/program-platform/tree")
        assert graph_response.status_code == 200
        body = graph_response.json()
        assert body["root"]["id"] == "program-platform"
        assert len(body["nodes"]) >= 5


def test_memory_app_starts_without_demo_data(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.get("/programs")

    assert response.status_code == 200
    assert response.json() == []


def test_admin_directory_search_and_member_add_flow(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        sync_response = client.post("/config/directory/sync")
        search_response = client.get("/config/directory/users", params={"query": "asha"})
        add_response = client.post(
            "/config/members/from-directory",
            json={"external_ids": ["U1001"]},
        )
        members_response = client.get("/config/members")

    assert sync_response.status_code == 200
    assert sync_response.json() == {
        "tenant_id": "demo",
        "synced_count": 3,
        "deactivated_count": 0,
    }
    assert search_response.status_code == 200
    assert search_response.json()["total"] == 1
    assert [item["external_id"] for item in search_response.json()["items"]] == ["U1001"]
    assert add_response.status_code == 201
    assert [item["id"] for item in add_response.json()] == ["U1001"]
    assert members_response.status_code == 200
    assert [item["id"] for item in members_response.json()] == ["U1001"]


def test_admin_add_from_directory_missing_id_returns_404(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/config/members/from-directory",
            json={"external_ids": ["missing-user"]},
        )

    assert response.status_code == 404
    assert "missing-user" in response.json()["detail"]


def test_admin_directory_sync_provider_unavailable_returns_503(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={
                "runtime_mode": "container",
                "slack_bot_token": None,
            }
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/config/directory/sync")

    assert response.status_code == 503
    assert "slack_bot_token" in response.json()["detail"]


def test_admin_directory_sync_provider_configuration_error_returns_424(
    settings: Settings,
) -> None:
    app = create_app(settings=settings)
    app.dependency_overrides[get_directory_sync_service] = lambda: (
        _ProviderConfigurationFailingDirectorySyncService()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/config/directory/sync")

    assert response.status_code == 424
    detail = response.json()["detail"]
    assert "users:read" in detail
    assert "slack bot token" in detail


def test_admin_add_from_directory_validates_batch_before_writing(
    settings: Settings,
) -> None:
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        sync_response = client.post("/config/directory/sync")
        add_response = client.post(
            "/config/members/from-directory",
            json={"external_ids": ["U1001", "missing-user"]},
        )
        members_response = client.get("/config/members")

    assert sync_response.status_code == 200
    assert add_response.status_code == 404
    assert "missing-user" in add_response.json()["detail"]
    assert members_response.status_code == 200
    assert members_response.json() == []


def test_admin_add_from_directory_wrong_kind_conflict_returns_409(
    settings: Settings,
) -> None:
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        sync_response = client.post("/config/directory/sync")
        project_response = client.post(
            "/config/projects",
            json={"id": "U1001", "name": "Directory ID Project"},
        )
        add_response = client.post(
            "/config/members/from-directory",
            json={"external_ids": ["U1001"]},
        )

    assert sync_response.status_code == 200
    assert project_response.status_code == 201
    assert add_response.status_code == 409
    assert "not a developer" in add_response.json()["detail"]


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


def test_chat_webhook_route_reports_clarifying_reply(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "chat_provider": "fake",
            "issue_tracker_provider": "fake",
            "llm_provider": "fake",
        }
    )
    registry = _ScriptedLlmRegistry(
        configured,
        llm=_SequenceLlmProvider(
            texts=[
                "Can you share status?",
                '{"sufficient":false,"question":"What blocker should I note?","signals":null}',
            ]
        ),
    )
    app = create_app(settings=configured, registry=registry)
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
                "text": "Still working on it",
                "message_id": "msg-reply-1",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "clarifying",
        "message_id": "msg-reply-1",
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


def test_chat_webhook_route_echoes_verification_challenge(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/chat/slack",
            json={"type": "url_verification", "challenge": "challenge-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-token"}


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
        _populate_graph_fixture(app, settings)
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
        _populate_graph_fixture(sm_app, settings)
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
        _populate_graph_fixture(po_app, settings)
        project = client.get("/projects/project-foundations/progress?as_of=2026-06-15")
        pod_denied = client.get("/pods/pod-runtime/checkins?as_of=2026-06-15")

    assert project.status_code == 200
    assert project.json()["total_tasks"] == 4
    assert pod_denied.status_code == 403

    exec_app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "exec"}))
    with TestClient(exec_app) as client:
        _populate_graph_fixture(exec_app, settings)
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
        member = client.post("/config/members", json={"id": "dev-1", "name": "Dev One"})
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

    assert member.status_code == 201
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


def test_admin_checkin_dispatch_rejects_unknown_developer_without_dispatch(
    settings: Settings,
) -> None:
    configured = settings.model_copy(update={"workflow_provider": "fake"})
    registry = _RecordingWorkflowRegistry(configured)
    app = create_app(settings=configured, registry=registry)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/admin/workflows/checkin/dispatch",
            json={
                "tenant_id": "demo",
                "developer_id": "UQA_UNKNOWN",
                "checkin_date": "2026-01-10",
            },
        )

    schedule_run = asyncio.run(
        registry.status_repository().checkin_schedule_run(
            "demo",
            "UQA_UNKNOWN",
            date(2026, 1, 10),
        )
    )
    checkin = asyncio.run(
        registry.status_repository().checkin_by_correlation(
            "demo",
            "checkin-UQA_UNKNOWN-2026-01-10",
        )
    )

    assert response.status_code == 404
    assert "UQA_UNKNOWN" in response.json()["detail"]
    assert registry.scheduler.checkin_inputs == []
    assert schedule_run is None
    assert checkin is None


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


def test_admin_config_crud_populates_directory_and_dashboards(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={
                "tenant_default_timezone": "Asia/Kolkata",
                "checkin_reply_wait_seconds": 60,
                "checkin_final_reply_wait_seconds": 120,
            }
        )
    )
    as_of = date.today().isoformat()
    with TestClient(app) as client:
        empty_programs = client.get("/programs")
        program = client.post(
            "/config/programs",
            json={
                "id": "program-alpha",
                "name": "Alpha Program",
                "description": "Runtime config program",
            },
        )
        project = client.post(
            "/config/projects",
            json={
                "id": "project-alpha",
                "name": "Alpha Project",
                "description": "Configured project",
                "code": "ALPHA",
            },
        )
        pod = client.post(
            "/config/pods",
            json={"id": "pod-alpha", "name": "Alpha Pod"},
        )
        workstream = client.post(
            "/config/workstreams",
            json={
                "id": "workstream-alpha",
                "name": "Runtime Config Admin",
                "metadata": {
                    "type": "feature",
                    "phase": "build",
                    "owner_id": "dev-ada",
                    "target_date": as_of,
                    "confidence": 0.7,
                    "summary": "Configuring workstream graph controls.",
                },
            },
        )
        member = client.post(
            "/config/members",
            json={"id": "dev-ada", "name": "Ada"},
        )
        asyncio.run(
            app.state.registry.graph_repository().upsert_node(
                Task(
                    tenant_id=settings.tenant_id,
                    id="task-alpha",
                    name="Alpha task",
                    metadata={"status": "blocked"},
                )
            )
        )
        program_link = client.post(
            "/config/projects/project-alpha/program",
            json={"program_id": "program-alpha"},
        )
        pod_link = client.post("/config/pods/pod-alpha/projects/project-alpha")
        workstream_link = client.post(
            "/config/projects/project-alpha/workstreams/workstream-alpha"
        )
        pod_workstream_link = client.post("/config/pods/pod-alpha/workstreams/workstream-alpha")
        workstream_task_link = client.post("/config/workstreams/workstream-alpha/tasks/task-alpha")
        member_link = client.post(
            "/config/pods/pod-alpha/members/dev-ada",
            json={"role": "engineer"},
        )
        duplicate_member_link = client.post(
            "/config/pods/pod-alpha/members/dev-ada",
            json={"role": "engineer"},
        )
        preference = client.put(
            "/config/members/dev-ada/checkin-preference",
            json={"local_time": "10:45:00", "weekdays": [0, 2, 4]},
        )
        preference_list = client.get("/config/checkin-preferences")
        projects = client.get(f"/projects?as_of={as_of}")
        workstreams = client.get(f"/workstreams?as_of={as_of}")
        workstream_detail = client.get(f"/workstreams/workstream-alpha?as_of={as_of}")
        project_workstreams = client.get(
            f"/projects/project-alpha/workstreams?as_of={as_of}"
        )
        pods = client.get(f"/pods?as_of={as_of}")
        checkins = client.get(f"/pods/pod-alpha/checkins?as_of={as_of}")
        progress = client.get(f"/projects/project-alpha/progress?as_of={as_of}")
        workstream_progress = client.get(
            f"/workstreams/workstream-alpha/progress?as_of={as_of}"
        )
        heatmap = client.get(f"/portfolio/heatmap?as_of={as_of}")
        delete_member_link = client.delete("/config/pods/pod-alpha/members/dev-ada")
        delete_project = client.delete("/config/projects/project-alpha")
        projects_after_delete = client.get(f"/projects?as_of={as_of}")

    assert empty_programs.status_code == 200
    assert empty_programs.json() == []
    assert program.status_code == 201
    assert project.status_code == 201
    assert project.json()["metadata"]["code"] == "ALPHA"
    assert pod.status_code == 201
    assert workstream.status_code == 201
    assert workstream.json()["kind"] == "workstream"
    assert member.status_code == 201
    assert program_link.status_code == 200
    assert pod_link.status_code == 200
    assert workstream_link.status_code == 200
    assert pod_workstream_link.status_code == 200
    assert pod_workstream_link.json()["kind"] == "assigned_to"
    assert workstream_task_link.status_code == 200
    assert member_link.status_code == 200
    assert member_link.json()["metadata"]["role"] == "engineer"
    assert duplicate_member_link.status_code == 409
    assert preference.status_code == 200
    assert preference.json()["local_time"] == "10:45:00"
    assert preference.json()["timezone"] == "Asia/Kolkata"
    assert preference_list.status_code == 200
    assert preference_list.json()[0]["developer_id"] == "dev-ada"
    assert projects.status_code == 200
    assert projects.json()[0]["program_ids"] == ["program-alpha"]
    assert projects.json()[0]["pod_ids"] == ["pod-alpha"]
    assert projects.json()[0]["workstream_ids"] == ["workstream-alpha"]
    assert workstreams.status_code == 200
    assert workstreams.json()[0]["project_ids"] == ["project-alpha"]
    assert workstreams.json()[0]["pod_ids"] == ["pod-alpha"]
    assert workstreams.json()[0]["task_ids"] == ["task-alpha"]
    assert workstream_detail.status_code == 200
    assert workstream_detail.json()["id"] == "workstream-alpha"
    assert project_workstreams.status_code == 200
    assert [item["id"] for item in project_workstreams.json()] == ["workstream-alpha"]
    assert pods.status_code == 200
    assert pods.json()[0]["workstream_ids"] == ["workstream-alpha"]
    assert pods.json()[0]["member_ids"] == ["dev-ada"]
    assert checkins.status_code == 200
    assert checkins.json()["missing"] == 1
    assert progress.status_code == 200
    assert progress.json()["project_id"] == "project-alpha"
    assert workstream_progress.status_code == 200
    assert workstream_progress.json()["workstream_id"] == "workstream-alpha"
    assert workstream_progress.json()["rag"] == "red"
    assert heatmap.status_code == 200
    assert heatmap.json()["cells"]
    assert delete_member_link.status_code == 204
    assert delete_project.status_code == 204
    assert projects_after_delete.status_code == 200
    assert projects_after_delete.json() == []


def test_config_routes_are_admin_only_but_directory_is_readable(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "dev"}))
    with TestClient(app) as client:
        denied = client.post(
            "/config/programs",
            json={"id": "program-alpha", "name": "Alpha Program"},
        )
        directory = client.get("/programs")

    assert denied.status_code == 403
    assert directory.status_code == 200


def test_config_crud_full_lifecycle(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "admin"}))
    with TestClient(app) as client:
        program = client.post(
            "/config/programs",
            json={"id": "program-alpha", "name": "Alpha Program"},
        )
        project = client.post(
            "/config/projects",
            json={
                "id": "project-alpha",
                "name": "Alpha Project",
                "code": "ALPHA",
                "jira_project_key": "PO",
                "jira_board_id": "board-1",
                "github_repos": ["oneai/program-manager", "oneai/api", "oneai/api"],
            },
        )
        updated_project = client.put(
            "/config/projects/project-alpha",
            json={"jira_base_jql": 'labels = "alpha"', "github_repos": "oneai/program-manager"},
        )
        fetched_project = client.get("/config/projects/project-alpha")
        pod = client.post(
            "/config/pods",
            json={
                "id": "pod-alpha",
                "name": "Alpha Pod",
                "jira_filter_jql": "component = API",
                "github_repos": ["oneai/program-manager"],
            },
        )
        updated_pod = client.put("/config/pods/pod-alpha", json={"github_repos": []})
        fetched_pod = client.get("/config/pods/pod-alpha")
        member = client.post("/config/members", json={"id": "dev-ada", "name": "Ada"})
        fetched_program = client.get("/config/programs/program-alpha")
        updated_program = client.put(
            "/config/programs/program-alpha",
            json={"name": "Alpha Program v2"},
        )
        program_link = client.post(
            "/config/projects/project-alpha/program",
            json={"program_id": "program-alpha"},
        )
        pod_link = client.post("/config/pods/pod-alpha/projects/project-alpha")
        member_link = client.post(
            "/config/pods/pod-alpha/members/dev-ada",
            json={"role": "engineer"},
        )
        programs = client.get("/config/programs")
        projects = client.get("/projects")
        fetched_member = client.get("/config/members/dev-ada")
        delete_member = client.delete("/config/members/dev-ada")
        missing_member = client.get("/config/members/dev-ada")

    assert program.status_code == 201
    assert project.status_code == 201
    assert project.json()["jira_project_key"] == "PO"
    assert project.json()["jira_board_id"] == "board-1"
    assert project.json()["github_repos"] == ["oneai/program-manager", "oneai/api"]
    assert updated_project.status_code == 200
    assert updated_project.json()["jira_base_jql"] == 'labels = "alpha"'
    assert updated_project.json()["github_repos"] == ["oneai/program-manager"]
    assert fetched_project.status_code == 200
    assert fetched_project.json()["metadata"]["github_repos"] == "oneai/program-manager"
    assert pod.status_code == 201
    assert pod.json()["jira_filter_jql"] == "component = API"
    assert pod.json()["github_repos"] == ["oneai/program-manager"]
    assert updated_pod.status_code == 200
    assert updated_pod.json()["github_repos"] == []
    assert fetched_pod.status_code == 200
    assert fetched_pod.json()["metadata"]["github_repos"] is None
    assert member.status_code == 201
    assert fetched_program.status_code == 200
    assert fetched_program.json()["name"] == "Alpha Program"
    assert updated_program.status_code == 200
    assert updated_program.json()["name"] == "Alpha Program v2"
    assert program_link.status_code == 200
    assert pod_link.status_code == 200
    assert member_link.status_code == 200
    assert member_link.json()["metadata"]["role"] == "engineer"
    assert any(item["id"] == "program-alpha" for item in programs.json())
    assert any(item["id"] == "project-alpha" for item in projects.json())
    assert projects.json()[0]["program_ids"] == ["program-alpha"]
    assert projects.json()[0]["pod_ids"] == ["pod-alpha"]
    assert fetched_member.status_code == 200
    assert delete_member.status_code == 204
    assert missing_member.status_code == 404


@dataclass
class _ProviderConfigurationFailingDirectorySyncService:
    async def sync(self, tenant_id: str) -> object:
        raise ProviderConfigurationError(
            "slack bot token is missing required OAuth scope(s) for users.list: users:read"
        )


@dataclass
class _SequenceLlmProvider:
    texts: list[str]
    requests: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            tenant_id=request.tenant_id,
            text=self.texts.pop(0),
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost_usd=0.0,
                latency_ms=1.0,
            ),
            trace_id=f"trace-{len(self.requests)}",
        )


class _ScriptedLlmRegistry(ServiceRegistry):
    def __init__(self, settings: Settings, *, llm: _SequenceLlmProvider) -> None:
        super().__init__(settings)
        self._scripted_llm = llm

    def llm_provider(self) -> LlmProvider:
        return self._scripted_llm


@dataclass
class _RecordingDispatchScheduler:
    checkin_inputs: list[DeveloperCheckinDispatch] = field(default_factory=list)
    sync_inputs: list[SyncDispatchInput] = field(default_factory=list)

    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id="recorded-heartbeat", status="ready")

    async def ensure_checkin_fanout_schedule(
        self,
        config: CheckinScheduleConfig,
    ) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")

    async def ensure_conversation_purge_schedule(
        self,
        config: ConversationPurgeScheduleConfig,
    ) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")

    async def ensure_sync_schedules(
        self,
        configs: Sequence[SyncScheduleConfig],
    ) -> list[ScheduleBootstrapResult]:
        return [
            ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")
            for config in configs
        ]

    async def dispatch_developer_checkin(self, input: DeveloperCheckinDispatch) -> str:
        self.checkin_inputs.append(input)
        return f"recorded-checkin-{input.developer_id}"

    async def dispatch_sync(self, input: SyncDispatchInput) -> str:
        self.sync_inputs.append(input)
        return f"recorded-sync-{input.connector}-{input.scope}"


class _RecordingWorkflowRegistry(ServiceRegistry):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.scheduler = _RecordingDispatchScheduler()

    def workflow_scheduler(self) -> _RecordingDispatchScheduler:
        return self.scheduler


def _populate_graph_fixture(app: FastAPI, settings: Settings) -> None:
    asyncio.run(
        populate_demo_graph(
            app.state.registry.graph_repository(),
            app.state.registry.time_series_repository(),
            settings.tenant_id,
        )
    )
