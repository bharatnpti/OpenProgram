"""Ask reads release readiness as the readiness board shows it, to whoever the board answers.

In QA round V2 a product owner asked what was still missing before a project
could go to production. Ask had no readiness tool, so it explained the
project's colour and never named the blocking criterion the board showed
missing. The release_readiness tool hands over that board, through the same
read rule as /projects/{id}/readiness and /pods/{id}/readiness.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.ask_service import (
    ASK_SYSTEM_PROMPT,
    AskService,
    ReleaseReadinessTool,
    what_is_needed,
)
from core.application.delivery_scope import DeliveryScopeService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.persona_views import PersonaViewService
from core.domain.auth import Principal, Role
from core.domain.delivery import DeliveryStage
from core.domain.graph import Developer, EdgeKind, GraphEdge, Pod, Task
from core.domain.llm import LlmToolCall
from core.domain.release_readiness import Urgency, UrgencyKind
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeLlmProvider
from tests.unit.test_ask_service import _llm_response

TENANT = "demo"
CRITERIA = (
    {
        "name": "Security review",
        "evidence": "A security review of the project's changes.",
        "applies_to": "release",
        "matchers": [{"kind": "label", "value": "security-review", "strength": "evidence"}],
        "draft": {"labels": ["security-review"]},
    },
    {
        "name": "Load test",
        "evidence": "A load test of the busiest path.",
        "applies_to": "release",
        "severity": "advisory",
        "matchers": [{"kind": "title_phrase", "value": "load test", "strength": "evidence"}],
    },
)


def _as(role: str, user: str = "someone") -> dict[str, str]:
    return {"x-openprogram-dev-user": user, "x-openprogram-dev-roles": role}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(
        settings=settings.model_copy(
            update={
                "demo_mode": True,
                "issue_tracker_provider": "fake",
                "tenant_id": TENANT,
                "llm_provider": "fake",
            }
        )
    )
    with TestClient(app) as test_client:
        graph = app.state.registry.graph_repository()

        async def seed() -> None:
            for node in (
                Developer(tenant_id=TENANT, id="U-SM", name="Ira Novak"),
                Pod(
                    tenant_id=TENANT,
                    id="pod-web",
                    name="Web Pod",
                    metadata={"escalation_sm_member_id": "U-SM"},
                ),
                Task(
                    tenant_id=TENANT,
                    id="LCH-2",
                    name="Load test of the order API",
                    metadata={"key": "LCH-2", "status": "Done", "state": "done"},
                ),
            ):
                await graph.upsert_node(node)
            for parent, child in (("launch", "pod-web"), ("launch", "LCH-2")):
                await graph.add_edge(
                    GraphEdge(
                        tenant_id=TENANT,
                        from_node_id=parent,
                        to_node_id=child,
                        kind=EdgeKind.CONTAINS,
                    )
                )

        project = test_client.post(
            "/config/projects",
            json={"id": "launch", "name": "Launch Project", "jira_project_key": "LCH"},
        )
        assert project.status_code == 201, project.text
        asyncio.run(seed())
        yield test_client


def _set_up(client: TestClient, *, enabled: bool = True) -> None:
    settings = {
        "enabled": enabled,
        "auto_suggest": True,
        "create_in_jira": False,
        "issue_type": "Task",
        "labels": ["release-readiness"],
    }
    assert client.put("/config/readiness/settings", json=settings).status_code == 200
    for criterion in CRITERIA:
        saved = client.put("/config/readiness/criteria", json=criterion)
        assert saved.status_code == 200, saved.text


def _registry(client: TestClient) -> ServiceRegistry:
    return client.app.state.registry  # type: ignore[attr-defined,no-any-return]


def _tool(client: TestClient, role: Role, subject: str = "someone") -> ReleaseReadinessTool:
    registry = _registry(client)
    graph = registry.graph_repository()
    facts = registry.time_series_repository()
    service = AskService(
        llm_provider=FakeLlmProvider(),
        graph_repository=graph,
        time_series_repository=facts,
        flow_metrics_service=FlowMetricsService(
            graph_repository=graph, time_series_repository=facts
        ),
        persona_view_service=PersonaViewService(
            graph_repository=graph,
            status_repository=registry.status_repository(),
            rollup_repository=registry.rollup_repository(),
            time_series_repository=facts,
        ),
        risk_service=registry.risk_service(),
        forecast_service=registry.forecast_service(),
        delivery_scope_service=DeliveryScopeService(graph, registry.rollup_repository()),
        release_readiness_service=registry.release_readiness_service(),
        model="test-model",
    )
    principal = Principal(tenant_id=TENANT, subject=subject, roles=frozenset({role}))
    tools = service.tools_for(principal, date.today())
    tool = next(tool for tool in tools if tool.name == "release_readiness")
    assert isinstance(tool, ReleaseReadinessTool)
    return tool


async def _read(tool: ReleaseReadinessTool, **arguments: str) -> dict[str, object]:
    return json.loads(await tool.run(arguments))  # type: ignore[no-any-return]


def _by_name(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    return {item["criterion"]: item for item in payload["criteria"]}  # type: ignore[attr-defined, index]


async def test_it_says_what_the_readiness_board_says(client: TestClient) -> None:
    _set_up(client)
    run = client.post("/projects/launch/readiness/run", headers=_as("mgr"))
    assert run.status_code == 200, run.text
    board = client.get("/projects/launch/readiness", headers=_as("mgr")).json()

    payload = await _read(_tool(client, Role.MGR), node_id="launch")

    criteria = _by_name(payload)
    route = {item["criterion"]["name"]: item for item in board["findings"]}
    assert set(criteria) == set(route) == {"Security review", "Load test"}
    for name, item in route.items():
        mine = criteria[name]
        assert mine["state"].lower().replace(" ", "_") == item["state"]  # type: ignore[attr-defined]
        assert mine["severity"] == item["criterion"]["severity"]
        assert mine["reason"] == item["reason"]
        assert [e["issue_key"] for e in mine["evidence"]] == [  # type: ignore[attr-defined]
            e["issue_key"] for e in item["evidence"]
        ]
        assert mine["needed_by"] == item["urgency"]["due_on"]
    security = criteria["Security review"]
    assert security["state"] == "Missing"
    assert security["severity"] == "blocking"
    assert security["needed_before"] == "production"
    # The drafted issue's status, as the board shows it.
    draft = route["Security review"]["suggestion"]
    assert security["draft"] == {
        "status": "Open",
        "summary": draft["draft"]["summary"],
        "jira_project": "LCH",
        "created_issue_key": None,
    }
    load = criteria["Load test"]
    assert load["state"] == "Covered"
    assert [e["issue_key"] for e in load["evidence"]] == ["LCH-2"]  # type: ignore[attr-defined]
    summary = board["summary"]
    assert (
        (payload["blocking_missing"], payload["missing"], payload["covered"])
        == (
            summary["blocking_missing"],
            summary["missing"],
            summary["covered"],
        )
        == (1, 1, 1)
    )
    assert payload["scope_name"] == "Launch Project"
    assert payload["checks_on"] is True
    assert payload["day_report_lines"] == board["important"]
    # Names and keys, never a member's id.
    assert "someone" not in json.dumps(payload)


async def test_the_criterion_is_what_is_needed_and_the_key_already_reached_the_stage(
    client: TestClient,
) -> None:
    """Ora wrote "(CHK-3 needed by 2026-11-24)": the stage's key read as the thing needed.

    LCH-2 is done, so it reached production while no security review is in
    Jira. The finding says the review is needed, and that LCH-2 got there
    without it; no field names the key as needed.
    """
    _set_up(client)
    client.post("/projects/launch/readiness/run", headers=_as("mgr"))

    criteria = _by_name(await _read(_tool(client, Role.MGR), node_id="launch"))

    security = criteria["Security review"]
    assert security["urgency"] == "stage_reached"
    assert security["what_is_needed"] == (
        "The security review is needed before production; "
        "LCH-2 already reached production without it."
    )
    assert security["reached_stage_without_it"] == "LCH-2"
    assert "requirement_at_stage" not in security
    load = criteria["Load test"]
    assert (load["state"], load["what_is_needed"], load["reached_stage_without_it"]) == (
        "Covered",
        None,
        None,
    )


@pytest.mark.parametrize(
    ("criterion", "stage", "urgency", "line"),
    [
        (
            "Data protection review (DPR)",
            DeliveryStage.PRODUCTION,
            Urgency(
                kind=UrgencyKind.STAGE_REACHED,
                due_on=date(2026, 11, 24),
                stage_key="LCH-3",
            ),
            "The data protection review (DPR) is needed before production, by 2026-11-24; "
            "LCH-3 already reached production without it.",
        ),
        (
            "Load test",
            DeliveryStage.PRODUCTION,
            Urgency(kind=UrgencyKind.DUE_SOON, due_on=date(2026, 11, 24)),
            "The load test is needed before production, by 2026-11-24.",
        ),
        (
            "Load test",
            DeliveryStage.PRODUCTION,
            Urgency(kind=UrgencyKind.OVERDUE, due_on=date(2026, 10, 1)),
            "The load test was needed before production by 2026-10-01, and that day has passed.",
        ),
        (
            "DPIA",
            DeliveryStage.BUSINESS_TESTING,
            Urgency(kind=UrgencyKind.NO_DATE),
            "The DPIA is needed before business testing.",
        ),
    ],
)
def test_what_is_needed_names_the_criterion_its_stage_and_its_day(
    criterion: str, stage: DeliveryStage, urgency: Urgency, line: str
) -> None:
    assert what_is_needed(criterion, stage, urgency, ready=False) == line


def test_nothing_is_needed_once_a_criterion_is_covered_or_not_applicable() -> None:
    urgency = Urgency(kind=UrgencyKind.LATER, due_on=date(2026, 11, 24))

    assert what_is_needed("Load test", DeliveryStage.PRODUCTION, urgency, ready=True) is None


def test_the_prompt_says_the_criterion_is_needed_never_the_key() -> None:
    assert "in the words of the criterion's what_is_needed" in ASK_SYSTEM_PROMPT
    assert "reached_stage_without_it is a requirement that already" in ASK_SYSTEM_PROMPT
    assert "never something that is needed" in ASK_SYSTEM_PROMPT


async def test_an_executive_reads_the_rows_without_drafts_as_the_route_gives_them(
    client: TestClient,
) -> None:
    _set_up(client)
    client.post("/projects/launch/readiness/run", headers=_as("mgr"))
    board = client.get("/projects/launch/readiness", headers=_as("exec")).json()

    payload = await _read(_tool(client, Role.EXEC), node_id="launch")

    assert all(item["suggestion"] is None for item in board["findings"])
    assert all("draft" not in item for item in payload["criteria"])  # type: ignore[attr-defined]
    assert _by_name(payload)["Security review"]["state"] == "Missing"


async def test_a_board_with_nothing_checked_never_reads_as_ready(client: TestClient) -> None:
    _set_up(client, enabled=False)

    payload = await _read(_tool(client, Role.PO), node_id="launch")

    assert payload["criteria"] == []
    assert payload["no_criteria"] == (
        "Release readiness checks are off for this tenant, so nothing is checked."
    )


async def test_a_board_the_route_refuses_comes_back_in_its_words(client: TestClient) -> None:
    _set_up(client)
    refused = client.get("/pods/pod-web/readiness", headers=_as("po"))
    outsider = client.get("/projects/launch/readiness", headers=_as("sm", "U-OTHER"))

    pod = await _read(_tool(client, Role.PO), node_id="pod-web")
    project = await _read(_tool(client, Role.SM, "U-OTHER"), node_id="launch")
    own = await _read(_tool(client, Role.SM, "U-SM"), node_id="pod-web")

    assert refused.status_code == outsider.status_code == 403
    assert pod == {"error": refused.json()["detail"]}
    assert project == {"error": outsider.json()["detail"]}
    # The pod's own scrum master reads its board.
    assert own["scope_name"] == "Web Pod"


async def test_it_covers_a_project_a_release_or_a_pod_and_says_so_otherwise(
    client: TestClient,
) -> None:
    tool = _tool(client, Role.MGR)

    with pytest.raises(ValueError, match="node_id"):
        await tool.run({})
    task = await _read(tool, node_id="LCH-2")

    assert task == {
        "error": "LCH-2 is a task; release_readiness covers a project, one of its releases, "
        "or a pod"
    }


def test_the_prompt_sends_a_production_question_to_release_readiness() -> None:
    assert "is answered from release_readiness" in ASK_SYSTEM_PROMPT
    assert "never call anything ready unless" in ASK_SYSTEM_PROMPT


def test_the_ask_route_offers_and_runs_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_up(client)
    client.post("/projects/launch/readiness/run", headers=_as("mgr"))
    llm = FakeLlmProvider(
        responses=[
            _llm_response(
                tool_calls=(
                    LlmToolCall(id="c1", name="release_readiness", arguments={"node_id": "launch"}),
                )
            ),
            _llm_response(
                json.dumps(
                    {
                        "answer": "Launch Project is not ready for production:\n"
                        "• Security review is missing (blocking)",
                        "references": ["launch"],
                    }
                )
            ),
        ]
    )
    monkeypatch.setattr(_registry(client), "llm_provider", lambda: llm)

    response = client.post(
        "/ask",
        json={"question": "What is still missing before Launch Project can go to production?"},
        headers=_as("po"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["tools_used"] == ["release_readiness"]
    assert "release_readiness" in {tool.name for tool in llm.requests[0].tools}
    result = json.loads(llm.requests[1].tool_results[0].content)
    assert _by_name(result)["Security review"]["state"] == "Missing"
    # What the model reads says what is needed, and which requirement got there without it.
    assert _by_name(result)["Security review"]["what_is_needed"] == (
        "The security review is needed before production; "
        "LCH-2 already reached production without it."
    )
