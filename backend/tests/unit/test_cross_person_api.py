from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry


def test_portfolio_cross_person_requests_visible_to_aggregate_roles(settings: Settings) -> None:
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.OPEN))
    app = _app_for_role(settings, "exec", "exec-user", store)

    with TestClient(app) as client:
        response = client.get("/portfolio/cross-person-requests?status=open")

    assert response.status_code == 200
    body = response.json()
    assert len(body["requests"]) == 1
    request = body["requests"][0]
    assert request["id"] == "xreq-1"
    assert request["status"] == "open"
    assert request["counterpart_email"] == "alice@example.com"
    assert "notify_message_id" not in request
    assert "notify_correlation_id" not in request


def test_my_cross_person_requests_returns_counterpart_inbox(settings: Settings) -> None:
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.ACKNOWLEDGED))
    app = _app_for_role(settings, "dev", "U-alice", store)

    with TestClient(app) as client:
        response = client.get("/me/cross-person-requests")

    assert response.status_code == 200
    body = response.json()
    assert [request["id"] for request in body["requests"]] == ["xreq-1"]
    assert body["requests"][0]["status"] == "acknowledged"


def test_update_cross_person_request_status(settings: Settings) -> None:
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.OPEN))
    app = _app_for_role(settings, "admin", "admin-user", store)

    with TestClient(app) as client:
        response = client.post(
            "/cross-person-requests/xreq-1/status",
            json={"status": "acknowledged"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "xreq-1"
    assert body["status"] == "acknowledged"
    stored = asyncio.run(store.get("demo", "xreq-1"))
    assert stored is not None
    assert stored.status is CrossPersonRequestStatus.ACKNOWLEDGED


def test_update_cross_person_request_status_denies_unrelated_developer(
    settings: Settings,
) -> None:
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.OPEN))
    app = _app_for_role(settings, "dev", "U-someone-else", store)

    with TestClient(app) as client:
        response = client.post(
            "/cross-person-requests/xreq-1/status",
            json={"status": "acknowledged"},
        )

    assert response.status_code == 403


def _app_for_role(
    settings: Settings,
    role: str,
    subject: str,
    store: InMemoryGraphStore,
) -> FastAPI:
    role_settings = settings.model_copy(
        update={"dev_principal_roles": role, "dev_principal_subject": subject}
    )
    registry = ServiceRegistry(role_settings, graph_store=store)
    return create_app(settings=role_settings, registry=registry)


async def _seed_request(
    store: InMemoryGraphStore,
    *,
    status: CrossPersonRequestStatus,
) -> None:
    await store.create(
        CrossPersonRequest(
            tenant_id="demo",
            id="xreq-1",
            requester_id="dev-1",
            requester_chat_ref="U-dev",
            counterpart_id="U-alice",
            kind=CrossPersonRequestKind.REVIEW,
            note="API schema review",
            source_correlation_id="corr-1",
            status=status,
            created_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
            updated_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
            raw_name="Alice Chen",
            email="alice@example.com",
            counterpart_display_name="Alice Chen",
            counterpart_email="alice@example.com",
            notify_message_id="msg-U-alice-1",
            notify_correlation_id="xreq-xreq-1",
        )
    )
