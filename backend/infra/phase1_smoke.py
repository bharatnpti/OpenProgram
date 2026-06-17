from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from typing import NoReturn

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.rollup_service import RollupService
from core.domain.graph import EntityRef, NodeKind
from core.domain.integrations import UserRef
from infra.persistence.seed_data import seed_demo_graph
from infra.registry import ServiceRegistry
from infra.workflows import daily_checkin, nudge

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="


async def main() -> None:
    settings = Settings(
        secret_key=SECRET_KEY,
        runtime_mode="memory",
        chat_provider="fake",
        issue_tracker_provider="fake",
        vcs_provider="fake",
        calendar_provider="fake",
        llm_provider="fake",
        workflow_provider="fake",
        dev_principal_roles="admin",
    )
    registry = ServiceRegistry(settings)
    daily_checkin._service_registry = lambda: registry
    nudge._service_registry = lambda: registry
    try:
        await seed_demo_graph(
            registry.graph_repository(),
            registry.time_series_repository(),
            settings.tenant_id,
        )
        checkin_date = date(2026, 1, 13)
        await _prove_scheduled_checkin_and_webhook(settings, registry, checkin_date)
        await _prove_nudge_non_response(registry, checkin_date)
        await _prove_sync_facts(registry)
        await _prove_rollup_and_persona(settings, registry, checkin_date)
    finally:
        await registry.close()
    print("phase1 smoke ok")


async def _prove_scheduled_checkin_and_webhook(
    settings: Settings,
    registry: ServiceRegistry,
    checkin_date: date,
) -> None:
    result = await daily_checkin.start_daily_checkin_activity(
        daily_checkin.DailyCheckinInput(
            tenant_id=settings.tenant_id,
            developer_id="dev-asha",
            developer_name="Asha",
            chat_external_id="dev-asha",
            correlation_id="smoke-checkin-asha",
            checkin_date=checkin_date.isoformat(),
        )
    )
    _assert(result.status == "sent", "scheduled check-in did not send")
    correlation = await registry.status_repository().checkin_correlation_by_id(
        settings.tenant_id,
        result.correlation_id,
    )
    if correlation is None:
        _die("scheduled check-in did not persist correlation")
    _assert(
        bool(correlation.outbound_message_id),
        "scheduled check-in did not record outbound message",
    )

    app = create_app(settings=settings, registry=registry)
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/chat/fake",
            json={
                "user_id": "dev-asha",
                "thread_id": "thread-dev-asha",
                "message_id": "smoke-reply-asha",
                "text": "API path is ready; no blockers.",
                "received_at": datetime(2026, 1, 13, 9, 5, tzinfo=UTC).isoformat(),
            },
        )
    _assert(response.status_code == 200, "webhook route failed")
    _assert(response.json()["status"] == "processed", "webhook reply was not processed")

    checkin = await registry.status_repository().checkin_by_correlation(
        settings.tenant_id,
        result.correlation_id,
    )
    status = await registry.status_repository().latest_developer_status(
        settings.tenant_id,
        "dev-asha",
        checkin_date,
    )
    facts = await registry.time_series_repository().list_facts(
        settings.tenant_id,
        EntityRef(tenant_id=settings.tenant_id, kind=NodeKind.DEVELOPER, id="dev-asha"),
    )
    _assert(checkin is not None and checkin.replied_at is not None, "reply was not persisted")
    _assert(status is not None and status.source.value == "confirmed", "status was not confirmed")
    _assert(any(fact.source == "checkin" for fact in facts), "check-in fact missing")


async def _prove_nudge_non_response(registry: ServiceRegistry, checkin_date: date) -> None:
    checkin_result = await daily_checkin.start_daily_checkin_activity(
        daily_checkin.DailyCheckinInput(
            tenant_id=registry.settings.tenant_id,
            developer_id="dev-liam",
            developer_name="Liam",
            chat_external_id="dev-liam",
            correlation_id="smoke-checkin-liam",
            checkin_date=checkin_date.isoformat(),
        )
    )
    _assert(checkin_result.status == "sent", "non-responder check-in did not send")
    payload = nudge.NudgeInput(
        tenant_id=registry.settings.tenant_id,
        correlation_id=checkin_result.correlation_id,
        as_of=checkin_date.isoformat(),
        developer_name="Liam",
        chat_external_id="dev-liam",
    )
    first = await nudge.send_checkin_nudge_activity(payload)
    second = await nudge.send_checkin_nudge_activity(payload)
    closed = await nudge.close_checkin_non_response_activity(payload)
    _assert(first.nudge_message_id is not None, "nudge was not sent")
    _assert(second.nudge_message_id == first.nudge_message_id, "nudge was duplicated")
    _assert(closed.terminal_source == "inferred", "non-response did not record inferred status")


async def _prove_sync_facts(registry: ServiceRegistry) -> None:
    issue_result = await registry.issue_read_sync_service().sync_project(
        tenant_id=registry.settings.tenant_id,
        project_key="PO",
        container_id="pod-runtime",
        observed_at=datetime(2026, 1, 13, 10, 0, tzinfo=UTC),
    )
    vcs_result = await registry.vcs_read_sync_service().sync_repo(
        tenant_id=registry.settings.tenant_id,
        repo_name="pulseops",
        observed_at=datetime(2026, 1, 13, 10, 0, tzinfo=UTC),
    )
    calendar_result = await registry.calendar_read_sync_service().sync_user(
        user=UserRef(
            tenant_id=registry.settings.tenant_id,
            external_id="dev-asha",
            display_name="Asha",
        ),
        start=date(2026, 1, 12),
        end=date(2026, 1, 13),
        observed_at=datetime(2026, 1, 13, 10, 0, tzinfo=UTC),
    )
    task_facts = await registry.time_series_repository().list_facts(
        registry.settings.tenant_id,
        EntityRef(tenant_id=registry.settings.tenant_id, kind=NodeKind.TASK, id="PO-1"),
    )
    dev_facts = await registry.time_series_repository().list_facts(
        registry.settings.tenant_id,
        EntityRef(tenant_id=registry.settings.tenant_id, kind=NodeKind.DEVELOPER, id="dev-asha"),
    )
    _assert(issue_result.items_synced > 0, "issue sync did not populate facts")
    _assert(vcs_result.items_synced > 0, "vcs sync did not populate facts")
    _assert(calendar_result.items_synced > 0, "calendar sync did not populate facts")
    _assert(any(fact.source == "issue" for fact in task_facts), "issue fact missing")
    _assert(any(fact.source == "vcs_commit" for fact in dev_facts), "commit fact missing")
    _assert(any(fact.source == "calendar" for fact in dev_facts), "calendar fact missing")


async def _prove_rollup_and_persona(
    settings: Settings,
    registry: ServiceRegistry,
    checkin_date: date,
) -> None:
    tree = await registry.graph_repository().get_program_tree(
        settings.tenant_id,
        "program-platform",
        checkin_date,
    )
    statuses = await RollupService(
        registry.status_repository(),
        registry.rollup_repository(),
    ).compute_and_record(tree, checkin_date)
    _assert(any(status.rag.value != "green" for status in statuses), "rollup did not escalate")

    app = create_app(settings=settings, registry=registry)
    with TestClient(app) as client:
        checkins_response = client.get(f"/pods/pod-runtime/checkins?as_of={checkin_date}")
        heatmap_response = client.get(f"/portfolio/heatmap?as_of={checkin_date}")
    _assert(checkins_response.status_code == 200, "persona check-ins API failed")
    _assert(heatmap_response.status_code == 200, "persona heatmap API failed")
    checkins = checkins_response.json()
    serialized = json.dumps({"checkins": checkins, "heatmap": heatmap_response.json()})
    _assert(checkins["stale"] + checkins["missing"] >= 1, "persona check-ins hid non-response")
    _assert("API path is ready; no blockers." not in serialized, "persona API exposed raw reply")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        _die(message)


def _die(message: str) -> NoReturn:
    raise SystemExit(message)


if __name__ == "__main__":
    asyncio.run(main())
