from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from typing import NoReturn

from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.rollup_service import RollupService
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
)
from core.domain.integrations import UserRef
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import CheckIn, CheckInSignals, DeveloperStatus, Mood, StatusSource
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
        await _prepare_smoke_graph(registry)
        await _prove_config_crud(settings, registry)
        checkin_date = date(2026, 1, 13)
        await _prove_scheduled_checkin_and_webhook(settings, registry, checkin_date)
        await _prove_nudge_non_response(registry, checkin_date)
        await _prove_sync_facts(registry)
        await _prove_rollup_and_persona(settings, registry, checkin_date)
    finally:
        await registry.close()
    print("phase1 smoke ok")


async def _prepare_smoke_graph(registry: ServiceRegistry) -> None:
    tenant_id = registry.settings.tenant_id
    for node in _smoke_nodes(tenant_id):
        await registry.graph_repository().upsert_node(node)
    for edge in _smoke_edges(tenant_id):
        await registry.graph_repository().add_edge(edge)
    for fact in _smoke_facts(tenant_id):
        await registry.time_series_repository().append_fact_once(fact)
    for checkin in _smoke_checkins(tenant_id):
        await registry.status_repository().record_checkin(checkin)
    for status in _smoke_developer_statuses(tenant_id):
        await registry.status_repository().record_developer_status(status)
    for node_status in _smoke_node_statuses(tenant_id):
        await registry.rollup_repository().record_node_status(node_status)


def _smoke_nodes(tenant_id: str) -> tuple[GraphNode, ...]:
    return (
        Program(tenant_id=tenant_id, id="program-platform", name="Platform Program"),
        Project(tenant_id=tenant_id, id="project-foundations", name="Foundations"),
        Project(tenant_id=tenant_id, id="project-insights", name="Insights"),
        Pod(tenant_id=tenant_id, id="pod-runtime", name="Runtime Pod"),
        Pod(tenant_id=tenant_id, id="pod-experience", name="Experience Pod"),
        Pod(tenant_id=tenant_id, id="pod-data", name="Data Pod"),
        Developer(tenant_id=tenant_id, id="dev-asha", name="Asha"),
        Developer(tenant_id=tenant_id, id="dev-liam", name="Liam"),
        Developer(tenant_id=tenant_id, id="dev-maya", name="Maya"),
        Developer(tenant_id=tenant_id, id="dev-noah", name="Noah"),
        Developer(tenant_id=tenant_id, id="dev-zoe", name="Zoe"),
        Developer(tenant_id=tenant_id, id="dev-ira", name="Ira"),
        Developer(tenant_id=tenant_id, id="dev-kai", name="Kai"),
        Developer(tenant_id=tenant_id, id="dev-omar", name="Omar"),
        Task(tenant_id=tenant_id, id="task-api", name="FastAPI shell"),
        Task(tenant_id=tenant_id, id="task-graph", name="Graph persistence"),
        Task(tenant_id=tenant_id, id="task-chat", name="Chat adapter"),
        Task(tenant_id=tenant_id, id="task-ui", name="React shell"),
    )


def _smoke_edges(tenant_id: str) -> tuple[GraphEdge, ...]:
    start = date(2026, 1, 1)
    return (
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="program-platform",
            to_node_id="project-foundations",
            kind=EdgeKind.CONTAINS,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="program-platform",
            to_node_id="project-insights",
            kind=EdgeKind.CONTAINS,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="project-foundations",
            to_node_id="pod-runtime",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="project-foundations",
            to_node_id="pod-experience",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="project-insights",
            to_node_id="pod-data",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-runtime",
            to_node_id="dev-asha",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-runtime",
            to_node_id="dev-liam",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-experience",
            to_node_id="dev-maya",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-experience",
            to_node_id="dev-noah",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-data",
            to_node_id="dev-zoe",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-data",
            to_node_id="dev-ira",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-data",
            to_node_id="dev-kai",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="pod-runtime",
            to_node_id="dev-omar",
            kind=EdgeKind.CONTAINS,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="dev-asha",
            to_node_id="task-api",
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="dev-liam",
            to_node_id="task-graph",
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="dev-maya",
            to_node_id="task-chat",
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=start,
        ),
        GraphEdge(
            tenant_id=tenant_id,
            from_node_id="dev-noah",
            to_node_id="task-ui",
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=start,
        ),
    )


def _smoke_facts(tenant_id: str) -> tuple[FactEvent, ...]:
    now = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
    return (
        FactEvent(
            tenant_id=tenant_id,
            source="smoke",
            entity_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id="task-api"),
            payload={"status": "green", "confidence": 0.8},
            observed_at=now,
            correlation_id="smoke-demo",
        ),
        FactEvent(
            tenant_id=tenant_id,
            source="smoke",
            entity_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id="task-graph"),
            payload={"status": "amber", "confidence": 0.6},
            observed_at=now,
            correlation_id="smoke-demo",
        ),
    )


def _smoke_checkins(tenant_id: str) -> tuple[CheckIn, ...]:
    asked_at = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)
    replied_at = datetime(2026, 6, 15, 9, 7, tzinfo=UTC)
    return (
        CheckIn(
            tenant_id=tenant_id,
            developer_id="dev-asha",
            correlation_id="smoke-checkin-history-asha",
            asked_at=asked_at,
            replied_at=replied_at,
            raw_reply="API shell is ready for review; no blockers.",
            signals=CheckInSignals(
                progress_note="API shell ready for review",
                blockers=(),
                mood=Mood.POSITIVE,
            ),
        ),
        CheckIn(
            tenant_id=tenant_id,
            developer_id="dev-liam",
            correlation_id="smoke-checkin-history-liam",
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
        ),
    )


def _smoke_developer_statuses(tenant_id: str) -> tuple[DeveloperStatus, ...]:
    return (
        DeveloperStatus(
            tenant_id=tenant_id,
            developer_id="dev-asha",
            as_of=date(2026, 6, 15),
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="API shell is ready for review with no blockers.",
        ),
        DeveloperStatus(
            tenant_id=tenant_id,
            developer_id="dev-liam",
            as_of=date(2026, 6, 14),
            source=StatusSource.STALE,
            blockers=("schema review",),
            summary="Last update is stale while graph persistence awaits schema review.",
        ),
    )


def _smoke_node_statuses(tenant_id: str) -> tuple[NodeStatus, ...]:
    return (
        NodeStatus(
            entity_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.POD, id="pod-runtime"),
            rag=Rag.AMBER,
            source=StatusSource.INFERRED,
            factors=(
                RollupFactor(
                    description="Graph persistence has an unresolved schema review.",
                    contributes=Rag.AMBER,
                    source_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id="task-graph"),
                ),
            ),
            as_of=date(2026, 6, 15),
        ),
    )


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
                "received_at": datetime(2026, 1, 13, 9, 35, tzinfo=UTC).isoformat(),
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


async def _prove_config_crud(settings: Settings, registry: ServiceRegistry) -> None:
    app = create_app(settings=settings, registry=registry)
    with TestClient(app) as client:
        program = client.post(
            "/config/programs",
            json={"id": "program-smoke", "name": "Smoke Program"},
        )
        project = client.post(
            "/config/projects",
            json={"id": "project-smoke", "name": "Smoke Project"},
        )
        pod = client.post(
            "/config/pods",
            json={"id": "pod-smoke", "name": "Smoke Pod"},
        )
        member = client.post(
            "/config/members",
            json={"id": "dev-smoke", "name": "Smoke Dev"},
        )
        program_link = client.post(
            "/config/projects/project-smoke/program",
            json={"program_id": "program-smoke"},
        )
        pod_link = client.post("/config/pods/pod-smoke/projects/project-smoke")
        member_link = client.post(
            "/config/pods/pod-smoke/members/dev-smoke",
            json={"role": "engineer"},
        )
        programs = client.get("/config/programs")
        projects = client.get("/projects")
        fetched_member = client.get("/config/members/dev-smoke")
        delete_member = client.delete("/config/members/dev-smoke")
        members_after_delete = client.get("/config/members")

    _assert(program.status_code == 201, "config program create failed")
    _assert(project.status_code == 201, "config project create failed")
    _assert(pod.status_code == 201, "config pod create failed")
    _assert(member.status_code == 201, "config member create failed")
    _assert(program_link.status_code == 200, "config program-project link failed")
    _assert(pod_link.status_code == 200, "config pod-project link failed")
    _assert(member_link.status_code == 200, "config pod-member link failed")
    _assert(
        any(item["id"] == "program-smoke" for item in programs.json()),
        "config program missing",
    )
    _assert(
        any(item["id"] == "project-smoke" for item in projects.json()),
        "directory project missing",
    )
    _assert(fetched_member.status_code == 200, "config member fetch failed")
    _assert(delete_member.status_code == 204, "config member delete failed")
    _assert(
        all(item["id"] != "dev-smoke" for item in members_after_delete.json()),
        "deleted config member still listed",
    )


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
