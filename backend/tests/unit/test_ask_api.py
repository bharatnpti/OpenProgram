from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.ask_service import AskService
from core.application.blocker_resolution import BlockerResolutionService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.persona_views import PersonaViewService
from core.application.risk_service import RiskService
from core.domain.auth import Principal, Role
from core.domain.llm import LlmResponse, LlmToolCall, TokenUsage
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeLlmProvider

AS_OF = "2026-09-25"

# Each Ask tool beside the REST route that serves the same data. Ask may offer
# a tool only to a role that route would answer.
_REST_TWINS: dict[str, tuple[str, str]] = {
    "search_graph_nodes": ("POST", "/ask"),
    "graph_neighbors": ("POST", "/ask"),
    "recent_facts": ("GET", "/portfolio/feed"),
    "workstream_flow": ("GET", f"/workstreams/ws-x/flow?as_of={AS_OF}"),
    "portfolio_flow": ("GET", f"/portfolio/flow?as_of={AS_OF}"),
    "open_risks": ("GET", f"/portfolio/risks?as_of={AS_OF}"),
    "workstream_progress": ("GET", f"/workstreams/ws-x/progress?as_of={AS_OF}"),
    "portfolio_heatmap": ("GET", f"/portfolio/heatmap?as_of={AS_OF}"),
    "pod_checkins": ("GET", f"/pods/pod-x/checkins?as_of={AS_OF}"),
    "pod_blockers": ("GET", f"/pods/pod-x/blockers?as_of={AS_OF}"),
}


def _app_for_role(settings: Settings, role: str, store: InMemoryGraphStore) -> FastAPI:
    role_settings = settings.model_copy(
        update={"dev_principal_roles": role, "llm_provider": "fake"}
    )
    registry = ServiceRegistry(role_settings, graph_store=store)
    return create_app(settings=role_settings, registry=registry)


def _ask_service(store: InMemoryGraphStore) -> AskService:
    return AskService(
        llm_provider=FakeLlmProvider(),
        graph_repository=store,
        time_series_repository=store,
        flow_metrics_service=FlowMetricsService(
            graph_repository=store, time_series_repository=store
        ),
        persona_view_service=PersonaViewService(
            graph_repository=store,
            status_repository=store,
            rollup_repository=store,
            time_series_repository=store,
        ),
        risk_service=RiskService(
            graph_repository=store,
            time_series_repository=store,
            status_repository=store,
            blocker_resolution=BlockerResolutionService(store, store),
        ),
        model="test-model",
    )


@pytest.mark.parametrize("role", [role.value for role in Role])
def test_ask_offers_a_tool_exactly_when_its_rest_twin_serves_the_role(
    settings: Settings, role: str
) -> None:
    store = InMemoryGraphStore()
    app = _app_for_role(settings, role, store)
    # Unknown ids fail past the capability check; only a 403 means "not yours".
    with TestClient(app, raise_server_exceptions=False) as client:
        served = {
            tool
            for tool, (method, path) in _REST_TWINS.items()
            if client.request(
                method, path, json={"question": "x"} if method == "POST" else None
            ).status_code
            != 403
        }

    principal = Principal(tenant_id="demo", subject="U1001", roles=frozenset({Role(role)}))
    offered = {
        tool.name for tool in _ask_service(store)._tools(principal, date.fromisoformat(AS_OF))
    }

    assert offered == served


def test_ask_route_answers_with_the_tools_the_asker_may_use(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryGraphStore()
    app = _app_for_role(settings, "exec", store)
    llm = FakeLlmProvider(
        responses=[
            _response(tool_calls=(LlmToolCall(id="call-1", name="open_risks", arguments={}),)),
            _response(text=json.dumps({"answer": "No open risks.", "references": []})),
        ]
    )
    monkeypatch.setattr(app.state.registry, "llm_provider", lambda: llm)

    with TestClient(app) as client:
        response = client.post("/ask", json={"question": "What are the top risks?", "as_of": AS_OF})

    assert response.status_code == 200
    assert response.json()["tools_used"] == ["open_risks"]
    assert response.json()["answer"] == "No open risks."
    offered = {tool.name for tool in llm.requests[0].tools}
    assert "portfolio_heatmap" in offered
    assert "pod_checkins" not in offered
    risks = json.loads(llm.requests[1].tool_results[0].content)
    assert risks["as_of"] == AS_OF


def _response(text: str = "", tool_calls: tuple[LlmToolCall, ...] = ()) -> LlmResponse:
    return LlmResponse(
        tenant_id="demo",
        text=text,
        model="test-model",
        usage=TokenUsage(
            prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0.0, latency_ms=1.0
        ),
        trace_id="trace-ask",
        tool_calls=tool_calls,
    )
