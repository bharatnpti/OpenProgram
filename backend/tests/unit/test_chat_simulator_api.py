from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import ValidationError

from api.main import create_app
from config.settings import Settings
from core.domain.cross_person import CrossPersonRequestResolution, CrossPersonRequestStatus
from core.domain.status import CrossPersonMention, StatusSource
from infra.registry import ServiceRegistry


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


def test_persona_switching_is_off_unless_demo_mode_is_on(settings: Settings) -> None:
    """The acting-as headers are inert by default.

    Without ``demo_mode`` the roster is empty and the impersonation headers are
    ignored, so a request is served as the configured dev principal and never as
    whoever the caller named.
    """
    base = settings.model_copy(update={"dev_principal_subject": "U1001"})
    headers = {"x-openprogram-dev-user": "U1002", "x-openprogram-dev-roles": "dev"}

    with TestClient(create_app(settings=base)) as client:
        off_users = client.get("/api/v1/auth/dev-users")
        off_status = client.get("/api/v1/auth/status", headers=headers)

    assert off_users.json() == {"items": []}
    assert off_status.json()["demo_mode"] is False
    assert off_status.json()["user"]["subject"] == "U1001", "header must be ignored"

    with TestClient(create_app(settings=base.model_copy(update={"demo_mode": True}))) as client:
        on_status = client.get("/api/v1/auth/status", headers=headers)

    assert on_status.json()["demo_mode"] is True
    assert on_status.json()["user"]["subject"] == "U1002"


def test_demo_mode_is_refused_outside_a_local_dev_auth_tenant(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        settings.model_copy(update={"demo_mode": True}).model_validate(
            {**settings.model_dump(), "demo_mode": True, "environment": "staging"}
        )


def test_chat_simulator_scopes_a_developer_to_their_own_thread(settings: Settings) -> None:
    """A developer answers their own check-in; everything wider stays admin-only.

    The simulator stands in for the chat workspace, so a person must be able to
    read and reply in their own conversation. Reading the whole tenant
    transcript, speaking as someone else, or clearing history are config acts.
    """
    configured = settings.model_copy(
        update={
            "chat_provider": "mock_slack",
            "issue_tracker_provider": "fake",
            "llm_provider": "fake",
            "chat_simulator_enabled": True,
            "dev_principal_roles": "dev",
            "dev_principal_subject": "U1001",
        }
    )
    app = create_app(settings=configured)

    with TestClient(app) as client:
        # Whether the simulator exists at all is a feature flag, not a secret.
        assert client.get("/test/chat-simulator/status").status_code == 200

        assert client.get("/test/chat-simulator/messages").status_code == 403
        assert (
            client.get("/test/chat-simulator/messages", params={"user_id": "U1002"}).status_code
            == 403
        )
        assert (
            client.get("/test/chat-simulator/messages", params={"user_id": "U1001"}).status_code
            == 200
        )

        assert (
            client.post(
                "/test/chat-simulator/users/U1002/messages",
                json={"text": "speaking for someone else"},
            ).status_code
            == 403
        )
        own_post = client.post(
            "/test/chat-simulator/users/U1001/messages",
            json={"text": "Shipped the API shell; no blockers."},
        )
        assert own_post.status_code != 403, own_post.text

        assert client.delete("/test/chat-simulator/state").status_code == 403
        assert (
            client.post(
                "/test/chat-simulator/messages/1.000001/reply",
                json={"text": "replying as the tenant admin would"},
            ).status_code
            == 403
        )


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


# ---------------------------------------------------------------------------
# Replying in the thread of a cross-person request DM
#
# A request DM ends "Reply in this thread". The composer's plain send opens a
# check-in instead, so the reply became the counterpart's own status update and
# the request stayed open. A threaded send carries the DM's id as its thread and
# reaches the request through the same threaded-reply routing Slack uses.
# ---------------------------------------------------------------------------

_REQUESTER = "U1001"
_COUNTERPART = "U1002"


def _simulator_app(settings: Settings, **overrides: object) -> FastAPI:
    return create_app(
        settings=settings.model_copy(
            update={
                "chat_provider": "mock_slack",
                "issue_tracker_provider": "fake",
                "llm_provider": "fake",
                "chat_simulator_enabled": True,
                **overrides,
            }
        )
    )


def _raise_request(app: FastAPI) -> dict[str, object]:
    """Asha's check-in asks Liam for a review; returns the DM Liam received."""
    registry: ServiceRegistry = app.state.registry
    asyncio.run(
        registry.cross_person_request_service().record_from_checkin(
            tenant_id="demo",
            requester_id="dev-asha",
            requester_chat_ref=_REQUESTER,
            source_correlation_id="sim-thread-source",
            resolutions=(
                CrossPersonRequestResolution(
                    mention=CrossPersonMention(
                        raw_name="Liam Chen", kind="review", note="API schema review"
                    ),
                    status=CrossPersonRequestStatus.OPEN,
                    counterpart_id=_COUNTERPART,
                    counterpart_display_name="Liam Chen",
                ),
            ),
        )
    )
    dms = _bot_messages(app, _COUNTERPART, "cross_person_request")
    assert len(dms) == 1, dms
    return dms[0]


def _transcript(app: FastAPI) -> list[dict[str, object]]:
    messages = asyncio.run(app.state.registry.chat_simulator_messages())
    return [dict(message) for message in messages]


def _bot_messages(app: FastAPI, user_id: str, purpose: str) -> list[dict[str, object]]:
    return [
        message
        for message in _transcript(app)
        if message["direction"] == "bot"
        and message["user_id"] == user_id
        and message["purpose"] == purpose
    ]


def _request_status(app: FastAPI) -> CrossPersonRequestStatus:
    requests = asyncio.run(
        app.state.registry.cross_person_request_repository().list_for_counterpart(
            "demo", _COUNTERPART
        )
    )
    assert len(requests) == 1
    return requests[0].status


def _send(client: TestClient, user_id: str, text: str, thread_id: object = None) -> Response:
    body: dict[str, object] = {"text": text}
    if thread_id is not None:
        body["thread_id"] = thread_id
    return client.post(f"/test/chat-simulator/users/{user_id}/messages", json=body)


def test_thread_reply_to_a_request_dm_acknowledges_the_request(settings: Settings) -> None:
    # The counterpart is a plain developer answering their own conversation.
    app = _simulator_app(settings, dev_principal_roles="dev", dev_principal_subject=_COUNTERPART)
    dm = _raise_request(app)

    with TestClient(app) as client:
        response = _send(client, _COUNTERPART, "on it", thread_id=dm["message_id"])
        transcript = client.get(
            "/test/chat-simulator/messages", params={"user_id": _COUNTERPART}
        ).json()["items"]

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "acknowledged"
    assert response.json()["started_checkin"] is False
    assert _request_status(app) is CrossPersonRequestStatus.ACKNOWLEDGED
    reply = next(message for message in transcript if message["direction"] == "user")
    assert reply["text"] == "on it"
    assert reply["thread_id"] == dm["message_id"]
    # It carries no correlation id of the DM's, so it was the thread that routed it.
    assert reply["correlation_id"] is None
    # Nothing was opened for the counterpart's own status: the DM is all they hold.
    assert _bot_messages(app, _COUNTERPART, "status_checkin") == []


def test_thread_reply_saying_done_resolves_the_request_and_tells_the_requester_once(
    settings: Settings,
) -> None:
    app = _simulator_app(settings)
    dm = _raise_request(app)

    with TestClient(app) as client:
        done = _send(client, _COUNTERPART, "Done, reviewed and approved.", dm["message_id"])
        thanks = _send(client, _COUNTERPART, "thanks!", dm["message_id"])

    assert done.json()["status"] == "resolved"
    assert thanks.json()["status"] == "resolved"
    assert _request_status(app) is CrossPersonRequestStatus.RESOLVED
    told = _bot_messages(app, _REQUESTER, "cross_person_request_resolved")
    assert len(told) == 1
    assert "Liam Chen" in str(told[0]["text"])
    assert _bot_messages(app, _COUNTERPART, "status_checkin") == []


def test_unthreaded_message_still_goes_to_the_checkin_path(settings: Settings) -> None:
    app = _simulator_app(settings)
    _raise_request(app)

    with TestClient(app) as client:
        response = _send(client, _COUNTERPART, "Shipped the importer; no blockers.")

    assert response.status_code == 200, response.text
    assert response.json()["started_checkin"] is True
    assert len(_bot_messages(app, _COUNTERPART, "status_checkin")) == 1
    # Said in the channel, it is the person's own update and answers nothing.
    assert _request_status(app) is CrossPersonRequestStatus.OPEN
    replies = [message for message in _transcript(app) if message["direction"] == "user"]
    assert [reply["thread_id"] for reply in replies] == [None]


def test_thread_reply_leaves_an_open_checkin_question_open(settings: Settings) -> None:
    """Answering a request DM must not use up the check-in question above it."""
    app = _simulator_app(settings)
    asyncio.run(
        app.state.registry.status_collector().start_checkin(
            tenant_id="demo",
            developer_id="dev-liam",
            chat_external_id=_COUNTERPART,
            correlation_id="sim-thread-open-question",
        )
    )
    dm = _raise_request(app)
    question_id = asyncio.run(app.state.registry.open_chat_simulator_question(_COUNTERPART))
    assert question_id is not None

    with TestClient(app) as client:
        thread = _send(client, _COUNTERPART, "on it", dm["message_id"])
        still_open = asyncio.run(app.state.registry.open_chat_simulator_question(_COUNTERPART))
        plain = _send(client, _COUNTERPART, "Finished the importer; no blockers.")

    assert thread.json()["status"] == "acknowledged"
    assert still_open == question_id
    assert plain.json()["started_checkin"] is False


def test_thread_reply_under_a_message_that_is_not_a_request_is_kept_but_ignored(
    settings: Settings,
) -> None:
    """Only a request DM claims a thread reply; nothing else turns it into status."""
    app = _simulator_app(settings)
    asyncio.run(
        app.state.registry.status_collector().start_checkin(
            tenant_id="demo",
            developer_id="dev-liam",
            chat_external_id=_COUNTERPART,
            correlation_id="sim-thread-non-request",
        )
    )
    (question,) = _bot_messages(app, _COUNTERPART, "status_checkin")
    bot_messages_before = [m for m in _transcript(app) if m["direction"] == "bot"]

    with TestClient(app) as client:
        response = _send(client, _COUNTERPART, "Shipped the importer.", question["message_id"])

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ignored"
    assert response.json()["started_checkin"] is False
    transcript = _transcript(app)
    (reply,) = [m for m in transcript if m["direction"] == "user"]
    assert reply["thread_id"] == question["message_id"]
    # No new bot message: nothing was acknowledged, asked, or started.
    assert [m for m in transcript if m["direction"] == "bot"] == bot_messages_before
    # The check-in the question belongs to is still waiting for its real answer.
    checkin = asyncio.run(
        app.state.registry.status_repository().checkin_by_correlation(
            "demo", "sim-thread-non-request"
        )
    )
    assert checkin is not None
    assert checkin.replied_at is None
    assert (
        asyncio.run(app.state.registry.open_chat_simulator_question(_COUNTERPART))
        == (question["message_id"])
    )


def test_thread_reply_needs_a_bot_message_in_the_same_conversation(settings: Settings) -> None:
    app = _simulator_app(settings)
    dm = _raise_request(app)

    with TestClient(app) as client:
        unknown = _send(client, _COUNTERPART, "on it", "1.999999")
        someone_elses = _send(client, _REQUESTER, "on it", dm["message_id"])

    assert unknown.status_code == 404
    assert someone_elses.status_code == 404
    assert _request_status(app) is CrossPersonRequestStatus.OPEN
    assert [m for m in _transcript(app) if m["direction"] == "user"] == []
