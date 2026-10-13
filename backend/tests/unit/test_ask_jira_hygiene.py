"""Ora reads Jira hygiene as the hygiene board shows it, to whoever the board answers.

The jira_hygiene tool hands over the board of /projects/{id}/hygiene and
/pods/{id}/hygiene, through the readiness board's read rule: an executive gets
each rule's counts and never who, and a developer gets no Ask tools at all.
"""

from __future__ import annotations

import json
from datetime import date

from fastapi.testclient import TestClient

from core.application.ask_hygiene import JiraHygieneTool
from core.application.ask_investigation import STEP_GUIDANCE
from core.application.ask_people import NamelessTool
from core.application.ask_service import ASK_SYSTEM_PROMPT, AskService
from core.application.delivery_scope import DeliveryScopeService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.persona_views import PersonaViewService
from core.domain.auth import Principal, Role
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeLlmProvider
from tests.unit.test_hygiene_api import TENANT, _as, _set_up, client  # noqa: F401

__all__ = ["client"]


def _service(client: TestClient) -> AskService:  # noqa: F811
    registry: ServiceRegistry = client.app.state.registry  # type: ignore[attr-defined]
    graph = registry.graph_repository()
    facts = registry.time_series_repository()
    return AskService(
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
        hygiene_service=registry.hygiene_service(),
    )


def _tool(client: TestClient, role: Role, subject: str = "someone") -> JiraHygieneTool | None:  # noqa: F811
    principal = Principal(tenant_id=TENANT, subject=subject, roles=frozenset({role}))
    tools = _service(client).tools_for(principal, date.today())
    tool = next((tool for tool in tools if tool.name == "jira_hygiene"), None)
    # An executive's tools say each person as their role or team (D103).
    inner = tool.inner if isinstance(tool, NamelessTool) else tool
    assert inner is None or isinstance(inner, JiraHygieneTool)
    return tool  # type: ignore[return-value]


async def test_it_says_what_the_board_says_by_key_title_and_owner(client: TestClient) -> None:  # noqa: F811
    _set_up(client)
    assert client.post("/projects/checkout/hygiene/run", headers=_as("mgr")).status_code == 200
    tool = _tool(client, Role.PO, "U-PO")
    assert tool is not None

    payload = json.loads(await tool.run({"project_id": "checkout"}))

    assert payload["rules"][0]["open"] == 1
    assert payload["rules"][0]["asks"] is False
    [finding] = payload["findings"]
    assert finding["key"] == "CHK-9"
    assert finding["title"] == "Capture retry"
    assert finding["what"] == "No story points"
    assert finding["owner"] == "Kai Lund"
    assert finding["suggested_fix"] == "Estimate CHK-9 before it starts."
    assert payload["held_for_stale_data"] == 0
    # Names and keys, never a member's id.
    assert "U-DEV" not in json.dumps(payload)


async def test_an_executive_reads_counts_and_a_developer_gets_no_tool(client: TestClient) -> None:  # noqa: F811
    _set_up(client)
    client.post("/projects/checkout/hygiene/run", headers=_as("mgr"))
    executive = _tool(client, Role.EXEC, "U-EX")
    assert executive is not None

    payload = json.loads(await executive.run({"project_id": "checkout"}))

    assert payload["rules"][0]["open"] == 1
    assert "findings" not in payload and payload["counts_only"]
    assert _tool(client, Role.DEV, "U-DEV") is None


async def test_a_refusal_comes_back_in_the_routes_words(client: TestClient) -> None:  # noqa: F811
    _set_up(client)
    scrum_master = _tool(client, Role.SM, "U-OTHER")
    assert scrum_master is not None

    payload = json.loads(await scrum_master.run({"project_id": "checkout"}))
    nothing = json.loads(await scrum_master.run({}))

    assert payload == {
        "error": "You read the projects your own pods work on, and this is not one of them."
    }
    assert "project_id or pod_id" in nothing["error"]


def test_the_prompts_point_jira_questions_at_the_tool() -> None:
    assert "jira_hygiene" in ASK_SYSTEM_PROMPT
    assert "Jira hygiene" in STEP_GUIDANCE
