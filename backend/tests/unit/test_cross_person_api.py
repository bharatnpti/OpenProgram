from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.graph import FactEvent
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


def test_update_cross_person_request_status(settings: Settings) -> None:
    """The person asked acknowledges the request: it is theirs to take on."""
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.OPEN))
    app = _app_for_role(settings, "dev", "U-alice", store)

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


# Every role that reads the portfolio board, and people who are on no request.
_NOT_ON_THE_REQUEST = [
    ("admin", "admin-user"),
    ("mgr", "mgr-user"),
    ("exec", "exec-user"),
    ("sm", "sm-user"),
    ("po", "po-user"),
    ("dev", "U-someone-else"),
]


@pytest.mark.parametrize(
    ("role", "subject"),
    # The requester waits on the ask; only the person asked takes it on.
    [*_NOT_ON_THE_REQUEST, ("dev", "dev-1")],
)
def test_nobody_but_the_person_asked_acknowledges_a_request(
    settings: Settings, role: str, subject: str
) -> None:
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.OPEN))
    app = _app_for_role(settings, role, subject, store)

    with TestClient(app) as client:
        response = client.post(
            "/cross-person-requests/xreq-1/status",
            json={"status": "acknowledged"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Only the person this request asks can acknowledge it."
    stored = asyncio.run(store.get("demo", "xreq-1"))
    assert stored is not None
    assert stored.status is CrossPersonRequestStatus.OPEN
    assert asyncio.run(_status_facts(store)) == []


@pytest.mark.parametrize("subject", ["U-alice", "dev-1"])
def test_the_person_asked_or_the_requester_resolves_a_request(
    settings: Settings, subject: str
) -> None:
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.ACKNOWLEDGED))
    app = _app_for_role(settings, "dev", subject, store)

    with TestClient(app) as client:
        response = client.post(
            "/cross-person-requests/xreq-1/status",
            json={"status": "resolved"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    stored = asyncio.run(store.get("demo", "xreq-1"))
    assert stored is not None
    assert stored.status is CrossPersonRequestStatus.RESOLVED


@pytest.mark.parametrize(("role", "subject"), _NOT_ON_THE_REQUEST)
def test_nobody_else_resolves_a_request_whatever_their_role_reads(
    settings: Settings, role: str, subject: str
) -> None:
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.OPEN))
    app = _app_for_role(settings, role, subject, store)

    with TestClient(app) as client:
        response = client.post(
            "/cross-person-requests/xreq-1/status",
            json={"status": "resolved"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Only the person this request asks, or the person who raised it, can resolve it."
    )
    stored = asyncio.run(store.get("demo", "xreq-1"))
    assert stored is not None
    assert stored.status is CrossPersonRequestStatus.OPEN
    assert asyncio.run(_status_facts(store)) == []


@pytest.mark.parametrize("status", ["open", "needs_resolution", "dismissed"])
def test_statuses_openprogram_records_are_not_set_by_hand(settings: Settings, status: str) -> None:
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.ACKNOWLEDGED))
    app = _app_for_role(settings, "dev", "U-alice", store)

    with TestClient(app) as client:
        response = client.post("/cross-person-requests/xreq-1/status", json={"status": status})

    assert response.status_code == 422
    stored = asyncio.run(store.get("demo", "xreq-1"))
    assert stored is not None
    assert stored.status is CrossPersonRequestStatus.ACKNOWLEDGED


def test_each_status_change_names_who_made_it_and_when(settings: Settings) -> None:
    store = InMemoryGraphStore()
    asyncio.run(_seed_request(store, status=CrossPersonRequestStatus.OPEN))

    with TestClient(_app_for_role(settings, "dev", "U-alice", store)) as client:
        assert client.post(
            "/cross-person-requests/xreq-1/status", json={"status": "acknowledged"}
        ).is_success
    acknowledged = asyncio.run(store.get("demo", "xreq-1"))
    with TestClient(_app_for_role(settings, "dev", "dev-1", store)) as client:
        assert client.post(
            "/cross-person-requests/xreq-1/status", json={"status": "resolved"}
        ).is_success
    resolved = asyncio.run(store.get("demo", "xreq-1"))

    assert acknowledged is not None and resolved is not None
    facts = asyncio.run(_status_facts(store))
    assert len(facts) == 2
    assert {
        fact.payload["transition"]: (fact.payload["changed_by"], fact.observed_at) for fact in facts
    } == {
        "acknowledged": ("U-alice", acknowledged.updated_at),
        "resolved": ("dev-1", resolved.updated_at),
    }
    assert acknowledged.updated_at > _ASKED_AT


def test_an_unmatched_request_is_resolved_by_its_requester_and_acknowledged_by_nobody(
    settings: Settings,
) -> None:
    """Nobody is asked yet, so there is no one to acknowledge it."""
    store = InMemoryGraphStore()
    asyncio.run(
        _seed_request(store, status=CrossPersonRequestStatus.NEEDS_RESOLUTION, counterpart_id=None)
    )

    with TestClient(_app_for_role(settings, "dev", "dev-1", store)) as client:
        acknowledged = client.post(
            "/cross-person-requests/xreq-1/status", json={"status": "acknowledged"}
        )
        resolved = client.post("/cross-person-requests/xreq-1/status", json={"status": "resolved"})

    assert acknowledged.status_code == 403
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"


def test_an_unknown_request_is_not_found(settings: Settings) -> None:
    store = InMemoryGraphStore()
    app = _app_for_role(settings, "dev", "U-alice", store)

    with TestClient(app) as client:
        response = client.post(
            "/cross-person-requests/xreq-missing/status", json={"status": "resolved"}
        )

    assert response.status_code == 404


async def _status_facts(store: InMemoryGraphStore) -> list[FactEvent]:
    """The transitions recorded for the request since it was seeded."""
    facts = await store.list_recent_facts("demo", sources=("cross_person_request",))
    return [fact for fact in facts if fact.payload.get("request_id") == "xreq-1"]


def _app_for_role(
    settings: Settings,
    role: str,
    subject: str,
    store: InMemoryGraphStore,
) -> FastAPI:
    role_settings = settings.model_copy(
        update={
            "dev_principal_roles": role,
            "dev_principal_subject": subject,
            # A resolution tells the requester; the fake keeps that offline.
            "chat_provider": "fake",
        }
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


_ASKED_AT = datetime(2026, 1, 10, 9, 10, tzinfo=UTC)


async def _seed_request(
    store: InMemoryGraphStore,
    *,
    status: CrossPersonRequestStatus,
    counterpart_id: str | None = "U-alice",
) -> None:
    await store.create(
        CrossPersonRequest(
            tenant_id="demo",
            id="xreq-1",
            requester_id="dev-1",
            requester_chat_ref="U-dev",
            counterpart_id=counterpart_id,
            kind=CrossPersonRequestKind.REVIEW,
            note="API schema review",
            source_correlation_id="corr-1",
            status=status,
            created_at=_ASKED_AT,
            updated_at=_ASKED_AT,
            raw_name="Alice Chen",
            email="alice@example.com",
            counterpart_display_name="Alice Chen",
            counterpart_email="alice@example.com",
            notify_message_id="msg-U-alice-1",
            notify_correlation_id="xreq-xreq-1",
        )
    )
