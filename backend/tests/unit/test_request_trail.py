"""A request's trail says only what happened or is due, and who reads it.

The demo fixture: Foundations holds the Runtime Pod (Asha, Liam, Omar). Here
the Runtime Pod names Zoe as its scrum master and Ira as its manager, and the
default matrix moves a review up to the scrum master after 2 days and to the
manager after 5. Liam asked Omar for a review on Mon 8 Jun.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, time

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.graph import EntityRef, FactEvent, NodeKind
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.reports import (
    DayReportDefinition,
    DeliveryOutcome,
    DestinationKind,
    ReportDestination,
    ReportRun,
    ReportSchedule,
    RunTrigger,
)
from tests.fixtures.demo_graph import populate_demo_graph

ASKED_AT = datetime(2026, 6, 8, 9, 0, tzinfo=UTC)
ACKNOWLEDGED_AT = datetime(2026, 6, 8, 15, 0, tzinfo=UTC)
MERGED_AT = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
TODAY = "2026-06-16"


def _as(role: str, user: str) -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


def _request(request_id: str, *, status: CrossPersonRequestStatus) -> CrossPersonRequest:
    return CrossPersonRequest(
        tenant_id="demo",
        id=request_id,
        requester_id="dev-liam",
        requester_chat_ref="dev-liam",
        counterpart_id="dev-omar",
        counterpart_display_name="Omar",
        kind=CrossPersonRequestKind.REVIEW,
        note="Review the runtime config change",
        source_correlation_id="checkin-liam-1",
        status=status,
        created_at=ASKED_AT,
        updated_at=ASKED_AT,
        notify_message_id=f"msg-{request_id}",
        notify_correlation_id=f"xreq-{request_id}",
        notify_attempts=1,
        notify_last_attempt_at=datetime(2026, 6, 8, 9, 1, tzinfo=UTC),
    )


def _fact(request: CrossPersonRequest, transition: str, at: datetime, **extra: object) -> FactEvent:
    payload: dict = {"request_id": request.id, "transition": transition, **extra}
    return FactEvent(
        tenant_id="demo",
        source="cross_person_request",
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-omar"),
        payload=payload,
        observed_at=at,
        correlation_id=f"cross-person:{request.id}:{transition}",
    )


@pytest.fixture
def app_and_registry(settings: Settings):  # noqa: ANN201
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    registry = app.state.registry

    async def seed() -> None:
        graph = registry.graph_repository()
        await populate_demo_graph(graph, registry.time_series_repository(), "demo")
        pod = await graph.get_node("demo", "pod-runtime")
        await graph.upsert_node(
            replace(
                pod,
                metadata={
                    **pod.metadata,
                    "escalation_sm_chat_external_id": "dev-zoe",
                    "escalation_sm_display_name": "Zoe",
                    "escalation_manager_chat_external_id": "dev-ira",
                    "escalation_manager_display_name": "Ira",
                },
            )
        )
        requests = registry.cross_person_request_repository()
        facts = registry.time_series_repository()
        # Acknowledged with an ETA, then cleared when the merge landed.
        done = _request("xreq-done", status=CrossPersonRequestStatus.OPEN)
        await requests.create(done)
        await requests.update_status("demo", done.id, CrossPersonRequestStatus.RESOLVED, MERGED_AT)
        await facts.append_fact_once(_fact(done, "opened", ASKED_AT, changed_by="dev-liam"))
        await facts.append_fact_once(
            _fact(done, "acknowledged", ACKNOWLEDGED_AT, changed_by="dev-omar", eta="by Friday")
        )
        await facts.append_fact_once(_fact(done, "resolved", MERGED_AT, changed_by=None))
        # Still open, its DM never delivered.
        waiting = replace(
            _request("xreq-waiting", status=CrossPersonRequestStatus.OPEN),
            notify_message_id=None,
            notify_correlation_id=None,
            notify_attempts=5,
        )
        await requests.create(waiting)
        # The project's day report went out on 11 Jun, and not again.
        reports = registry.day_report_repository()
        await reports.save_definition(
            DayReportDefinition(
                tenant_id="demo",
                report_id="report-foundations",
                name="Foundations daily",
                project_id="project-foundations",
                enabled=True,
                schedule=ReportSchedule(
                    local_time=time(17), timezone="UTC", weekdays=(0, 1, 2, 3, 4)
                ),
                destinations=(ReportDestination(kind=DestinationKind.PERSON, target="dev-ira"),),
                updated_at=ASKED_AT,
                updated_by="dev-ira",
            )
        )
        destination = ReportDestination(kind=DestinationKind.PERSON, target="dev-ira")
        await reports.record_run(
            ReportRun(
                tenant_id="demo",
                run_id="run-11",
                report_id="report-foundations",
                report_date=date(2026, 6, 11),
                trigger=RunTrigger.SCHEDULE,
                started_at=datetime(2026, 6, 11, 17, tzinfo=UTC),
                finished_at=datetime(2026, 6, 11, 17, 1, tzinfo=UTC),
                outcomes=(DeliveryOutcome(destination=destination, ok=True, detail="Sent."),),
            )
        )

    asyncio.run(seed())
    return app, registry


@pytest.fixture
def client(app_and_registry) -> Iterator[TestClient]:  # noqa: ANN001
    app, _ = app_and_registry
    with TestClient(app) as test_client:
        yield test_client


def _steps(client: TestClient, request_id: str, headers: dict[str, str]) -> list[dict]:
    response = client.get(
        f"/cross-person-requests/{request_id}/trail?as_of={TODAY}", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["steps"]


def test_the_trail_of_an_ask_acknowledged_then_cleared_by_the_merge(client: TestClient) -> None:
    steps = _steps(client, "xreq-done", _as("mgr", "someone"))

    assert [(step["kind"], step["state"]) for step in steps] == [
        ("asked", "done"),
        ("acknowledged", "done"),
        # The 11 Jun report carried it while it stood at the scrum master.
        ("named", "done"),
        # It reached the manager on 13 Jun; no report went out before it closed.
        ("named", "due"),
        ("cleared", "done"),
    ]
    asked, acknowledged, to_sm, to_manager, cleared = steps
    assert asked["who"] == "Omar" and asked["dm"] == "sent"
    assert asked["dm_at"].startswith("2026-06-08T09:01")
    assert acknowledged["by"] == "Omar" and acknowledged["eta"] == "by Friday"
    assert (to_sm["who"], to_sm["role"], to_sm["on"], to_sm["report"]) == (
        "Zoe",
        "Scrum master",
        "2026-06-11",
        "sent",
    )
    assert to_sm["computed"] is True and to_sm["project_name"] == "Foundations"
    assert (to_manager["who"], to_manager["on"], to_manager["report"]) == (
        "Ira",
        "2026-06-13",
        "not_sent",
    )
    assert cleared["by"] is None and cleared["at"].startswith("2026-06-15T10:00")
    # Nothing reminds the person asked yet, so no step says it did.
    assert all(step["kind"] != "reminder" for step in steps)


def test_an_undelivered_ask_never_claims_a_dm_and_says_what_is_due(client: TestClient) -> None:
    steps = _steps(client, "xreq-waiting", _as("sm", "dev-zoe"))

    asked = steps[0]
    assert asked["kind"] == "asked" and asked["dm"] == "not_delivered"
    assert asked["dm_at"] is None and asked["dm_attempts"] == 5
    named = [step for step in steps if step["kind"] == "named"]
    assert [(step["who"], step["state"], step["report"]) for step in named] == [
        ("Zoe", "done", "sent"),
        ("Ira", "due", "not_sent"),
    ]


def test_the_requester_and_the_person_asked_read_their_trail_and_others_do_not(
    client: TestClient,
) -> None:
    for who in ("dev-liam", "dev-omar"):
        assert _steps(client, "xreq-done", _as("dev", who))[0]["who"] == "Omar"
    refused = client.get(
        f"/cross-person-requests/xreq-done/trail?as_of={TODAY}", headers=_as("dev", "dev-kai")
    )
    assert refused.status_code == 403
    missing = client.get("/cross-person-requests/nope/trail", headers=_as("mgr", "m"))
    assert missing.status_code == 404


def test_an_executive_reads_the_trail_with_no_one_named(client: TestClient) -> None:
    response = client.get(
        f"/cross-person-requests/xreq-done/trail?as_of={TODAY}", headers=_as("exec", "elena")
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["names_hidden"] is True
    for name in ("Omar", "Liam", "Zoe", "Ira"):
        assert name not in response.text
    assert [step["role"] for step in body["steps"] if step["kind"] == "named"] == [
        "Scrum master",
        "Manager",
    ]


def test_the_board_as_counts_and_the_resolved_list(client: TestClient) -> None:
    counts = client.get(f"/cross-person-requests/counts?as_of={TODAY}", headers=_as("exec", "e"))
    assert counts.status_code == 200, counts.text
    body = counts.json()
    assert (body["open"], body["not_delivered"], body["waiting_over_a_week"]) == (1, 1, 1)
    assert body["resolved_recently"] == 1

    resolved = client.get(
        f"/cross-person-requests/resolved?as_of={TODAY}", headers=_as("po", "dev-maya")
    )
    assert resolved.status_code == 200, resolved.text
    assert [request["id"] for request in resolved.json()["requests"]] == ["xreq-done"]
    assert (
        client.get("/cross-person-requests/resolved", headers=_as("exec", "e")).status_code == 403
    )
    assert (
        client.get("/cross-person-requests/counts", headers=_as("dev", "dev-kai")).status_code
        == 403
    )


async def test_an_acknowledgement_keeps_the_when_its_reply_gave(settings: Settings) -> None:
    from infra.adapters.chat.fake import FakeChatProvider  # noqa: F401  (registry's own)
    from infra.registry import ServiceRegistry

    registry = ServiceRegistry(settings.model_copy(update={"chat_provider": "fake"}))
    service = registry.cross_person_request_service()
    request = _request("xreq-eta", status=CrossPersonRequestStatus.OPEN)
    await registry.cross_person_request_repository().create(request)

    await service.handle_counterpart_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="dev-omar"),
            text="on it, will finish by Friday",
            thread_id="msg-xreq-eta",
            message_id="m-1",
            correlation_id="xreq-xreq-eta",
            received_at=ACKNOWLEDGED_AT,
        ),
        request,
    )

    facts = await registry.time_series_repository().list_facts(
        "demo", EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-omar")
    )
    acknowledged = [fact for fact in facts if fact.payload.get("transition") == "acknowledged"]
    assert len(acknowledged) == 1
    assert "Friday" in str(acknowledged[0].payload.get("eta"))
