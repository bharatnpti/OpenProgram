from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.status import StatusSource


def test_chat_simulator_routes_return_404_when_disabled(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={
                "chat_provider": "mock_slack",
                "chat_simulator_enabled": False,
            }
        )
    )

    with TestClient(app) as client:
        response = client.get("/test/chat-simulator/status")

    assert response.status_code == 404


def test_chat_simulator_routes_are_admin_only(settings: Settings) -> None:
    app = create_app(
        settings=settings.model_copy(
            update={
                "chat_provider": "mock_slack",
                "chat_simulator_enabled": True,
                "dev_principal_roles": "dev",
            }
        )
    )

    with TestClient(app) as client:
        response = client.get("/test/chat-simulator/status")

    assert response.status_code == 403


def test_chat_simulator_reply_processes_checkin(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "chat_provider": "mock_slack",
            "issue_tracker_provider": "fake",
            "llm_provider": "fake",
            "chat_simulator_enabled": True,
        }
    )
    app = create_app(settings=configured)
    with TestClient(app) as client:
        asyncio.run(
            app.state.registry.status_collector().start_checkin(
                tenant_id="demo",
                developer_id="dev-asha",
                developer_name="Asha Rao",
                chat_external_id="U1001",
                correlation_id="sim-route-corr",
                asked_at=datetime(2026, 1, 13, 9, 30, tzinfo=UTC),
            )
        )
        messages_response = client.get("/test/chat-simulator/messages")
        bot_message = messages_response.json()["items"][0]
        reply_response = client.post(
            f"/test/chat-simulator/messages/{bot_message['message_id']}/reply",
            json={
                "text": "Finished API shell; no blockers.",
                "received_at": "2026-01-13T09:35:00+00:00",
            },
        )
        status = asyncio.run(
            app.state.registry.status_repository().latest_developer_status(
                "demo",
                "dev-asha",
                date(2026, 1, 13),
            )
        )

    assert messages_response.status_code == 200
    assert bot_message["direction"] == "bot"
    assert bot_message["user_id"] == "U1001"
    assert bot_message["purpose"] == "status_checkin"
    assert reply_response.status_code == 200
    assert reply_response.json()["status"] == "processed"
    assert status is not None
    assert status.source is StatusSource.CONFIRMED


def test_chat_simulator_reset_clears_messages(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "chat_provider": "mock_slack",
            "issue_tracker_provider": "fake",
            "llm_provider": "fake",
            "chat_simulator_enabled": True,
        }
    )
    app = create_app(settings=configured)
    with TestClient(app) as client:
        asyncio.run(
            app.state.registry.status_collector().start_checkin(
                tenant_id="demo",
                developer_id="dev-asha",
                chat_external_id="U1001",
                correlation_id="sim-reset-corr",
                asked_at=datetime(2026, 1, 13, 9, 30, tzinfo=UTC),
            )
        )
        assert client.get("/test/chat-simulator/messages").json()["items"]
        reset_response = client.delete("/test/chat-simulator/state")
        messages_response = client.get("/test/chat-simulator/messages")

    assert reset_response.status_code == 204
    assert messages_response.json() == {"items": []}


def test_mock_chat_webhook_route_processes_slack_shaped_reply(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "chat_provider": "mock_slack",
            "issue_tracker_provider": "fake",
            "llm_provider": "fake",
            "chat_simulator_enabled": True,
        }
    )
    app = create_app(settings=configured)
    asked_at = datetime.now(tz=UTC)
    message_id = f"{int(asked_at.timestamp())}.000001"
    with TestClient(app) as client:
        asyncio.run(
            app.state.registry.status_collector().start_checkin(
                tenant_id="demo",
                developer_id="dev-asha",
                chat_external_id="U1001",
                correlation_id="mock-webhook-corr",
                asked_at=asked_at,
            )
        )
        response = client.post(
            "/webhooks/chat/mock_slack",
            json={
                "event": {
                    "user": "U1001",
                    "channel": "D1001",
                    "text": "Finished API shell; no blockers.",
                    "ts": message_id,
                }
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "processed",
        "message_id": message_id,
    }
