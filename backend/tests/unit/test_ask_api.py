from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dtos import (
    InvestigateAnswerEvent,
    InvestigateEvent,
    InvestigatePlanEvent,
    InvestigateStepEvent,
)
from api.main import create_app
from config.settings import Settings
from core.application.ask_service import AskService, may_ask
from core.application.blocker_resolution import BlockerResolutionService
from core.application.delivery_scope import DeliveryScopeService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.persona_views import PersonaViewService
from core.application.risk_service import RiskService
from core.domain.auth import Principal, Role
from core.domain.graph import Developer, EdgeKind, GraphEdge, Pod, Project, Task
from core.domain.llm import LlmResponse, LlmToolCall, TokenUsage
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeLlmProvider

AS_OF = "2026-09-25"
# Who asks: the REST calls and the Ask principal are the same person.
ASKER = "U1001"
# The roles /ask admits, by the rule it and every Ask tool share.
_ASKING_ROLES = [
    role.value
    for role in Role
    if may_ask(Principal(tenant_id="demo", subject=ASKER, roles=frozenset({role})))
]

_ASK_ROUTE = ("POST", "/ask")

# Each Ask tool beside the REST route that serves the same data. Ask may offer
# a tool only to a role that route would answer, and only to a role /ask itself
# admits (see test_ask_offers_a_tool_exactly_when_its_rest_twin_serves_the_role).
_REST_TWINS: dict[str, tuple[str, str]] = {
    "search_graph_nodes": _ASK_ROUTE,
    "graph_neighbors": _ASK_ROUTE,
    "recent_facts": ("GET", "/portfolio/feed"),
    "workstream_flow": ("GET", f"/workstreams/ws-x/flow?as_of={AS_OF}"),
    "portfolio_flow": ("GET", f"/portfolio/flow?as_of={AS_OF}"),
    "open_risks": ("GET", f"/portfolio/risks?as_of={AS_OF}"),
    "workstream_progress": ("GET", f"/workstreams/ws-x/progress?as_of={AS_OF}"),
    "portfolio_heatmap": ("GET", f"/portfolio/heatmap?as_of={AS_OF}"),
    "pod_checkins": ("GET", f"/pods/pod-x/checkins?as_of={AS_OF}"),
    "pod_blockers": ("GET", f"/pods/pod-x/blockers?as_of={AS_OF}"),
}
# A tool that explains several kinds of node, each through its own route: it is
# offered when any of them serves the role, and answers per kind as they do.
_ANY_REST_TWIN: dict[str, tuple[tuple[str, str], ...]] = {
    "status_reasons": (
        ("GET", f"/programs/program-x/tree?as_of={AS_OF}"),
        ("GET", f"/projects/project-x/progress?as_of={AS_OF}"),
        ("GET", f"/workstreams/ws-x/progress?as_of={AS_OF}"),
        ("GET", f"/pods/pod-x/rollup?as_of={AS_OF}"),
    ),
    # Scoped reads: a scrum master reads a project their pods work on and a
    # developer their own pod, so an unknown id is refused before it is looked
    # up. These ask about the asker's own part of the tree (_own_part). A
    # developer's own pod answers here, but Ask is not offered to them at all.
    "delivery_forecast": (
        ("GET", f"/projects/project-own/delivery?as_of={AS_OF}"),
        ("GET", f"/pods/pod-own/delivery?as_of={AS_OF}"),
    ),
}


def _app_for_role(settings: Settings, role: str, store: InMemoryGraphStore) -> FastAPI:
    role_settings = settings.model_copy(
        update={
            "dev_principal_roles": role,
            "dev_principal_subject": ASKER,
            "llm_provider": "fake",
        }
    )
    registry = ServiceRegistry(role_settings, graph_store=store)
    return create_app(settings=role_settings, registry=registry)


async def _own_part(store: InMemoryGraphStore) -> None:
    """The asker's pod and its project, and a pod and project that are not theirs."""
    for node in (
        Developer(tenant_id="demo", id=ASKER, name="Asker"),
        Pod(tenant_id="demo", id="pod-own", name="Own Pod"),
        Pod(tenant_id="demo", id="pod-other", name="Other Pod"),
        Project(tenant_id="demo", id="project-own", name="Own Project"),
        Project(tenant_id="demo", id="project-other", name="Other Project"),
    ):
        await store.upsert_node(node)
    for parent, child in (
        ("pod-own", ASKER),
        ("project-own", "pod-own"),
        ("project-other", "pod-other"),
    ):
        await store.add_edge(
            GraphEdge(
                tenant_id="demo", from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
            )
        )


def _ask_service(store: InMemoryGraphStore, registry: ServiceRegistry) -> AskService:
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
        forecast_service=registry.forecast_service(),
        delivery_scope_service=DeliveryScopeService(store, store),
        model="test-model",
    )


@pytest.mark.parametrize("role", [role.value for role in Role])
async def test_ask_offers_a_tool_exactly_when_its_rest_twin_serves_the_role(
    settings: Settings, role: str
) -> None:
    store = InMemoryGraphStore()
    await _own_part(store)
    app = _app_for_role(settings, role, store)
    # Unknown ids fail past the capability check; only a 403 means "not yours".
    with TestClient(app, raise_server_exceptions=False) as client:

        def serves(method: str, path: str) -> bool:
            return (
                client.request(
                    method, path, json={"question": "x"} if method == "POST" else None
                ).status_code
                != 403
            )

        served = {tool for tool, (method, path) in _REST_TWINS.items() if serves(method, path)}
        served |= {
            tool
            for tool, twins in _ANY_REST_TWIN.items()
            if any(serves(method, path) for method, path in twins)
        }
        # The one exception to "offered exactly where the twin serves": /ask's own
        # check gates every tool. A role it refuses gets none, even where a
        # twin would answer them for their own pod (a developer's own pod's dates).
        if not serves(*_ASK_ROUTE):
            served = set()

    principal = Principal(tenant_id="demo", subject=ASKER, roles=frozenset({Role(role)}))
    tools = _ask_service(store, app.state.registry).tools_for(principal, date.fromisoformat(AS_OF))
    offered = {tool.name for tool in tools}

    assert offered == served


async def test_a_developer_is_offered_no_ask_tool_though_their_own_pods_delivery_read_answers(
    settings: Settings,
) -> None:
    """Ask is not for developers: /ask refuses them, so no tool is offered at all."""
    store = InMemoryGraphStore()
    await _own_part(store)
    app = _app_for_role(settings, Role.DEV.value, store)
    principal = Principal(tenant_id="demo", subject=ASKER, roles=frozenset({Role.DEV}))

    with TestClient(app) as client:
        assert client.post("/ask", json={"question": "x"}).status_code == 403
        # The exception is real: the twin of delivery_forecast reads for their own pod.
        assert client.get(f"/pods/pod-own/delivery?as_of={AS_OF}").status_code == 200

    tools = _ask_service(store, app.state.registry).tools_for(principal, date.fromisoformat(AS_OF))
    assert tools == ()


@pytest.mark.parametrize("role", _ASKING_ROLES)
async def test_delivery_forecast_answers_each_project_and_pod_as_its_route_does(
    settings: Settings, role: str
) -> None:
    """Refused where the route refuses, in its words; read where the route reads.

    For every role /ask admits; a role it refuses is offered no tool to compare.
    """
    store = InMemoryGraphStore()
    await _own_part(store)
    app = _app_for_role(settings, role, store)
    principal = Principal(tenant_id="demo", subject=ASKER, roles=frozenset({Role(role)}))
    tools = _ask_service(store, app.state.registry).tools_for(principal, date.fromisoformat(AS_OF))
    tool = next(tool for tool in tools if tool.name == "delivery_forecast")
    reads: dict[str, bool] = {}

    with TestClient(app) as client:
        for kind, node_id in (
            ("projects", "project-own"),
            ("projects", "project-other"),
            ("pods", "pod-own"),
            ("pods", "pod-other"),
        ):
            route = client.get(f"/{kind}/{node_id}/delivery?as_of={AS_OF}")
            answer = json.loads(await tool.run({"node_id": node_id}))
            if route.status_code == 403:
                assert answer == {"error": route.json()["detail"]}, node_id
            else:
                assert route.status_code == 200, (node_id, route.text)
                assert "error" not in answer, node_id
                assert answer["as_of"] == AS_OF
            reads[node_id] = route.status_code == 200

    # A scrum master sees their own part and nothing beyond what the route gives.
    expected = {
        "sm": {"project-own", "pod-own", "pod-other"},
    }.get(role, {"project-own", "project-other", "pod-own", "pod-other"})
    assert {node_id for node_id, read in reads.items() if read} == expected


async def test_delivery_forecast_says_what_the_delivery_route_says(settings: Settings) -> None:
    store = InMemoryGraphStore()
    await _own_part(store)
    await store.upsert_node(
        Task(
            tenant_id="demo",
            id="OWN-1",
            name="Own requirement",
            metadata={"key": "OWN-1", "status": "In Progress", "state": "in_progress"},
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo", from_node_id="pod-own", to_node_id="OWN-1", kind=EdgeKind.CONTAINS
        )
    )
    app = _app_for_role(settings, "mgr", store)
    registry = app.state.registry
    principal = Principal(tenant_id="demo", subject=ASKER, roles=frozenset({Role.MGR}))
    tools = _ask_service(store, registry).tools_for(principal, date.fromisoformat(AS_OF))
    tool = next(tool for tool in tools if tool.name == "delivery_forecast")
    # A past day is read from its stored snapshot, as the daily job keeps it.
    await registry.delivery_service().record_snapshots("demo", date.fromisoformat(AS_OF))

    with TestClient(app) as client:
        committed = client.put(
            "/projects/project-own/delivery-date", json={"target_date": "2026-11-30"}
        )
        assert committed.status_code == 200, committed.text
        route = client.get(f"/projects/project-own/delivery?as_of={AS_OF}").json()["project"]
        answer = json.loads(await tool.run({"node_id": "project-own"}))

    (project,) = answer["projects"]
    assert project["project_id"] == "project-own"
    assert project["project_name"] == "Own Project"
    assert project["committed_date"] == route["commitment"]["target_date"] == "2026-11-30"
    assert project["target_date"] == route["target"]
    assert project["history_working_days"] == route["history"]["sample_days"]
    assert project["history_working_days_needed"] == route["history"]["needed_days"]
    assert project["enough_history"] is False
    assert project["no_forecast_reason"] == route["history"]["reason"]
    assert project["requirements"] == route["total"] == 1
    assert project["reasons"] == route["reasons"]
    assert [pod["pod_name"] for pod in project["pods"]] == ["Own Pod"]


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


async def test_ask_route_labels_each_reference_and_keeps_the_ids(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chips used to print raw ids -- U0AA1OMAR01, pod-data -- for every role."""
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id="demo", id="U0AA1OMAR01", name="Omar Haddad"))
    await store.upsert_node(Pod(tenant_id="demo", id="pod-data", name="Data Pod"))
    await store.upsert_node(
        Task(tenant_id="demo", id="CHK-8", name="Payment form validation UI", metadata={})
    )
    app = _app_for_role(settings, "exec", store)
    llm = FakeLlmProvider(
        responses=[
            _response(
                text=json.dumps(
                    {
                        "answer": "Data Pod is amber: U0AA1OMAR01 sent a partial update.",
                        "references": ["U0AA1OMAR01", "pod-data", "CHK-8", "U-missing"],
                    }
                )
            ),
        ]
    )
    monkeypatch.setattr(app.state.registry, "llm_provider", lambda: llm)

    with TestClient(app) as client:
        response = client.post("/ask", json={"question": "Why is Data amber?", "as_of": AS_OF})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Data Pod is amber: Omar Haddad sent a partial update."
    assert body["references"] == ["U0AA1OMAR01", "pod-data", "CHK-8", "U-missing"]
    assert body["sources"] == [
        {"id": "U0AA1OMAR01", "kind": "developer", "label": "Omar Haddad"},
        {"id": "pod-data", "kind": "pod", "label": "Data Pod"},
        {"id": "CHK-8", "kind": "task", "label": "CHK-8"},
        {"id": "U-missing", "kind": None, "label": None},
    ]


def test_investigate_streams_the_plan_each_step_and_the_answer_as_json_lines(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryGraphStore()
    app = _app_for_role(settings, "exec", store)
    step = "Which risks are open?"
    task = {"description": step, "subagent_type": "researcher"}
    # One step, so the calls come in a fixed order: the main agent delegates, the
    # researcher looks up and writes its notes, the main agent answers.
    llm = FakeLlmProvider(
        responses=[
            _response(
                tool_calls=(
                    LlmToolCall(
                        id="task-1", name="task", arguments=task, arguments_json=json.dumps(task)
                    ),
                )
            ),
            _response(tool_calls=(LlmToolCall(id="call-1", name="open_risks", arguments={}),)),
            _response(text=json.dumps({"findings": ["No open risks"], "references": []})),
            _response(text=json.dumps({"answer": "Nothing is at risk.", "references": []})),
        ]
    )
    monkeypatch.setattr(app.state.registry, "llm_provider", lambda: llm)

    with TestClient(app) as client:
        response = client.post(
            "/ask/investigate", json={"question": "What is at risk?", "as_of": AS_OF}
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["cache-control"] == "no-store"
    lines = [InvestigateEvent.model_validate_json(line).root for line in response.iter_lines()]
    assert [line.type for line in lines] == ["plan", "step", "answer"]
    plan, done, answer = lines
    assert isinstance(plan, InvestigatePlanEvent)
    assert [(s.question, s.status) for s in plan.steps] == [(step, "running")]
    assert isinstance(done, InvestigateStepEvent)
    assert (done.step.status, done.step.tools_used) == ("done", ["open_risks"])
    assert done.step.findings == ["No open risks"]
    assert isinstance(answer, InvestigateAnswerEvent)
    assert answer.answer.answer == "Nothing is at risk."
    assert answer.answer.tools_used == ["open_risks"]
    # The step was offered the exec's tools, and no more.
    assert "pod_checkins" not in {tool.name for tool in llm.requests[1].tools}
    risks = json.loads(next(t.content for t in llm.requests[2].turns if t.role == "tool"))
    assert risks["as_of"] == AS_OF


def test_investigate_is_refused_before_streaming_to_a_role_that_may_not_ask(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app_for_role(settings, "dev", InMemoryGraphStore())
    llm = FakeLlmProvider()
    monkeypatch.setattr(app.state.registry, "llm_provider", lambda: llm)

    with TestClient(app) as client:
        refused = client.post("/ask/investigate", json={"question": "What is at risk?"})
        empty = client.post("/ask/investigate", json={"question": ""})

    assert refused.status_code == 403
    assert refused.headers["content-type"].startswith("application/json")
    assert empty.status_code == 422
    assert llm.requests == []


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
