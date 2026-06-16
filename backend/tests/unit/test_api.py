from __future__ import annotations

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
