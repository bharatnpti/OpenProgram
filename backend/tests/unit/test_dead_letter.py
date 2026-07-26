from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.dead_letter_service import DeadLetterService
from core.domain.inbound import InboundChatEvent
from tests.contract.fakes import FakeDeadLetterRepository


async def test_dead_letter_service_record_is_idempotent_and_rearm_flips_status() -> None:
    service = DeadLetterService(FakeDeadLetterRepository())
    first_seen = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    now = datetime(2026, 1, 10, 13, 0, tzinfo=UTC)

    created = await service.record_inbound(
        tenant_id="demo",
        conversation_key="demo:conv-1",
        event_ids=("evt-1",),
        reason="retries exhausted",
        attempts=5,
        first_seen_at=first_seen,
        now=now,
    )
    # Same conversation_key + first_seen_at → deterministic id → upsert, not a dup.
    await service.record_inbound(
        tenant_id="demo",
        conversation_key="demo:conv-1",
        event_ids=("evt-1", "evt-2"),
        reason="retries exhausted",
        attempts=6,
        first_seen_at=first_seen,
        now=now,
    )
    assert await service.count_open("demo") == 1

    rearmed = await service.rearm("demo", created.id, now)
    assert rearmed is not None
    assert await service.count_open("demo") == 0
    assert await service.rearm("demo", "missing", now) is None


def test_inbound_sweeper_dead_letters_exhausted_events(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app):
        registry = app.state.registry
        now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
        # received_at far older than inbound_events_dead_letter_seconds (3600s).
        asyncio.run(
            registry.inbound_chat_event_repository().append(
                InboundChatEvent(
                    tenant_id="demo",
                    provider="slack",
                    event_id="evt-old",
                    conversation_key="demo:thread-old",
                    chat_user_ref="U1",
                    message_ref="m1",
                    text="hi",
                    correlation_id="corr-old",
                    chat_thread_ref="thread-old",
                    received_at=datetime(2020, 1, 1, tzinfo=UTC),
                )
            )
        )
        result = asyncio.run(registry.sweep_inbound_events("demo", grace_seconds=120, now=now))
        open_letters = asyncio.run(registry.dead_letter_repository().list_open_dead_letters("demo"))

    assert result.dead_lettered == 1
    assert len(open_letters) == 1
    assert open_letters[0].conversation_key == "demo:thread-old"
    # Privacy: the dead-letter carries identifiers only, never the raw text.
    assert "hi" not in open_letters[0].reason


def test_ops_dead_letters_list_and_rearm_endpoints(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        registry = app.state.registry
        created = asyncio.run(
            registry.dead_letter_service().record_inbound(
                tenant_id="demo",
                conversation_key="demo:thread-x",
                event_ids=("evt-x",),
                reason="retries exhausted",
                attempts=5,
                first_seen_at=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
                now=datetime(2026, 1, 10, 13, 0, tzinfo=UTC),
            )
        )
        listed = client.get("/admin/ops/dead-letters")
        rearm = client.post(f"/admin/ops/dead-letters/{created.id}/rearm")
        missing = client.post("/admin/ops/dead-letters/nope/rearm")
        after = client.get("/admin/ops/dead-letters")

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["dead_letters"]] == [created.id]
    assert rearm.status_code == 200
    assert missing.status_code == 404
    assert after.json()["dead_letters"] == []


def test_ready_reports_workflow_backlog(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert "workflow_backlog" in response.json()["dependencies"]
