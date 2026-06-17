from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.rollup_service import RollupService
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    GraphEdge,
    NodeKind,
    Pod,
    Program,
    Task,
)
from core.domain.status import CheckIn, DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("PULSEOPS_RUN_INTEGRATION") != "1",
        reason="set PULSEOPS_RUN_INTEGRATION=1 or run make integration",
    ),
]

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="


def test_phase1_confirmed_reply_correlates_through_webhook() -> None:
    settings = _settings(
        chat_provider="fake",
        issue_tracker_provider="fake",
        llm_provider="fake",
    )
    app = create_app(settings=settings)
    with TestClient(app) as client:
        asyncio.run(
            app.state.registry.status_collector().start_checkin(
                tenant_id="demo",
                developer_id="dev-asha",
                chat_external_id="U123",
                correlation_id="phase1-corr",
                asked_at=datetime(2026, 6, 15, 9, 0, tzinfo=UTC),
            )
        )
        response = client.post(
            "/webhooks/chat/fake",
            json={
                "user_id": "U123",
                "thread_id": "thread-U123",
                "text": "Finished API shell; no blockers.",
                "message_id": "phase1-reply",
                "received_at": "2026-06-15T09:07:00+00:00",
            },
        )
        status = asyncio.run(
            app.state.registry.status_repository().latest_developer_status(
                "demo",
                "dev-asha",
                date(2026, 6, 15),
            )
        )
        focus = client.get("/me/focus?as_of=2026-06-15")

    assert response.status_code == 200
    assert response.json() == {"status": "processed", "message_id": "phase1-reply"}
    assert status is not None
    assert status.source is StatusSource.CONFIRMED
    assert "Finished API shell" not in json.dumps(focus.json())


def test_phase1_non_response_records_non_green_terminal_status() -> None:
    settings = _settings(chat_provider="fake", issue_tracker_provider="fake", llm_provider="fake")
    app = create_app(settings=settings)
    with TestClient(app):
        repository = app.state.registry.status_repository()
        asyncio.run(
            repository.record_checkin(
                CheckIn(
                    tenant_id="demo",
                    developer_id="dev-liam",
                    correlation_id="phase1-non-response",
                    asked_at=datetime(2026, 6, 15, 9, 0, tzinfo=UTC),
                    replied_at=None,
                    raw_reply=None,
                    signals=None,
                )
            )
        )
        collector = app.state.registry.status_collector()
        nudge_message_id = asyncio.run(
            collector.send_nudge(
                tenant_id="demo",
                correlation_id="phase1-non-response",
                developer_name="Liam",
                chat_external_id="U456",
            )
        )
        terminal = asyncio.run(
            collector.record_non_response(
                tenant_id="demo",
                developer_id="dev-liam",
                as_of=date(2026, 6, 15),
                developer_name="Liam",
            )
        )

    assert nudge_message_id == "msg-U456-1"
    assert terminal.source in {StatusSource.INFERRED, StatusSource.STALE, StatusSource.UNKNOWN}
    assert terminal.source is not StatusSource.CONFIRMED
    assert "no confirmed reply" in terminal.blockers


async def test_phase1_critical_path_blocker_rolls_up_red_and_persists() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Program(tenant_id="demo", id="program-1", name="Program"))
    await store.upsert_node(Pod(tenant_id="demo", id="pod-1", name="Runtime Pod"))
    await store.upsert_node(Developer(tenant_id="demo", id="dev-1", name="Asha"))
    await store.upsert_node(
        Task(
            tenant_id="demo",
            id="task-critical",
            name="Critical rollout",
            metadata={"critical_path": True},
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="program-1",
            to_node_id="pod-1",
            kind=EdgeKind.CONTAINS,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-1",
            to_node_id="dev-1",
            kind=EdgeKind.CONTAINS,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="dev-1",
            to_node_id="task-critical",
            kind=EdgeKind.ASSIGNED_TO,
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 6, 15),
            source=StatusSource.CONFIRMED,
            blockers=("release gate",),
            summary="Blocked on release gate.",
        )
    )
    tree = await store.get_program_tree("demo", "program-1", date(2026, 6, 15))

    statuses = await RollupService(store, store).compute_and_record(tree, date(2026, 6, 15))
    persisted = await store.latest_node_status(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.PROGRAM, id="program-1"),
        date(2026, 6, 15),
    )

    assert {status.entity_ref.id: status.rag.value for status in statuses}["program-1"] == "red"
    assert persisted is not None
    assert persisted.rag.value == "red"


def test_phase1_persona_routes_are_role_scoped() -> None:
    dev_app = create_app(settings=_settings(dev_principal_roles="dev"))
    with TestClient(dev_app) as client:
        focus = client.get("/me/focus?as_of=2026-06-15")
        heatmap_denied = client.get("/portfolio/heatmap?as_of=2026-06-15")

    exec_app = create_app(settings=_settings(dev_principal_roles="exec"))
    with TestClient(exec_app) as client:
        heatmap = client.get("/portfolio/heatmap?as_of=2026-06-15")

    assert focus.status_code == 200
    assert heatmap_denied.status_code == 403
    assert heatmap.status_code == 200


def _settings(**overrides: object) -> Settings:
    values = {
        "secret_key": SECRET_KEY,
        "runtime_mode": "memory",
        "dev_principal_subject": "dev-asha",
        "dev_principal_roles": "admin",
        **overrides,
    }
    return Settings(_env_file=None, **values)
