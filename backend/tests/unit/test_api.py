from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from datetime import time as time_of_day

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_directory_sync_service
from api.main import create_app
from config.settings import Settings
from core.domain.brief import BriefKind, NarrativeBrief
from core.domain.errors import ProviderConfigurationError
from core.domain.graph import Developer, EntityRef, NodeKind, Task
from core.domain.integrations import Issue, IssueState
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.rollup import NodeStatus, Rag
from core.domain.status import CheckInPreference, StatusSource, WriteBackConsent
from core.domain.workflows import (
    CheckinScheduleConfig,
    ConversationPurgeScheduleConfig,
    DeveloperCheckinDispatch,
    ScheduleBootstrapResult,
    SyncDispatchInput,
    SyncScheduleConfig,
)
from core.domain.writeback import WriteBackAudit, WriteBackStatus
from core.ports.llm import LlmProvider
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeIssueTracker
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


def test_node_trend_endpoint_returns_daily_rag_history(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        registry = app.state.registry
        for day, rag in (
            ("2026-01-08", Rag.RED),
            ("2026-01-09", Rag.AMBER),
            ("2026-01-10", Rag.GREEN),
        ):
            asyncio.run(
                registry.rollup_repository().record_node_status(
                    NodeStatus(
                        entity_ref=EntityRef(
                            tenant_id="demo", kind=NodeKind.PROJECT, id="project-api"
                        ),
                        rag=rag,
                        source=StatusSource.CONFIRMED,
                        factors=(),
                        as_of=date.fromisoformat(day),
                    )
                )
            )
        response = client.get(
            "/persona/project/project-api/trend",
            params={"as_of": "2026-01-10", "window_days": 5},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["entity_ref"]["id"] == "project-api"
    assert body["window_days"] == 5
    assert [point["as_of"] for point in body["points"]] == [
        "2026-01-08",
        "2026-01-09",
        "2026-01-10",
    ]
    assert [point["score"] for point in body["points"]] == [1, 2, 3]


def test_narrative_briefs_endpoint_returns_newest_first(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        repository = app.state.registry.narrative_brief_repository()
        for kind, scope_id, generated_at, title in (
            (BriefKind.DAILY_POD, "pod-1", "2026-01-10T17:00:00+00:00", "Pod 1 daily"),
            (BriefKind.EXEC, "", "2026-01-11T16:00:00+00:00", "Exec brief"),
        ):
            asyncio.run(
                repository.record_brief(
                    NarrativeBrief(
                        tenant_id="demo",
                        kind=kind,
                        scope_id=scope_id,
                        title=title,
                        body="Descriptive rollup only.",
                        generated_at=datetime.fromisoformat(generated_at),
                        sources=("pod:pod-1",),
                    )
                )
            )
        all_response = client.get("/persona/briefs")
        exec_response = client.get("/persona/briefs", params={"kind": "exec"})

    assert all_response.status_code == 200
    briefs = all_response.json()["briefs"]
    assert [brief["title"] for brief in briefs] == ["Exec brief", "Pod 1 daily"]
    assert briefs[0]["kind"] == "exec"
    assert briefs[1]["sources"] == ["pod:pod-1"]

    assert exec_response.status_code == 200
    exec_briefs = exec_response.json()["briefs"]
    assert [brief["title"] for brief in exec_briefs] == ["Exec brief"]


def test_narrative_briefs_endpoint_rejects_out_of_range_limit(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/persona/briefs", params={"limit": 0})

    assert response.status_code == 422


def test_node_trend_endpoint_rejects_unsupported_kind(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/persona/workstream/ws-1/trend")

    assert response.status_code == 422


def test_node_trend_endpoint_answers_an_exec_for_the_program(settings: Settings) -> None:
    # The exec's Today charts the program's 30-day momentum from this route, so
    # an exec must be able to read it -- the same aggregate they read as heat.
    # A developer, who holds no aggregate read, is still turned away.
    def trend_status(role: str) -> int:
        role_settings = settings.model_copy(update={"dev_principal_roles": role})
        app = create_app(settings=role_settings)
        with TestClient(app, raise_server_exceptions=False) as client:
            asyncio.run(
                app.state.registry.rollup_repository().record_node_status(
                    NodeStatus(
                        entity_ref=EntityRef(
                            tenant_id="demo", kind=NodeKind.PROGRAM, id="program-platform"
                        ),
                        rag=Rag.AMBER,
                        source=StatusSource.CONFIRMED,
                        factors=(),
                        as_of=date(2026, 1, 10),
                    )
                )
            )
            response = client.get(
                "/persona/program/program-platform/trend",
                params={"as_of": "2026-01-10", "window_days": 30},
            )
        if response.status_code == 200:
            assert [point["rag"] for point in response.json()["points"]] == ["amber"]
        return response.status_code

    assert trend_status("exec") == 200
    assert trend_status("dev") == 403


def test_pod_escalation_contacts_endpoint_round_trip(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        create = client.post("/config/pods", json={"id": "pod-1", "name": "Runtime Pod"})
        for member_id, name in (("sam", "Sam SM"), ("mia", "Mia Manager"), ("nolink", "No Link")):
            client.post("/config/members", json={"id": member_id, "name": name})
        client.put("/config/members/sam/identity-link", json={"chat_user_id": "U-SM"})
        client.put("/config/members/mia/identity-link", json={"chat_user_id": "U-MGR"})
        client.post("/config/pods/pod-1/members/sam", json={"role": "scrum_master"})
        empty = client.get("/config/pods/pod-1/escalation-contacts")
        candidates = client.get("/config/pods/pod-1/escalation-candidates")
        update = client.put(
            "/config/pods/pod-1/escalation-contacts",
            json={"scrum_master": {"member_id": "sam"}, "manager": {"member_id": "mia"}},
        )
        reloaded = client.get("/config/pods/pod-1/escalation-contacts")
        unlinked = client.put(
            "/config/pods/pod-1/escalation-contacts",
            json={"scrum_master": {"member_id": "nolink"}},
        )
        typed = client.put(
            "/config/pods/pod-1/escalation-contacts",
            json={"manager": {"chat_external_id": "U-NOBODY"}},
        )
        blank = client.put("/config/pods/pod-1/escalation-contacts", json={"manager": {}})
        after_rejections = client.get("/config/pods/pod-1/escalation-contacts")

    assert create.status_code == 201
    assert empty.status_code == 200
    assert empty.json()["scrum_master"] is None
    assert candidates.status_code == 200
    assert [item["member_id"] for item in candidates.json()] == ["sam", "mia", "nolink"]
    assert candidates.json()[0] == {
        "member_id": "sam",
        "name": "Sam SM",
        "chat_user_id": "U-SM",
        "in_pod": True,
        "pod_role": "scrum_master",
    }
    assert candidates.json()[2]["chat_user_id"] is None
    assert update.status_code == 200
    body = reloaded.json()
    assert body["pod_id"] == "pod-1"
    assert body["scrum_master"] == {
        "chat_external_id": "U-SM",
        "display_name": "Sam SM",
        "member_id": "sam",
    }
    assert body["manager"]["chat_external_id"] == "U-MGR"
    assert body["manager"]["member_id"] == "mia"
    assert unlinked.status_code == 400
    assert typed.status_code == 400
    assert blank.status_code == 422
    assert after_rejections.json() == body


def test_pod_escalation_contacts_endpoint_missing_pod_returns_404(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/config/pods/missing/escalation-contacts")

    assert response.status_code == 404


def test_identity_link_auto_match_and_unmapped_flow(settings: Settings) -> None:
    # Fake tracker: "unmapped" means unreachable on chat only.
    app = create_app(settings=settings.model_copy(update={"issue_tracker_provider": "fake"}))
    with TestClient(app) as client:
        client.post("/config/directory/sync")
        client.post("/config/members/from-directory", json={"external_ids": ["U1001", "U1002"]})
        # U1002 gets an admin-set chat id that auto-match must not overwrite.
        client.put(
            "/config/members/U1002/identity-link",
            json={"chat_user_id": "ADMIN-CHAT"},
        )

        unmapped_before = client.get("/config/members/unmapped")
        auto_match = client.post("/config/members/identity-links/auto-match")
        u1001_link = client.get("/config/members/U1001/identity-link")
        u1002_link = client.get("/config/members/U1002/identity-link")
        unmapped_after = client.get("/config/members/unmapped")

    assert unmapped_before.status_code == 200
    # U1002 already has an admin chat id, so only U1001 is unmapped for delivery.
    assert {item["id"] for item in unmapped_before.json()} == {"U1001"}
    u1001_before = next(item for item in unmapped_before.json() if item["id"] == "U1001")
    assert "chat_user_id" in u1001_before["missing"]

    assert auto_match.status_code == 200
    summary = auto_match.json()
    # U1001 gets both fields; U1002 keeps its admin chat id and only gains jira_email.
    assert summary["updated_count"] == 2
    matched = {member["id"]: member for member in summary["members"]}
    assert set(matched) == {"U1001", "U1002"}
    assert set(matched["U1001"]["filled"]) == {"chat_user_id", "jira_email"}
    assert set(matched["U1002"]["filled"]) == {"jira_email"}

    assert u1001_link.json()["chat_user_id"] == "U1001"
    assert u1001_link.json()["jira_email"] == "asha@example.com"
    # Admin-set value survives; only the missing jira_email is filled.
    assert u1002_link.json()["chat_user_id"] == "ADMIN-CHAT"
    assert u1002_link.json()["jira_email"] == "liam@example.com"

    assert unmapped_after.status_code == 200
    assert unmapped_after.json() == []


def test_identity_unmapped_flags_missing_jira_account_with_a_real_tracker(
    settings: Settings,
) -> None:
    # With Jira configured, a member reachable on chat but with no Jira account
    # is unmapped too: none of their issues can be attributed to them.
    assert settings.issue_tracker_provider == "jira"
    app = create_app(settings=settings)
    with TestClient(app) as client:
        client.post("/config/directory/sync")
        client.post("/config/members/from-directory", json={"external_ids": ["U1001", "U1002"]})
        client.put(
            "/config/members/U1001/identity-link",
            json={"chat_user_id": "U1001", "jira_account_id": "712020:asha"},
        )
        client.put("/config/members/U1002/identity-link", json={"chat_user_id": "U1002"})
        unmapped = client.get("/config/members/unmapped")
        # Jira is not reachable in tests: auto-match must degrade, not fail.
        auto_match = client.post("/config/members/identity-links/auto-match")

    assert unmapped.status_code == 200
    assert [item["id"] for item in unmapped.json()] == ["U1002"]
    assert "jira_account_id" in unmapped.json()[0]["missing"]
    assert "chat_user_id" not in unmapped.json()[0]["missing"]
    assert auto_match.status_code == 200


def test_identity_unmapped_requires_manage_config(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "dev"}))
    with TestClient(app, raise_server_exceptions=False) as client:
        unmapped = client.get("/config/members/unmapped")
        auto_match = client.post("/config/members/identity-links/auto-match")

    assert unmapped.status_code == 403
    assert auto_match.status_code == 403


def test_writeback_consent_put_then_get_round_trips(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        client.post("/config/directory/sync")
        client.post("/config/members/from-directory", json={"external_ids": ["U1001"]})

        default = client.get("/config/members/U1001/writeback-consent")
        for value in ("auto_apply", "never", "always_ask"):
            updated = client.put(
                "/config/members/U1001/writeback-consent",
                json={"consent": value},
            )
            assert updated.status_code == 200, value
            assert updated.json() == {"developer_id": "U1001", "consent": value}
            after = client.get("/config/members/U1001/writeback-consent")
            assert after.json()["consent"] == value

    assert default.status_code == 200
    # Default when unset is always_ask.
    assert default.json() == {"developer_id": "U1001", "consent": "always_ask"}


def test_writeback_consent_requires_manage_config(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "dev"}))
    with TestClient(app, raise_server_exceptions=False) as client:
        get_resp = client.get("/config/members/U1001/writeback-consent")
        put_resp = client.put(
            "/config/members/U1001/writeback-consent",
            json={"consent": "never"},
        )
    assert get_resp.status_code == 403
    assert put_resp.status_code == 403


def test_tenant_writeback_reports_the_default_until_a_tenant_override(
    settings: Settings,
) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        default = client.get("/config/tenant/writeback")
        switched_on = client.put("/config/tenant/writeback", json={"enabled": True})
        after_on = client.get("/config/tenant/writeback")
        switched_off = client.put("/config/tenant/writeback", json={"enabled": False})
        after_off = client.get("/config/tenant/writeback")

    assert default.status_code == 200
    # Off unless switched on, and the console can tell it is the deployment default.
    assert default.json() == {"enabled": False, "source": "default"}
    assert switched_on.json() == {"enabled": True, "source": "tenant"}
    assert after_on.json() == {"enabled": True, "source": "tenant"}
    assert switched_off.json() == {"enabled": False, "source": "tenant"}
    assert after_off.json() == {"enabled": False, "source": "tenant"}


def test_tenant_writeback_override_beats_the_environment_default(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"jira_writeback_enabled": True}))
    with TestClient(app) as client:
        default = client.get("/config/tenant/writeback")
        client.put("/config/tenant/writeback", json={"enabled": False})
        overridden = client.get("/config/tenant/writeback")

    assert default.json() == {"enabled": True, "source": "default"}
    assert overridden.json() == {"enabled": False, "source": "tenant"}


def test_tenant_writeback_requires_manage_config(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "dev"}))
    with TestClient(app, raise_server_exceptions=False) as client:
        get_resp = client.get("/config/tenant/writeback")
        put_resp = client.put("/config/tenant/writeback", json={"enabled": True})
    assert get_resp.status_code == 403
    assert put_resp.status_code == 403


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


def test_admin_directory_sync_missing_slack_token_returns_424(settings: Settings) -> None:
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

    assert response.status_code == 424
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


def test_create_work_item_duplicate_id_returns_409(settings: Settings) -> None:
    app = create_app(settings=settings)
    payload = {
        "id": "wi-1",
        "name": "Wire payment intent API",
        "item_type": "feature",
        "state": "in_progress",
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.post("/config/work-items", json=payload)
        duplicate = client.post("/config/work-items", json=payload)
        duplicate_from_branch = client.post(
            "/config/work-items/from-branch",
            json={"repo": "acme/api", "branch": "feat/payments"},
        )
        second_from_branch = client.post(
            "/config/work-items/from-branch",
            json={"repo": "acme/api", "branch": "feat/payments"},
        )
        unknown_workstream = client.post(
            "/config/work-items",
            json={**payload, "id": "wi-2", "workstream_id": "ws-missing"},
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]
    assert duplicate_from_branch.status_code == 201
    assert second_from_branch.status_code == 409
    # The workstream link runs after the node is created, so it needs the same guard.
    assert unknown_workstream.status_code == 404
    assert "ws-missing" in unknown_workstream.json()["detail"]


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


def test_container_slack_webhook_route_accepts_valid_signature(settings: Settings) -> None:
    configured = _container_slack_settings(settings)
    app = create_app(settings=configured)
    body = _slack_json_body({"type": "url_verification", "challenge": "signed-challenge"})

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/chat/slack",
            content=body,
            headers=_signed_slack_headers(body),
        )

    assert response.status_code == 200
    assert response.json() == {"challenge": "signed-challenge"}


def test_container_slack_webhook_route_rejects_invalid_signature(settings: Settings) -> None:
    configured = _container_slack_settings(settings)
    app = create_app(settings=configured)
    body = _slack_json_body({"type": "url_verification", "challenge": "signed-challenge"})
    headers = _signed_slack_headers(body) | {"x-slack-signature": "v0=invalid"}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/webhooks/chat/slack", content=body, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid webhook signature"}


def test_container_slack_webhook_route_rejects_missing_signature(settings: Settings) -> None:
    configured = _container_slack_settings(settings)
    app = create_app(settings=configured)
    body = _slack_json_body({"type": "url_verification", "challenge": "signed-challenge"})

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/webhooks/chat/slack",
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid webhook signature"}


def test_container_slack_webhook_route_rejects_stale_signature(settings: Settings) -> None:
    configured = _container_slack_settings(settings)
    app = create_app(settings=configured)
    body = _slack_json_body({"type": "url_verification", "challenge": "signed-challenge"})
    stale_timestamp = str(int(time.time()) - configured.slack_signature_tolerance_seconds - 1)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/webhooks/chat/slack",
            content=body,
            headers=_signed_slack_headers(body, timestamp=stale_timestamp),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid webhook signature"}


def test_container_slack_webhook_route_ignores_signed_non_json_payload(
    settings: Settings,
) -> None:
    configured = _container_slack_settings(settings)
    app = create_app(settings=configured)
    body = b"not-json"

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/webhooks/chat/slack",
            content=body,
            headers=_signed_slack_headers(body),
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "message_id": "invalid-payload"}


def test_container_slack_webhook_route_ignores_signed_non_message_event(
    settings: Settings,
) -> None:
    configured = _container_slack_settings(settings)
    app = create_app(settings=configured)
    body = _slack_json_body({"event": {"type": "reaction_added", "user": "U123"}})

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/webhooks/chat/slack",
            content=body,
            headers=_signed_slack_headers(body),
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "message_id": "unsupported-provider"}


def test_container_slack_webhook_route_ignores_signed_bot_message_event(
    settings: Settings,
) -> None:
    configured = _container_slack_settings(settings)
    app = create_app(settings=configured)
    body = _slack_json_body(
        {
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "bot_id": "B123",
                "user": "U123",
                "text": "bot reply",
                "ts": "1700000000.000001",
                "channel": "C123",
            }
        }
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/webhooks/chat/slack",
            content=body,
            headers=_signed_slack_headers(body),
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "message_id": "unsupported-provider"}


async def test_chat_signature_required_keys_off_configured_provider_not_url(
    settings: Settings,
) -> None:
    # Configured chat provider is Slack in container mode: verification is
    # required regardless of the URL segment (mock_slack must not bypass it).
    configured = _container_slack_settings(settings)
    registry = ServiceRegistry(configured)
    body = _slack_json_body({"event": {"type": "message"}})

    try:
        assert not await registry.chat_webhook_signature_valid("mock_slack", {}, body)
        assert await registry.chat_webhook_signature_valid(
            "slack", _signed_slack_headers(body), body
        )
    finally:
        # Looking up the tenant's signing secret opens the process's shared
        # Postgres pool on this test's loop whenever a database answers. Close
        # it on that loop, as an app's lifespan would, or a later test's
        # shutdown meets a pool whose loop has closed.
        await registry.shutdown()


async def test_chat_signature_skipped_when_configured_provider_is_not_slack(
    settings: Settings,
) -> None:
    # Configured chat provider is the credential-free simulator: no signature.
    configured = settings.model_copy(
        update={"runtime_mode": "container", "chat_provider": "mock_slack"}
    )
    registry = ServiceRegistry(configured)

    assert await registry.chat_webhook_signature_valid("slack", {}, b"{}")
    assert await registry.chat_webhook_signature_valid("mock_slack", {}, b"{}")


def test_chat_webhook_dedupes_redelivered_event_id(settings: Settings) -> None:
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
        first = client.post(
            "/webhooks/chat/fake",
            json={"user_id": "U123", "text": "blocked on API", "message_id": "msg-dup"},
        )
        # Same message id => same derived event_id => absorbed as a redelivery.
        redelivery = client.post(
            "/webhooks/chat/fake",
            json={"user_id": "U123", "text": "blocked on API", "message_id": "msg-dup"},
        )

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert redelivery.status_code == 200
    assert redelivery.json() == {"status": "duplicate", "message_id": "msg-dup"}


def test_metrics_endpoint_exposes_prometheus_metrics(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        client.get("/health")
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "openprogram_info" in response.text
    assert "openprogram_http_requests_total" in response.text


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
        # Her own pod's blockers are hers to read (delivery_scope.py); another pod's are not.
        own_pod = client.get("/pods/pod-runtime/blockers?as_of=2026-06-15")
        blocked_response = client.get("/pods/pod-experience/blockers?as_of=2026-06-15")

    assert focus_response.status_code == 200
    body = focus_response.json()
    assert body["developer_id"] == "dev-asha"
    assert {task["id"] for task in body["tasks"]} == {"task-api"}
    assert body["developer_confirmed"] is False
    assert own_pod.status_code == 200
    assert blocked_response.status_code == 403


def test_dev_can_view_confirm_and_correct_own_status(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "dev", "dev_principal_subject": "dev-asha"}
        )
    )
    with TestClient(app) as client:
        _populate_graph_fixture(app, settings)
        status_response = client.get("/me/status?as_of=2026-06-15")
        confirm_response = client.post("/me/status/confirm?as_of=2026-06-15")
        correct_response = client.post(
            "/me/status/correct?as_of=2026-06-15",
            json={
                "summary": "API shell is corrected and ready.",
                "blockers": ["release review"],
                "eta_change_days": 2,
            },
        )
        focus_response = client.get("/me/focus?as_of=2026-06-15")

    assert status_response.status_code == 200
    assert status_response.json()["developer_confirmed"] is False
    assert confirm_response.status_code == 200
    assert confirm_response.json()["developer_confirmed"] is True
    assert confirm_response.json()["source"] == "confirmed"
    assert correct_response.status_code == 200
    assert correct_response.json()["summary"] == "API shell is corrected and ready."
    assert correct_response.json()["blockers"] == ["release review"]
    assert correct_response.json()["eta_change_days"] == 2
    assert correct_response.json()["developer_confirmed"] is True
    assert focus_response.status_code == 200
    assert focus_response.json()["developer_confirmed"] is True
    assert focus_response.json()["summary"] == "API shell is corrected and ready."


def test_self_status_is_self_scoped_for_every_role(settings: Settings) -> None:
    """Own status is readable by any role, and only ever the caller's own.

    A scrum master is asked for a check-in like anyone else, so /me/status is
    not developer-only; what it must never do is resolve a different subject.
    """
    for roles, subject in (("sm", "dev-liam"), ("po", "dev-liam"), ("exec", "dev-liam")):
        app = create_app(
            settings=settings.model_copy(
                update={"dev_principal_roles": roles, "dev_principal_subject": subject}
            )
        )
        with TestClient(app) as client:
            _populate_graph_fixture(app, settings)
            response = client.get("/me/status?as_of=2026-06-15")

        assert response.status_code == 200, (roles, response.text)
        # The route reads the principal's subject; there is no way to ask for
        # someone else's, so the fixture developer's own status comes back.
        assert response.json()["summary"], (roles, response.json())


def test_persona_aggregate_routes_are_role_scoped(settings: Settings) -> None:
    sm_app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "sm"}))
    with TestClient(sm_app) as client:
        _populate_graph_fixture(sm_app, settings)
        blockers = client.get("/pods/pod-runtime/blockers?as_of=2026-06-15")
        checkins = client.get("/pods/pod-runtime/checkins?as_of=2026-06-15")
        project_denied = client.get("/projects/project-foundations/progress?as_of=2026-06-15")

    assert blockers.status_code == 200
    first_blocker = blockers.json()["blockers"][0]
    assert first_blocker["owner_id"] == "dev-liam"
    assert first_blocker["blocker_id"] == first_blocker["id"]
    # Liam's fixture blocker is a flat status string: unattributed fallback.
    assert first_blocker["unattributed"] is True
    assert first_blocker["first_seen_on"] == "2026-06-14"
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
        project = client.get("/projects/project-foundations/progress?as_of=2026-06-15")
        workstream = client.get(
            "/workstreams/workstream-runtime-config-admin/progress?as_of=2026-06-15"
        )
        blockers_denied = client.get("/pods/pod-runtime/blockers?as_of=2026-06-15")
        checkins_denied = client.get("/pods/pod-runtime/checkins?as_of=2026-06-15")

    assert tree.status_code == 200
    assert tree.json()["root_id"] == "program-platform"
    assert heatmap.status_code == 200
    # An exec's heat tile opens the project it names: progress is aggregate, and
    # its factors are the ones the program tree already gives the exec.
    assert project.status_code == 200
    assert project.json()["total_tasks"] == 4
    tree_project = next(
        node for node in tree.json()["nodes"] if node["id"] == "project-foundations"
    )
    assert project.json()["factors"]
    assert project.json()["factors"] == tree_project["factors"]
    assert workstream.status_code == 200
    assert workstream.json()["workstream_id"] == "workstream-runtime-config-admin"
    # Pod check-ins and blockers are per-person and stay with SM and manager.
    assert blockers_denied.status_code == 403
    assert checkins_denied.status_code == 403

    mgr_app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "mgr"}))
    with TestClient(mgr_app) as client:
        _populate_graph_fixture(mgr_app, settings)
        blockers = client.get("/pods/pod-runtime/blockers?as_of=2026-06-15")
        checkins = client.get("/pods/pod-runtime/checkins?as_of=2026-06-15")
        project = client.get("/projects/project-foundations/progress?as_of=2026-06-15")

    # The manager is the last step of the non-response escalation, so they can
    # open the pod they are escalated about.
    assert blockers.status_code == 200
    assert blockers.json()["blockers"][0]["owner_id"] == "dev-liam"
    assert checkins.status_code == 200
    assert checkins.json()["stale"] >= 1
    assert project.status_code == 200


def test_pod_routes_answer_only_for_a_pod(settings: Settings) -> None:
    """Pod check-ins and blockers are granted for a pod, not for whatever id is passed.

    The tree walk starts from any node, so a project id used to return the
    check-ins and blockers of everyone in that project. A non-pod id is a 404,
    the same as an id that does not exist.
    """
    for role in ("sm", "mgr", "admin"):
        app = create_app(settings=settings.model_copy(update={"dev_principal_roles": role}))
        with TestClient(app) as client:
            _populate_graph_fixture(app, settings)
            for route in ("checkins", "blockers"):
                pod = client.get(f"/pods/pod-runtime/{route}?as_of=2026-06-15")
                project = client.get(f"/pods/project-foundations/{route}?as_of=2026-06-15")
                program = client.get(f"/pods/program-platform/{route}?as_of=2026-06-15")
                unknown = client.get(f"/pods/pod-does-not-exist/{route}?as_of=2026-06-15")

                assert pod.status_code == 200, (role, route, pod.text)
                assert pod.json()["pod_id"] == "pod-runtime", (role, route)
                assert project.status_code == 404, (role, route, project.text)
                assert program.status_code == 404, (role, route, program.text)
                assert unknown.status_code == 404, (role, route, unknown.text)


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
            json={"tenant_id": "demo", "repo_name": "oneai/openprogram"},
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
    assert github.json()["workflow_id"] == "fake-sync-vcs-repo-oneai-openprogram"
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
                "checkin_fanout_cron": "0 7 * * 1-4",
            }
        )
    )
    with TestClient(app) as client:
        asyncio.run(
            app.state.registry.graph_repository().upsert_node(
                Developer(tenant_id=settings.tenant_id, id="dev-asha", name="Asha")
            )
        )
        default_response = client.get("/me/checkin-preference")
        updated_response = client.put(
            "/me/checkin-preference",
            json={"timezone": "Europe/Berlin", "weekdays": [0, 2, 4]},
        )
        invalid_weekday = client.put(
            "/me/checkin-preference",
            json={"weekdays": [7]},
        )
        invalid_timezone = client.put(
            "/me/checkin-preference",
            json={"timezone": "Mars/Olympus_Mons"},
        )
        stored = asyncio.run(
            app.state.registry.status_repository().checkin_preference_for(
                settings.tenant_id, "dev-asha"
            )
        )

    defaults = {
        "local_time": "09:30:00",
        "timezone": "Asia/Kolkata",
        "weekdays": [0, 1, 2, 3, 4],
        "reply_wait_seconds": 60,
        "final_reply_wait_seconds": 120,
    }
    # When the bot asks is the tenant's one schedule, read in UTC, whatever the
    # member's or the team's zone.
    send = {
        "kind": "weekly",
        "cron": "0 7 * * 1-4",
        "timezone": "UTC",
        "local_time": "07:00:00",
        "weekdays": [0, 1, 2, 3],
        "month_days": None,
        "months": None,
    }
    assert default_response.status_code == 200
    assert default_response.json() == {
        "developer_id": "dev-asha",
        **defaults,
        "inherited": [
            "local_time",
            "timezone",
            "weekdays",
            "reply_wait_seconds",
            "final_reply_wait_seconds",
        ],
        "defaults": defaults,
        "send": send,
    }
    assert updated_response.status_code == 200
    assert updated_response.json() == {
        "developer_id": "dev-asha",
        "local_time": "09:30:00",
        "timezone": "Europe/Berlin",
        "weekdays": [0, 2, 4],
        "reply_wait_seconds": 60,
        "final_reply_wait_seconds": 120,
        "inherited": ["local_time", "reply_wait_seconds", "final_reply_wait_seconds"],
        "defaults": defaults,
        "send": send,
    }
    # Only what the member set is stored; the rest still follows the defaults.
    assert stored == CheckInPreference(
        tenant_id=settings.tenant_id,
        developer_id="dev-asha",
        timezone="Europe/Berlin",
        weekdays=(0, 2, 4),
    )
    assert invalid_weekday.status_code == 422
    assert invalid_timezone.status_code == 422


def test_self_checkin_preference_refuses_reply_windows_and_time(settings: Settings) -> None:
    # The reply windows decide when the scrum master and manager hear about a
    # missed check-in, so a person can't stretch their own; and check-ins go
    # out at one team time, so a personal time is refused rather than ignored.
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "dev", "dev_principal_subject": "dev-noah"}
        )
    )
    stored = CheckInPreference(
        tenant_id=settings.tenant_id,
        developer_id="dev-noah",
        timezone="Europe/Berlin",
        weekdays=(0, 1, 2, 3, 4),
        reply_wait_seconds=14400,
        final_reply_wait_seconds=28800,
    )
    with TestClient(app) as client:
        registry = app.state.registry
        asyncio.run(
            registry.graph_repository().upsert_node(
                Developer(tenant_id=settings.tenant_id, id="dev-noah", name="Noah")
            )
        )
        asyncio.run(registry.status_repository().record_checkin_preference(stored))
        reply_wait = client.put("/me/checkin-preference", json={"reply_wait_seconds": 999999})
        with_days = client.put(
            "/me/checkin-preference",
            json={"weekdays": [0], "final_reply_wait_seconds": 60},
        )
        local_time = client.put("/me/checkin-preference", json={"local_time": "07:00:00"})
        unknown = client.put("/me/checkin-preference", json={"escalate": False})
        after = asyncio.run(
            registry.status_repository().checkin_preference_for(settings.tenant_id, "dev-noah")
        )

    assert reply_wait.status_code == 422
    assert "reply_wait_seconds is set by an admin" in reply_wait.json()["detail"][0]["msg"]
    assert with_days.status_code == 422
    assert "final_reply_wait_seconds is set by an admin" in with_days.json()["detail"][0]["msg"]
    assert local_time.status_code == 422
    assert "local_time is not set per person" in local_time.json()["detail"][0]["msg"]
    assert unknown.status_code == 422
    # Nothing was stored, not even the valid days sent beside a window.
    assert after == stored


def test_self_checkin_preference_refused_save_writes_no_row(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "dev", "dev_principal_subject": "dev-asha"}
        )
    )
    with TestClient(app) as client:
        registry = app.state.registry
        asyncio.run(
            registry.graph_repository().upsert_node(
                Developer(tenant_id=settings.tenant_id, id="dev-asha", name="Asha")
            )
        )
        refused = client.put(
            "/me/checkin-preference",
            json={"reply_wait_seconds": 60, "final_reply_wait_seconds": 60},
        )
        stored = asyncio.run(
            registry.status_repository().checkin_preference_for(settings.tenant_id, "dev-asha")
        )

    assert refused.status_code == 422
    assert stored is None


def test_admin_checkin_preference_still_sets_reply_windows(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        member = client.post("/config/members", json={"id": "dev-ada", "name": "Ada"})
        saved = client.put(
            "/config/members/dev-ada/checkin-preference",
            json={"reply_wait_seconds": 7200, "final_reply_wait_seconds": 21600},
        )
        stored = asyncio.run(
            app.state.registry.status_repository().checkin_preference_for(
                settings.tenant_id, "dev-ada"
            )
        )

    assert member.status_code == 201
    assert saved.status_code == 200
    assert saved.json()["reply_wait_seconds"] == 7200
    assert saved.json()["final_reply_wait_seconds"] == 21600
    assert stored is not None
    assert stored.reply_wait_seconds == 7200
    assert stored.final_reply_wait_seconds == 21600


def test_checkin_preference_needs_a_member_record(settings: Settings) -> None:
    # Check-ins go only to member nodes, so a person with none has no
    # preference to read, and a save must not write a row nothing reads.
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "admin", "dev_principal_subject": "admin-only"}
        )
    )
    with TestClient(app) as client:
        read = client.get("/me/checkin-preference")
        save = client.put("/me/checkin-preference", json={"weekdays": [0, 1]})
        stored = asyncio.run(
            app.state.registry.status_repository().checkin_preference_for(
                settings.tenant_id, "admin-only"
            )
        )

    assert read.status_code == 404
    assert save.status_code == 404
    assert stored is None


def test_checkin_preference_partial_save_keeps_the_other_stored_values(
    settings: Settings,
) -> None:
    # The console sends only the fields a developer changed, so a save must
    # leave every other stored value exactly as it was -- the admin sliders
    # once cut stored 4 h / 8 h reply windows to 1 h / 2 h on an unchanged save.
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "dev", "dev_principal_subject": "dev-noah"}
        )
    )
    stored = CheckInPreference(
        tenant_id=settings.tenant_id,
        developer_id="dev-noah",
        local_time=time_of_day(8, 5),
        timezone="Europe/Berlin",
        weekdays=(4, 0, 1),
        reply_wait_seconds=14400,
        final_reply_wait_seconds=28800,
    )
    with TestClient(app) as client:
        registry = app.state.registry
        asyncio.run(
            registry.graph_repository().upsert_node(
                Developer(tenant_id=settings.tenant_id, id="dev-noah", name="Noah")
            )
        )
        asyncio.run(registry.status_repository().record_checkin_preference(stored))
        before = client.get("/me/checkin-preference")
        days_only = client.put("/me/checkin-preference", json={"weekdays": [0, 1, 2, 3]})
        zone_only = client.put("/me/checkin-preference", json={"timezone": "Asia/Kolkata"})
        after = asyncio.run(
            registry.status_repository().checkin_preference_for(settings.tenant_id, "dev-noah")
        )

    assert before.json() == {
        "developer_id": "dev-noah",
        "local_time": "08:05:00",
        "timezone": "Europe/Berlin",
        "weekdays": [4, 0, 1],
        "reply_wait_seconds": 14400,
        "final_reply_wait_seconds": 28800,
        "inherited": [],
        "defaults": {
            "local_time": "09:30:00",
            "timezone": settings.tenant_default_timezone,
            "weekdays": [0, 1, 2, 3, 4],
            "reply_wait_seconds": settings.checkin_reply_wait_seconds,
            "final_reply_wait_seconds": settings.checkin_final_reply_wait_seconds,
        },
        # Noah's stored 08:05 is not when he is asked: the send is everyone's.
        "send": {
            "kind": "weekly",
            "cron": "30 9 * * 1-5",
            "timezone": "UTC",
            "local_time": "09:30:00",
            "weekdays": [0, 1, 2, 3, 4],
            "month_days": None,
            "months": None,
        },
    }
    assert days_only.status_code == 200
    assert zone_only.status_code == 200
    assert after == CheckInPreference(
        tenant_id=settings.tenant_id,
        developer_id="dev-noah",
        local_time=time_of_day(8, 5),
        timezone="Asia/Kolkata",
        weekdays=(0, 1, 2, 3),
        reply_wait_seconds=14400,
        final_reply_wait_seconds=28800,
    )


def test_first_checkin_preference_save_stores_only_what_was_set(settings: Settings) -> None:
    # A first save used to copy the team defaults into the member's row, so a
    # later change to the defaults never reached them. Each path -- the
    # member's own, the admin's, and write-back consent -- now stores only
    # what it was given.
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "admin,dev", "dev_principal_subject": "dev-asha"}
        )
    )
    with TestClient(app) as client:
        for member_id, name in (("dev-asha", "Asha"), ("dev-ada", "Ada"), ("dev-lin", "Lin")):
            client.post("/config/members", json={"id": member_id, "name": name})
        own = client.put("/me/checkin-preference", json={"weekdays": [0, 2, 4]})
        admin = client.put(
            "/config/members/dev-ada/checkin-preference",
            json={"reply_wait_seconds": 3600},
        )
        consent = client.put(
            "/config/members/dev-lin/writeback-consent",
            json={"consent": "never"},
        )
        repository = app.state.registry.status_repository()
        stored = {
            member_id: asyncio.run(repository.checkin_preference_for(settings.tenant_id, member_id))
            for member_id in ("dev-asha", "dev-ada", "dev-lin")
        }
        lin = client.get("/config/members/dev-lin/checkin-preference")

    assert own.status_code == 200
    assert admin.status_code == 200
    assert consent.status_code == 200
    assert stored["dev-asha"] == CheckInPreference(
        tenant_id=settings.tenant_id, developer_id="dev-asha", weekdays=(0, 2, 4)
    )
    assert stored["dev-ada"] == CheckInPreference(
        tenant_id=settings.tenant_id, developer_id="dev-ada", reply_wait_seconds=3600
    )
    assert stored["dev-lin"] == CheckInPreference(
        tenant_id=settings.tenant_id,
        developer_id="dev-lin",
        write_back_consent=WriteBackConsent.NEVER,
    )
    assert admin.json()["inherited"] == [
        "local_time",
        "timezone",
        "weekdays",
        "final_reply_wait_seconds",
    ]
    assert len(lin.json()["inherited"]) == 5


def test_checkin_preference_follows_later_changes_to_the_defaults(settings: Settings) -> None:
    app = create_app(settings=settings)
    path = "/config/members/dev-ada/checkin-preference"
    with TestClient(app) as client:
        client.post("/config/members", json={"id": "dev-ada", "name": "Ada"})
        client.put(path, json={"timezone": "Europe/Berlin", "reply_wait_seconds": 3600})
        # The deployment's defaults change after the member's first save.
        app.state.settings = settings.model_copy(
            update={
                "tenant_default_timezone": "Asia/Kolkata",
                "checkin_reply_wait_seconds": 600,
                "checkin_final_reply_wait_seconds": 1200,
                "checkin_fanout_cron": "45 8 * * *",
            }
        )
        member = client.get(path)
        listed = client.get("/config/checkin-preferences")
        # A schedule paused to one date a year, then one no words can say.
        app.state.settings = settings.model_copy(update={"checkin_fanout_cron": "0 0 1 1 *"})
        yearly = client.get(path)
        app.state.settings = settings.model_copy(update={"checkin_fanout_cron": "*/15 * * * *"})
        stepped = client.get(path)

    assert member.status_code == 200
    body = member.json()
    # What was set for the member stays; everything else is the new default.
    assert body["timezone"] == "Europe/Berlin"
    assert body["reply_wait_seconds"] == 3600
    assert body["final_reply_wait_seconds"] == 1200
    assert body["inherited"] == ["local_time", "weekdays", "final_reply_wait_seconds"]
    assert body["defaults"]["timezone"] == "Asia/Kolkata"
    assert body["defaults"]["reply_wait_seconds"] == 600
    # The send is the deployment's one schedule, in UTC, never the team's zone.
    assert body["send"] == {
        "kind": "weekly",
        "cron": "45 8 * * *",
        "timezone": "UTC",
        "local_time": "08:45:00",
        "weekdays": [0, 1, 2, 3, 4, 5, 6],
        "month_days": None,
        "months": None,
    }
    assert next(item for item in listed.json() if item["developer_id"] == "dev-ada") == body
    # 1 January only has no days of the week, so nothing can read it as every day.
    assert yearly.json()["send"] == {
        "kind": "dates",
        "cron": "0 0 1 1 *",
        "timezone": "UTC",
        "local_time": "00:00:00",
        "weekdays": None,
        "month_days": [1],
        "months": [1],
    }
    assert stepped.json()["send"] == {
        "kind": "other",
        "cron": "*/15 * * * *",
        "timezone": "UTC",
        "local_time": None,
        "weekdays": None,
        "month_days": None,
        "months": None,
    }


def test_checkin_preference_field_sent_as_null_goes_back_to_the_default(
    settings: Settings,
) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "admin,dev", "dev_principal_subject": "dev-noah"}
        )
    )
    stored = CheckInPreference(
        tenant_id=settings.tenant_id,
        developer_id="dev-noah",
        local_time=time_of_day(8, 5),
        timezone="Europe/Berlin",
        weekdays=(4, 0, 1),
        reply_wait_seconds=60,
        final_reply_wait_seconds=120,
        write_back_consent=WriteBackConsent.AUTO_APPLY,
    )
    with TestClient(app) as client:
        registry = app.state.registry
        asyncio.run(
            registry.graph_repository().upsert_node(
                Developer(tenant_id=settings.tenant_id, id="dev-noah", name="Noah")
            )
        )
        asyncio.run(registry.status_repository().record_checkin_preference(stored))
        admin = client.put(
            "/config/members/dev-noah/checkin-preference",
            json={"reply_wait_seconds": None, "local_time": None},
        )
        own = client.put("/me/checkin-preference", json={"timezone": None, "weekdays": None})
        after = asyncio.run(
            registry.status_repository().checkin_preference_for(settings.tenant_id, "dev-noah")
        )

    assert admin.status_code == 200
    assert admin.json()["reply_wait_seconds"] == settings.checkin_reply_wait_seconds
    assert admin.json()["inherited"] == ["local_time", "reply_wait_seconds"]
    assert own.status_code == 200
    assert own.json()["timezone"] == settings.tenant_default_timezone
    assert own.json()["weekdays"] == [0, 1, 2, 3, 4]
    assert own.json()["inherited"] == ["local_time", "timezone", "weekdays", "reply_wait_seconds"]
    # Only the cleared fields changed; the final wait and consent are kept.
    assert after == CheckInPreference(
        tenant_id=settings.tenant_id,
        developer_id="dev-noah",
        final_reply_wait_seconds=120,
        write_back_consent=WriteBackConsent.AUTO_APPLY,
    )


def test_checkin_preference_refuses_an_empty_list_of_days(settings: Settings) -> None:
    # With no days the bot never asks that person again, so an empty list is a
    # 422 on both endpoints rather than a silent stop to their check-ins.
    app = create_app(
        settings=settings.model_copy(
            update={"dev_principal_roles": "admin,dev", "dev_principal_subject": "dev-noah"}
        )
    )
    stored = CheckInPreference(
        tenant_id=settings.tenant_id,
        developer_id="dev-noah",
        timezone="Europe/Berlin",
        weekdays=(0, 2, 4),
    )
    with TestClient(app) as client:
        registry = app.state.registry
        asyncio.run(
            registry.graph_repository().upsert_node(
                Developer(tenant_id=settings.tenant_id, id="dev-noah", name="Noah")
            )
        )
        asyncio.run(registry.status_repository().record_checkin_preference(stored))
        own = client.put("/me/checkin-preference", json={"weekdays": []})
        admin = client.put("/config/members/dev-noah/checkin-preference", json={"weekdays": []})
        admin_with_zone = client.put(
            "/config/members/dev-noah/checkin-preference",
            json={"weekdays": [], "timezone": "Asia/Kolkata"},
        )
        after = asyncio.run(
            registry.status_repository().checkin_preference_for(settings.tenant_id, "dev-noah")
        )

    for response in (own, admin, admin_with_zone):
        assert response.status_code == 422
        assert "weekdays needs at least one day" in response.json()["detail"][0]["msg"]
    # Nothing was stored, not even the valid time zone sent beside the days.
    assert after == stored


def test_portfolio_heatmap_accepts_program_root_id(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "exec"}))
    with TestClient(app) as client:
        response = client.get(
            "/portfolio/heatmap?as_of=2026-06-15&program_root_id=program-platform"
        )

    assert response.status_code == 200
    assert response.json()["as_of"] == "2026-06-15"


def test_portfolio_attention_serves_exec_today_and_cells_say_why(settings: Settings) -> None:
    """Exec Today's headline and signals; every heat cell's reason beside its colour."""
    exec_app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "exec"}))
    with TestClient(exec_app) as client:
        _populate_graph_fixture(exec_app, settings)
        attention = client.get(
            "/portfolio/attention?as_of=2026-06-15&program_root_id=program-platform&tz=Asia/Kolkata"
        )
        bad_zone = client.get("/portfolio/attention?as_of=2026-06-15&tz=Not/AZone")
        heatmap = client.get("/portfolio/heatmap?as_of=2026-06-15&program_root_id=program-platform")
    dev_app = create_app(settings=settings.model_copy(update={"dev_principal_roles": "dev"}))
    with TestClient(dev_app) as client:
        denied = client.get("/portfolio/attention?as_of=2026-06-15")

    assert attention.status_code == 200
    body = attention.json()
    assert body["program_id"] == "program-platform"
    assert body["headline"].startswith(f"{body['rag'].capitalize()}:") or body[
        "headline"
    ].startswith("No status yet")
    assert len(body["signals"]) <= 5
    assert all(
        set(signal) >= {"kind", "severity", "title", "age_days", "link"}
        for signal in body["signals"]
    )
    assert set(body["checkins"]) == {"people", "asked", "answered", "first_asked_at"}
    # A zone that does not exist reads as UTC, never as a failed read.
    assert bad_zone.status_code == 200
    cells = heatmap.json()["cells"]
    assert cells and all(cell["reason"] for cell in cells)
    assert all(isinstance(cell["reasons"], list) for cell in cells)
    # The read names who has not answered and whose blockers are open: aggregate
    # roles only, like the heat map and the portfolio risks it is built from.
    assert denied.status_code == 403


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
        workstream_link = client.post("/config/projects/project-alpha/workstreams/workstream-alpha")
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
        project_workstreams = client.get(f"/projects/project-alpha/workstreams?as_of={as_of}")
        pods = client.get(f"/pods?as_of={as_of}")
        checkins = client.get(f"/pods/pod-alpha/checkins?as_of={as_of}")
        progress = client.get(f"/projects/project-alpha/progress?as_of={as_of}")
        workstream_progress = client.get(f"/workstreams/workstream-alpha/progress?as_of={as_of}")
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


def test_directory_names_workstream_people_for_every_role_that_opens_delivery(
    settings: Settings,
) -> None:
    """The member list is admin-only, so the directory carries the names.

    Delivery shows a workstream's owner, TPM and SM to every role that opens it.
    Without names in the directory response those roles could only show ids.
    """
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    with TestClient(app) as client:
        client.post("/config/members", json={"id": "dev-ada", "name": "Ada Lovelace"})
        client.post("/config/members", json={"id": "dev-ira", "name": "Ira Novak"})
        client.put("/config/members/dev-ira/identity-link", json={"chat_user_id": "U2006"})
        created = client.post(
            "/config/workstreams",
            json={
                "id": "workstream-alpha",
                "name": "Runtime Config Admin",
                "metadata": {"owner_id": "dev-ada", "tpm_id": "U2006", "sm_id": "U1002"},
            },
        )
        # Work in it, so it is in use and listed (an empty one is not).
        client.post(
            "/config/work-items",
            json={"id": "wi-alpha", "name": "Alpha", "workstream_id": "workstream-alpha"},
        )
        by_role = {}
        for role in ("exec", "mgr", "po", "sm"):
            # What the console sends while acting as someone in that role.
            headers = {"x-openprogram-dev-user": "dev-ada", "x-openprogram-dev-roles": role}
            by_role[role] = (
                client.get("/config/members", headers=headers).status_code,
                client.get("/workstreams", headers=headers),
                client.get("/workstreams/workstream-alpha", headers=headers),
            )

    assert created.status_code == 201
    expected = [
        {"key": "owner_id", "id": "dev-ada", "member_id": "dev-ada", "name": "Ada Lovelace"},
        {"key": "tpm_id", "id": "U2006", "member_id": "dev-ira", "name": "Ira Novak"},
        {"key": "sm_id", "id": "U1002", "member_id": None, "name": None},
    ]
    for role, (members_status, listing, detail) in by_role.items():
        assert members_status == 403, role
        assert listing.status_code == 200, role
        assert listing.json()[0]["people"] == expected, role
        assert detail.json()["people"] == expected, role
        # The stored ids are untouched, so the admin form still round-trips them.
        assert listing.json()[0]["metadata"]["tpm_id"] == "U2006", role


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
                "github_repos": ["oneai/openprogram", "oneai/api", "oneai/api"],
            },
        )
        updated_project = client.put(
            "/config/projects/project-alpha",
            json={"jira_base_jql": 'labels = "alpha"', "github_repos": "oneai/openprogram"},
        )
        fetched_project = client.get("/config/projects/project-alpha")
        pod = client.post(
            "/config/pods",
            json={
                "id": "pod-alpha",
                "name": "Alpha Pod",
                "jira_filter_jql": "component = API",
                "github_repos": ["oneai/openprogram"],
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
    assert project.json()["github_repos"] == ["oneai/openprogram", "oneai/api"]
    assert updated_project.status_code == 200
    assert updated_project.json()["jira_base_jql"] == 'labels = "alpha"'
    assert updated_project.json()["github_repos"] == ["oneai/openprogram"]
    assert fetched_project.status_code == 200
    assert fetched_project.json()["metadata"]["github_repos"] == "oneai/openprogram"
    assert pod.status_code == 201
    assert pod.json()["jira_filter_jql"] == "component = API"
    assert pod.json()["github_repos"] == ["oneai/openprogram"]
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


def _container_slack_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "runtime_mode": "container",
            "chat_provider": "slack",
            "slack_bot_token": "xoxb-test",
            "slack_signing_secret": "signing-secret",
        }
    )


def _slack_json_body(payload: object) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _signed_slack_headers(
    body: bytes,
    *,
    timestamp: str | None = None,
    secret: str = "signing-secret",
) -> dict[str, str]:
    timestamp_value = timestamp or str(int(time.time()))
    signature_base = b"v0:" + timestamp_value.encode("utf-8") + b":" + body
    digest = hmac.new(
        secret.encode("utf-8"),
        signature_base,
        hashlib.sha256,
    ).hexdigest()
    return {
        "content-type": "application/json",
        "x-slack-request-timestamp": timestamp_value,
        "x-slack-signature": f"v0={digest}",
    }


def _seed_applied_writeback(
    app: FastAPI,
    *,
    audit_id: str,
    issue_key: str,
    correlation_id: str,
    before_state: str,
    after_state: str,
    created_at: datetime,
) -> None:
    asyncio.run(
        app.state.registry.writeback_audit_repository().record(
            WriteBackAudit(
                id=audit_id,
                tenant_id="demo",
                developer_id="dev-1",
                issue_key=issue_key,
                correlation_id=correlation_id,
                status=WriteBackStatus.APPLIED,
                target_state=after_state,
                before_state=before_state,
                after_state=after_state,
                comment="done via check-in",
                source="checkin",
                created_at=created_at,
            )
        )
    )


def test_revert_writeback_route_reverts_then_is_idempotent(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        registry = app.state.registry
        # Inject a writable tracker so the sanctioned revert transition succeeds.
        registry._issue_tracker = FakeIssueTracker(
            issues={
                "PO-1": Issue(
                    tenant_id="demo",
                    key="PO-1",
                    title="Wire write-back",
                    state=IssueState.DONE,
                )
            }
        )
        _seed_applied_writeback(
            app,
            audit_id="wb-1",
            issue_key="PO-1",
            correlation_id="corr-1",
            before_state="in_progress",
            after_state="done",
            created_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
        )

        # Off, nothing writes to the tracker, a revert neither; on, it reverts once.
        off = client.post("/admin/ops/writeback/wb-1/revert")
        assert client.put("/config/tenant/writeback", json={"enabled": True}).status_code == 200
        first = client.post("/admin/ops/writeback/wb-1/revert")
        second = client.post("/admin/ops/writeback/wb-1/revert")
        missing = client.post("/admin/ops/writeback/does-not-exist/revert")

    assert off.status_code == 409
    assert off.json()["detail"] == (
        "Jira writes are off for this tenant. Nothing was reverted in the issue tracker."
    )
    assert first.status_code == 200
    body = first.json()
    assert body["issue_key"] == "PO-1"
    assert body["from_state"] == "done"
    assert body["to_state"] == "in_progress"
    assert body["status"] == "reverted"

    # A second revert of the same applied write must not double-apply.
    assert second.status_code == 409
    assert missing.status_code == 404


def test_writeback_adoption_endpoint_counts_applied_writes(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        _seed_applied_writeback(
            app,
            audit_id="wb-1",
            issue_key="PO-1",
            correlation_id="corr-1",
            before_state="in_progress",
            after_state="done",
            created_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
        )
        _seed_applied_writeback(
            app,
            audit_id="wb-2",
            issue_key="PO-2",
            correlation_id="corr-2",
            before_state="todo",
            after_state="in_progress",
            created_at=datetime(2026, 1, 11, 9, 0, tzinfo=UTC),
        )
        response = client.get("/persona/writeback-adoption")

    assert response.status_code == 200
    body = response.json()
    assert body["applied_count"] == 2
    # Newest-first, identifier-only (no developer note / DM content leaked).
    assert [entry["issue_key"] for entry in body["recent"]] == ["PO-2", "PO-1"]
    assert body["recent"][0]["to_state"] == "in_progress"
    assert "comment" not in body["recent"][0]
    assert "note" not in body["recent"][0]


def test_writeback_adoption_endpoint_requires_aggregate_scope() -> None:
    non_admin = Settings(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        runtime_mode="memory",
        dev_principal_roles="dev",
    )
    app = create_app(settings=non_admin)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/persona/writeback-adoption")
    assert response.status_code == 403


def _populate_graph_fixture(app: FastAPI, settings: Settings) -> None:
    asyncio.run(
        populate_demo_graph(
            app.state.registry.graph_repository(),
            app.state.registry.time_series_repository(),
            settings.tenant_id,
        )
    )
