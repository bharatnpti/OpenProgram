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
    assert request["delivery"] == "sent"
    assert "notify_message_id" not in request
    assert "notify_correlation_id" not in request
    assert "notify_attempts" not in request


def test_raised_requests_say_when_the_counterpart_dm_was_not_delivered(
    settings: Settings,
) -> None:
    """A requester whose ask never reached the other person needs to know."""
    store = InMemoryGraphStore()
    asyncio.run(_seed_undelivered_request(store, request_id="xreq-gave-up", next_attempt=False))
    asyncio.run(_seed_undelivered_request(store, request_id="xreq-retrying", next_attempt=True))
    app = _app_for_role(settings, "dev", "dev-1", store)

    with TestClient(app) as client:
        raised = client.get("/me/cross-person-requests?relation=raised").json()

    assert {request["id"]: request["delivery"] for request in raised["requests"]} == {
        "xreq-gave-up": "not_delivered",
        "xreq-retrying": "retrying",
    }


def test_portfolio_cross_person_requests_without_status_returns_every_active(
    settings: Settings,
) -> None:
    """No filter means all active, not just open.

    The three-column board asks for every active request in one call, so a
    default of OPEN left its Acknowledged and Needs-resolution columns unable
    to fill, and acknowledging a card made it vanish instead of move.
    """
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.ACKNOWLEDGED))
    app = _app_for_role(settings, "exec", "exec-user", store)

    with TestClient(app) as client:
        response = client.get("/portfolio/cross-person-requests")

    assert response.status_code == 200
    body = response.json()
    assert [request["id"] for request in body["requests"]] == ["xreq-1"]
    assert body["requests"][0]["status"] == "acknowledged"


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


def test_my_cross_person_requests_relation_filter(settings: Settings) -> None:
    """A requester needs to see whether their own ask landed.

    The board's columns are an inbox, so a request one raised appeared nowhere
    at all. `waiting` stays the default so the inbox behaviour is unchanged.
    """
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.OPEN))

    # dev-1 raised it; U-alice is the counterpart.
    requester = _app_for_role(settings, "dev", "dev-1", store)
    with TestClient(requester) as client:
        assert client.get("/me/cross-person-requests").json()["requests"] == []
        raised = client.get("/me/cross-person-requests?relation=raised").json()
        both = client.get("/me/cross-person-requests?relation=both").json()
    assert [request["id"] for request in raised["requests"]] == ["xreq-1"]
    assert [request["id"] for request in both["requests"]] == ["xreq-1"]

    counterpart = _app_for_role(settings, "dev", "U-alice", store)
    with TestClient(counterpart) as client:
        waiting = client.get("/me/cross-person-requests").json()
        raised_none = client.get("/me/cross-person-requests?relation=raised").json()
    assert [request["id"] for request in waiting["requests"]] == ["xreq-1"]
    assert raised_none["requests"] == []


def test_my_raised_requests_hold_an_ask_nobody_was_matched_to(settings: Settings) -> None:
    """An ask whose person was not matched waits on its requester, so it is theirs to see.

    It has no counterpart, so it is in no inbox. Listing only open and
    acknowledged asks told its requester "no open asks of others" for the one
    ask that was waiting on them to say who was meant. A closed ask still goes.
    """
    store = InMemoryGraphStore()
    for request_id, status in (
        ("xreq-unmatched", CrossPersonRequestStatus.NEEDS_RESOLUTION),
        ("xreq-resolved", CrossPersonRequestStatus.RESOLVED),
        ("xreq-dismissed", CrossPersonRequestStatus.DISMISSED),
    ):
        asyncio.run(_seed_unmatched_request(store, request_id=request_id, status=status))

    requester = _app_for_role(settings, "dev", "dev-1", store)
    with TestClient(requester) as client:
        raised = client.get("/me/cross-person-requests?relation=raised").json()
        both = client.get("/me/cross-person-requests?relation=both").json()
        waiting = client.get("/me/cross-person-requests").json()
    assert [request["id"] for request in raised["requests"]] == ["xreq-unmatched"]
    assert raised["requests"][0]["status"] == "needs_resolution"
    assert raised["requests"][0]["counterpart_id"] is None
    assert raised["requests"][0]["delivery"] is None
    assert [request["id"] for request in both["requests"]] == ["xreq-unmatched"]
    # Nobody was asked, so it is in no inbox: `waiting` is unchanged.
    assert waiting["requests"] == []

    someone_else = _app_for_role(settings, "dev", "dev-2", store)
    with TestClient(someone_else) as client:
        for relation in ("raised", "both", "waiting"):
            assert client.get(f"/me/cross-person-requests?relation={relation}").json() == {
                "requests": []
            }


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


async def _seed_undelivered_request(
    store: InMemoryGraphStore,
    *,
    request_id: str,
    next_attempt: bool,
) -> None:
    attempted_at = datetime(2026, 1, 10, 9, 10, tzinfo=UTC)
    await store.create(
        CrossPersonRequest(
            tenant_id="demo",
            id=request_id,
            requester_id="dev-1",
            requester_chat_ref="U-dev",
            counterpart_id="U-alice",
            kind=CrossPersonRequestKind.REVIEW,
            note="API schema review",
            source_correlation_id="corr-1",
            status=CrossPersonRequestStatus.OPEN,
            created_at=attempted_at,
            updated_at=attempted_at,
            counterpart_display_name="Alice Chen",
            notify_attempts=5 if not next_attempt else 2,
            notify_last_attempt_at=attempted_at,
            notify_next_attempt_at=attempted_at if next_attempt else None,
        )
    )


async def _seed_unmatched_request(
    store: InMemoryGraphStore,
    *,
    request_id: str,
    status: CrossPersonRequestStatus,
) -> None:
    """A request raised by dev-1 whose named person matched nobody: no counterpart, no DM."""
    await store.create(
        CrossPersonRequest(
            tenant_id="demo",
            id=request_id,
            requester_id="dev-1",
            requester_chat_ref="U-dev",
            counterpart_id=None,
            kind=CrossPersonRequestKind.REVIEW,
            note="Needs a reviewer assigned to the release MR",
            source_correlation_id="corr-2",
            status=status,
            created_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
            updated_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
            raw_name="reviewer",
        )
    )


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
