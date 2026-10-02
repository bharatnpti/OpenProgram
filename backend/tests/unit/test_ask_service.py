from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pytest

from api.dtos import RiskFindingResponse
from core.application.ask_service import (
    FACT_PERIODS,
    AskService,
    DateWindow,
    GraphNeighborsTool,
    OpenRisksTool,
    PodBlockersTool,
    PodCheckinsTool,
    RecentFactsTool,
    SearchGraphNodesTool,
    _prompt,
    fact_window,
    period_windows,
)
from core.application.blocker_resolution import BlockerResolutionService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.persona_views import PersonaViewService
from core.application.risk_service import RiskService
from core.domain.auth import Principal, Role
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    JsonScalar,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
    WorkItem,
    Workstream,
)
from core.domain.llm import LlmRequest, LlmResponse, LlmToolCall, TokenUsage
from core.domain.risk import RiskProviderConfig
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import CheckIn, DeveloperStatus, StatusSource
from core.ports.llm import LlmProvider
from core.ports.tools import AgentTool
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeLlmProvider


async def test_search_graph_nodes_tool_filters_by_query_kind_and_limit() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(
        Program(
            tenant_id="demo",
            id="program-platform",
            name="Platform Program",
            metadata={"portfolio": "platform"},
        )
    )
    await store.upsert_node(
        WorkItem(
            tenant_id="demo",
            id="wi-auth",
            name="Browser SSO BFF",
            metadata={"repo": "openprogram/auth"},
        )
    )
    await store.upsert_node(
        WorkItem(
            tenant_id="demo",
            id="wi-risk",
            name="Risk board",
            metadata={"repo": "openprogram/risk"},
        )
    )
    tool = SearchGraphNodesTool(tenant_id="demo", repository=store)

    payload = json.loads(
        await tool.run(
            {
                "query": "openprogram",
                "kinds": ["work_item"],
                "limit": 1,
            }
        )
    )

    assert payload == [
        {
            "id": "wi-auth",
            "kind": "work_item",
            "name": "Browser SSO BFF",
            "metadata": {"repo": "openprogram/auth"},
        }
    ]


async def test_graph_neighbors_tool_finds_a_developers_assignments() -> None:
    """Relationship questions need edges; node search alone cannot answer them.

    Without this tool, "which tasks is this developer on" came back as "no
    tasks found in the current graph" -- blaming correct data for a missing
    capability.
    """
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id="demo", id="U1004", name="Noah Weber"))
    await store.upsert_node(
        Task(tenant_id="demo", id="task-chk-102", name="Refund edge cases: settle scope")
    )
    await store.upsert_node(Pod(tenant_id="demo", id="pod-payments", name="Payments Pod"))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="U1004",
            to_node_id="task-chk-102",
            kind=EdgeKind.ASSIGNED_TO,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-payments",
            to_node_id="U1004",
            kind=EdgeKind.CONTAINS,
        )
    )
    tool = GraphNeighborsTool(tenant_id="demo", repository=store, as_of=date(2026, 9, 25))

    payload = json.loads(await tool.run({"node_id": "U1004"}))

    assert payload["node"] == {"id": "U1004", "kind": "developer", "name": "Noah Weber"}
    assert payload["edges"] == [
        {
            "direction": "out",
            "edge_kind": "assigned_to",
            "neighbor_id": "task-chk-102",
            "neighbor_kind": "task",
            "neighbor_name": "Refund edge cases: settle scope",
        },
        {
            "direction": "in",
            "edge_kind": "contains",
            "neighbor_id": "pod-payments",
            "neighbor_kind": "pod",
            "neighbor_name": "Payments Pod",
        },
    ]


async def test_graph_neighbors_tool_filters_by_direction_and_kind() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id="demo", id="U1004", name="Noah Weber"))
    await store.upsert_node(Task(tenant_id="demo", id="task-chk-102", name="Refund edge cases"))
    await store.upsert_node(Pod(tenant_id="demo", id="pod-payments", name="Payments Pod"))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="U1004",
            to_node_id="task-chk-102",
            kind=EdgeKind.ASSIGNED_TO,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-payments",
            to_node_id="U1004",
            kind=EdgeKind.CONTAINS,
        )
    )
    tool = GraphNeighborsTool(tenant_id="demo", repository=store, as_of=date(2026, 9, 25))

    payload = json.loads(
        await tool.run({"node_id": "U1004", "direction": "out", "kinds": ["assigned_to"]})
    )

    assert [edge["neighbor_id"] for edge in payload["edges"]] == ["task-chk-102"]


async def test_graph_neighbors_tool_omits_an_edge_that_had_ended() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id="demo", id="U1004", name="Noah Weber"))
    await store.upsert_node(Task(tenant_id="demo", id="task-old", name="Finished work"))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="U1004",
            to_node_id="task-old",
            kind=EdgeKind.ASSIGNED_TO,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 6, 1),
        )
    )
    tool = GraphNeighborsTool(tenant_id="demo", repository=store, as_of=date(2026, 9, 25))

    payload = json.loads(await tool.run({"node_id": "U1004"}))

    assert payload["edges"] == []


async def test_graph_neighbors_tool_requires_a_node_id() -> None:
    tool = GraphNeighborsTool(
        tenant_id="demo", repository=InMemoryGraphStore(), as_of=date(2026, 9, 25)
    )

    payload = json.loads(await tool.run({}))

    assert payload == {"error": "node_id is required"}


def test_ask_prompt_states_today_so_a_relative_period_resolves() -> None:
    """Without today's date the model invented one from its training cutoff.

    Asked "which workstreams are at risk this week?" it called the heatmap with
    as_of 2024-06-10, got an empty period back, and reported that status data
    was unavailable -- while the exec dashboard showed four amber workstreams.
    """
    prompt = _prompt("Which workstreams are at risk this week?", date(2026, 9, 25))

    assert "2026-09-25" in prompt
    assert "never guess a date" in prompt


# A Friday, and deliberately not the host's today: a tool that ignored the
# asked-for date would read a day with nothing recorded.
AS_OF = date(2026, 9, 25)
_RAW_REPLY = "raw reply text that must stay out of every tool result"
_AGGREGATE_TOOLS = {
    "search_graph_nodes",
    "graph_neighbors",
    "recent_facts",
    "workstream_flow",
    "portfolio_flow",
    "open_risks",
}


def _principal(*roles: Role) -> Principal:
    return Principal(tenant_id="demo", subject="U1001", roles=frozenset(roles))


def _llm_response(text: str = "", tool_calls: tuple[LlmToolCall, ...] = ()) -> LlmResponse:
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


def _risk_service(store: InMemoryGraphStore) -> RiskService:
    return RiskService(
        graph_repository=store,
        time_series_repository=store,
        status_repository=store,
        blocker_resolution=BlockerResolutionService(store, store),
        rollup_repository=store,
        provider_config=RiskProviderConfig(default_no_pr_days=3, default_stale_days=30),
    )


def _persona_service(store: InMemoryGraphStore) -> PersonaViewService:
    return PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )


def _ask_service(store: InMemoryGraphStore, llm: LlmProvider) -> AskService:
    return AskService(
        llm_provider=llm,
        graph_repository=store,
        time_series_repository=store,
        flow_metrics_service=FlowMetricsService(
            graph_repository=store,
            time_series_repository=store,
        ),
        persona_view_service=_persona_service(store),
        risk_service=_risk_service(store),
        model="test-model",
    )


def _tool(service: AskService, principal: Principal, name: str) -> AgentTool:
    return next(tool for tool in service._tools(principal, AS_OF) if tool.name == name)


async def _delivery_store() -> InMemoryGraphStore:
    """Checkout Revamp with one red, one amber and one green workstream on AS_OF.

    The red one also carries an open signal risk: a feature with no pull
    request, owned by a developer whose check-in says it is on track.
    """
    store = InMemoryGraphStore()
    nodes = (
        Program(tenant_id="demo", id="program-commerce", name="Commerce"),
        Project(tenant_id="demo", id="project-checkout", name="Checkout Revamp"),
        Workstream(tenant_id="demo", id="ws-payments", name="Payments"),
        Workstream(tenant_id="demo", id="ws-login", name="Login"),
        Workstream(tenant_id="demo", id="ws-cart", name="Cart"),
        Pod(tenant_id="demo", id="pod-payments", name="Payments Pod"),
        Developer(tenant_id="demo", id="dev-ada", name="Ada"),
        Developer(tenant_id="demo", id="dev-ben", name="Ben"),
        WorkItem(
            tenant_id="demo",
            id="wi-refunds",
            name="Refund flow",
            metadata={
                "item_type": "feature",
                "state": "in_progress",
                "owner_id": "dev-ada",
                "created_at": datetime(2026, 9, 15, 9, 0, tzinfo=UTC).isoformat(),
            },
        ),
    )
    for node in nodes:
        await store.upsert_node(node)
    for parent, child in (
        ("program-commerce", "project-checkout"),
        ("project-checkout", "ws-payments"),
        ("project-checkout", "ws-login"),
        ("project-checkout", "ws-cart"),
        ("project-checkout", "pod-payments"),
        ("pod-payments", "dev-ada"),
        ("pod-payments", "dev-ben"),
        ("ws-payments", "wi-refunds"),
    ):
        await store.add_edge(
            GraphEdge(
                tenant_id="demo", from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
            )
        )
    for node_id, kind, rag, why in (
        ("ws-payments", NodeKind.WORKSTREAM, Rag.RED, "Refund flow is blocked on settlement"),
        ("ws-login", NodeKind.WORKSTREAM, Rag.AMBER, "Login retries are slipping"),
        ("ws-cart", NodeKind.WORKSTREAM, Rag.GREEN, None),
        ("project-checkout", NodeKind.PROJECT, Rag.RED, "Payments is red"),
    ):
        ref = EntityRef(tenant_id="demo", kind=kind, id=node_id)
        await store.record_node_status(
            NodeStatus(
                entity_ref=ref,
                rag=rag,
                source=StatusSource.CONFIRMED,
                factors=(
                    (RollupFactor(description=why, contributes=rag, source_ref=ref),) if why else ()
                ),
                as_of=AS_OF,
            )
        )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-ada",
            as_of=AS_OF,
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Refund flow is on track.",
        )
    )
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-ada",
            correlation_id="checkin-ada",
            asked_at=datetime(2026, 9, 25, 9, 0, tzinfo=UTC),
            replied_at=datetime(2026, 9, 25, 9, 5, tzinfo=UTC),
            raw_reply=_RAW_REPLY,
            signals=None,
            checkin_date=AS_OF,
        )
    )
    await _risk_service(store).assess_and_persist_project("demo", "project-checkout", AS_OF)
    return store


@dataclass
class _KeywordRoutingLlm:
    """Chooses tools the way a model reading only their descriptions would.

    With no ids yet, it calls every tool that needs none and whose description
    claims "at risk" questions; once results are back it names the workstreams
    that are red or amber. Nothing here knows a tool name, so this fails if no
    description steers an "at risk" question to status data.
    """

    requests: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if not request.tool_results:
            return _llm_response(
                tool_calls=tuple(
                    LlmToolCall(id=f"call-{tool.name}", name=tool.name, arguments={})
                    for tool in request.tools
                    if "at risk" in tool.description and "required" not in tool.parameters
                )
            )
        named: dict[str, str] = {}
        for result in request.tool_results:
            payload = json.loads(result.content)
            for cell in payload.get("cells", []):
                if cell["kind"] == "workstream" and cell["rag"] in {"red", "amber"}:
                    named[cell["id"]] = f"{cell['name']} is {cell['rag']}: {cell['why']}."
        return _llm_response(
            text=json.dumps({"answer": " ".join(named.values()), "references": list(named)})
        )


async def test_at_risk_this_week_question_reads_status_and_names_the_red_workstream() -> None:
    """The console's own placeholder question once came back "status data is
    unavailable" with no tool called, while the dashboard showed reds."""
    store = await _delivery_store()
    llm = _KeywordRoutingLlm()

    view = await _ask_service(store, llm).ask(
        principal=_principal(Role.EXEC),
        question="Which workstreams are at risk this week?",
        correlation_id="ask-test",
        as_of=AS_OF,
    )

    assert view.tools_used == ("open_risks", "portfolio_heatmap")
    assert "Payments is red: Refund flow is blocked on settlement." in view.answer
    assert "Login is amber" in view.answer
    assert "Cart" not in view.answer
    assert set(view.references) == {"ws-payments", "ws-login"}
    assert "Today is Friday 2026-09-25." in llm.requests[0].prompt
    heatmap = json.loads(llm.requests[1].tool_results[1].content)
    assert heatmap["as_of"] == "2026-09-25"


async def test_tools_used_reports_the_calls_made_not_the_models_claim() -> None:
    store = await _delivery_store()
    llm = FakeLlmProvider(
        responses=[
            _llm_response(tool_calls=(LlmToolCall(id="call-1", name="open_risks", arguments={}),)),
            _llm_response(
                text=json.dumps({"answer": "One risk.", "references": [], "tools_used": ["x"]})
            ),
        ]
    )

    view = await _ask_service(store, llm).ask(
        principal=_principal(Role.SM),
        question="What are the top risks?",
        correlation_id="ask-test",
        as_of=AS_OF,
    )

    assert view.tools_used == ("open_risks",)
    assert view.answer == "One risk."


async def test_open_risks_tool_returns_the_signals_list() -> None:
    store = await _delivery_store()
    service = _risk_service(store)
    # What /portfolio/risks hands the Signals screen.
    signals = [
        RiskFindingResponse.from_domain(risk)
        for risk in await service.portfolio_risks("demo", AS_OF)
    ]
    drift = await service.portfolio_drift("demo", AS_OF)
    tool = OpenRisksTool(tenant_id="demo", service=service, repository=store, as_of=AS_OF)

    payload = json.loads(await tool.run({}))

    assert payload["risk_count"] == len(signals) == 1
    assert payload["drift_count"] == len(drift)
    assert [
        (risk["severity"], risk["entity_id"], risk["days_open"], risk["owner_says"])
        for risk in payload["risks"]
    ] == [
        (risk.severity.value, risk.entity_ref.id, risk.age_days, risk.owner_status_summary)
        for risk in signals
    ]
    risk = payload["risks"][0]
    assert risk["severity"] == "red"
    assert risk["rule"] == "feature_no_pr"
    assert risk["entity_name"] == "Refund flow"
    assert risk["workstream_name"] == "Payments"
    assert risk["owner_name"] == "Ada"
    assert risk["owner_says"] == "Refund flow is on track."
    assert risk["watermelon"] is signals[0].is_watermelon is True
    assert _RAW_REPLY not in json.dumps(payload)


async def test_open_risks_tool_narrows_to_a_workstream() -> None:
    store = await _delivery_store()
    tool = OpenRisksTool(
        tenant_id="demo", service=_risk_service(store), repository=store, as_of=AS_OF
    )

    payments = json.loads(await tool.run({"workstream_id": "ws-payments"}))
    cart = json.loads(await tool.run({"workstream_id": "ws-cart"}))

    assert [risk["entity_id"] for risk in payments["risks"]] == ["wi-refunds"]
    assert cart["risks"] == []


async def test_an_unknown_id_comes_back_as_an_error_the_model_can_correct() -> None:
    """A guessed id used to raise out of the tool loop and fail the question."""
    store = await _delivery_store()
    llm = FakeLlmProvider(
        responses=[
            _llm_response(
                tool_calls=(
                    LlmToolCall(
                        id="call-1", name="open_risks", arguments={"project_id": "project-x"}
                    ),
                )
            ),
            _llm_response(text=json.dumps({"answer": "No such project.", "references": []})),
        ]
    )

    view = await _ask_service(store, llm).ask(
        principal=_principal(Role.SM),
        question="What are the risks on project x?",
        correlation_id="ask-test",
        as_of=AS_OF,
    )

    error = json.loads(llm.requests[1].tool_results[0].content)
    assert "project-x not found" in error["error"]
    assert view.answer == "No such project."
    assert view.tools_used == ("open_risks",)


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.DEV, set()),
        (Role.PO, _AGGREGATE_TOOLS | {"workstream_progress"}),
        (Role.SM, _AGGREGATE_TOOLS | {"pod_checkins", "pod_blockers"}),
        (
            Role.MGR,
            _AGGREGATE_TOOLS
            | {"workstream_progress", "portfolio_heatmap", "pod_checkins", "pod_blockers"},
        ),
        (Role.EXEC, _AGGREGATE_TOOLS | {"workstream_progress", "portfolio_heatmap"}),
        (
            Role.ADMIN,
            _AGGREGATE_TOOLS
            | {"workstream_progress", "portfolio_heatmap", "pod_checkins", "pod_blockers"},
        ),
    ],
)
def test_ask_offers_only_the_tools_a_role_may_read(role: Role, expected: set[str]) -> None:
    service = _ask_service(InMemoryGraphStore(), FakeLlmProvider())

    offered = {tool.name for tool in service._tools(_principal(role), AS_OF)}

    assert offered == expected


async def test_a_role_that_cannot_read_risks_cannot_call_the_risk_tool_by_name() -> None:
    store = await _delivery_store()
    llm = FakeLlmProvider(
        responses=[
            _llm_response(tool_calls=(LlmToolCall(id="call-1", name="open_risks", arguments={}),)),
            _llm_response(text=json.dumps({"answer": "No access.", "references": []})),
        ]
    )

    view = await _ask_service(store, llm).ask(
        principal=_principal(Role.DEV),
        question="What are the top risks?",
        correlation_id="ask-test",
        as_of=AS_OF,
    )

    assert llm.requests[0].tools == ()
    assert llm.requests[1].tool_results[0].content == "Tool open_risks is not available."
    assert view.tools_used == ()


def test_ask_tools_all_carry_the_asked_for_date() -> None:
    service = _ask_service(InMemoryGraphStore(), FakeLlmProvider())

    tools = service._tools(_principal(Role.ADMIN), AS_OF)
    dated = [tool for tool in tools if hasattr(tool, "as_of")]

    # Every tool but the name search reads a day, so omitting as_of honours
    # the caller's period instead of jumping to the host's today.
    assert {tool.name for tool in tools} - {tool.name for tool in dated} == {"search_graph_nodes"}
    assert all(tool.as_of == AS_OF for tool in dated)


def test_period_windows_resolve_against_the_as_of_date() -> None:
    windows = period_windows(date(2026, 10, 2))  # a Friday

    assert windows["today"] == DateWindow(start=date(2026, 10, 2), end=date(2026, 10, 2))
    assert windows["yesterday"] == DateWindow(start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert windows["this_week"] == DateWindow(start=date(2026, 9, 28), end=date(2026, 10, 2))
    assert windows["last_week"] == DateWindow(start=date(2026, 9, 21), end=date(2026, 9, 27))
    assert windows["last_7_days"] == DateWindow(start=date(2026, 9, 26), end=date(2026, 10, 2))
    assert windows["last_30_days"].start == date(2026, 9, 3)
    # The tool schema offers exactly the periods that resolve.
    assert tuple(windows) == FACT_PERIODS


def test_this_week_asked_on_a_monday_is_that_monday_alone() -> None:
    windows = period_windows(date(2026, 9, 28))

    assert windows["this_week"] == DateWindow(start=date(2026, 9, 28), end=date(2026, 9, 28))
    assert windows["last_week"] == DateWindow(start=date(2026, 9, 21), end=date(2026, 9, 27))


def test_ask_prompt_spells_out_the_days_a_relative_period_means() -> None:
    prompt = _prompt("What changed since Monday?", date(2026, 10, 2))

    assert "Today is Friday 2026-10-02." in prompt
    assert "Yesterday was 2026-10-01." in prompt
    assert "'since Monday' starts 2026-09-28" in prompt
    assert "last week ran 2026-09-21 to 2026-09-27" in prompt
    assert "the last 7 days run from 2026-09-26 to today" in prompt


async def test_recent_facts_reads_a_window_that_ends_on_the_as_of_date() -> None:
    """It used to count back from the host's clock, whatever day was asked about."""
    store = InMemoryGraphStore()
    for day in (20, 22, 24, 25, 28):
        await store.append_fact(
            FactEvent(
                tenant_id="demo",
                source="work_item",
                entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.WORK_ITEM, id=f"wi-{day}"),
                payload={"name": f"Item {day}", "from_state": "proposed", "to_state": "done"},
                observed_at=datetime(2026, 9, day, 12, 0, tzinfo=UTC),
                correlation_id=f"fact-{day}",
            )
        )
    tool = RecentFactsTool(tenant_id="demo", repository=store, as_of=AS_OF)

    async def ids(arguments: dict[str, JsonScalar]) -> list[str]:
        payload = json.loads(await tool.run(arguments))
        return [fact["entity_id"] for fact in payload["facts"]]

    assert await ids({"period": "this_week"}) == ["wi-25", "wi-24", "wi-22"]
    assert await ids({"period": "yesterday"}) == ["wi-24"]
    assert await ids({"period": "today"}) == ["wi-25"]
    assert await ids({"since": "2026-09-24"}) == ["wi-25", "wi-24"]
    # Nothing after the as-of date, even when asked for.
    assert await ids({"since": "2026-09-24", "until": "2026-09-30"}) == ["wi-25", "wi-24"]
    assert await ids({}) == ["wi-25", "wi-24", "wi-22", "wi-20"]
    window = json.loads(await tool.run({"period": "this_week"}))["window"]
    assert window == {"start": "2026-09-21", "end": "2026-09-25"}


def test_a_fact_window_is_at_most_a_month() -> None:
    assert fact_window({"since": "2025-01-01"}, AS_OF) == DateWindow(
        start=AS_OF - timedelta(days=30), end=AS_OF
    )


async def test_snapshot_tools_never_read_past_the_as_of_date() -> None:
    store = await _delivery_store()
    heatmap = _tool(
        _ask_service(store, FakeLlmProvider()), _principal(Role.EXEC), "portfolio_heatmap"
    )

    later = json.loads(await heatmap.run({"as_of": "2026-12-01", "kinds": ["workstream"]}))

    assert later["as_of"] == "2026-09-25"
    assert [(cell["name"], cell["rag"]) for cell in later["cells"]] == [
        ("Payments", "red"),
        ("Login", "amber"),
        ("Cart", "green"),
    ]


async def test_workstream_progress_says_why_a_workstream_is_red() -> None:
    store = await _delivery_store()
    progress = _tool(
        _ask_service(store, FakeLlmProvider()), _principal(Role.MGR), "workstream_progress"
    )

    payload = json.loads(await progress.run({"workstream_id": "ws-payments"}))

    assert payload["rag"] == "red"
    assert payload["factors"] == [
        {"description": "Refund flow is blocked on settlement", "contributes": "red"}
    ]


async def test_pod_checkins_tool_reports_states_without_the_reply() -> None:
    store = await _delivery_store()
    tool = PodCheckinsTool(
        tenant_id="demo", service=_persona_service(store), repository=store, as_of=AS_OF
    )

    payload = json.loads(await tool.run({"pod_id": "pod-payments"}))

    pod = payload["pods"][0]
    assert (pod["pod_name"], pod["confirmed"], pod["missing"]) == ("Payments Pod", 1, 1)
    assert [(dev["developer_name"], dev["state"]) for dev in pod["developers"]] == [
        ("Ada", "confirmed"),
        ("Ben", "missing"),
    ]
    assert pod["developers"][0]["summary"] == "Refund flow is on track."
    assert _RAW_REPLY not in json.dumps(payload)
    assert json.loads(await tool.run({"pod_id": "ws-cart"})) == {
        "error": "ws-cart is a workstream, not a pod"
    }
    assert "not found" in json.loads(await tool.run({"pod_id": "pod-nowhere"}))["error"]


async def test_pod_blockers_tool_lists_open_blockers_across_pods() -> None:
    store = await _delivery_store()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-ben",
            as_of=AS_OF,
            source=StatusSource.CONFIRMED,
            blockers=("waiting on settlement API access",),
            summary="Blocked on settlement API access.",
        )
    )
    tool = PodBlockersTool(
        tenant_id="demo", service=_persona_service(store), repository=store, as_of=AS_OF
    )

    payload = json.loads(await tool.run({}))

    assert payload["blocker_count"] == 1
    blocker = payload["blockers"][0]
    assert (blocker["pod_id"], blocker["owner_name"]) == ("pod-payments", "Ben")
    assert blocker["description"] == "waiting on settlement API access"
    assert _RAW_REPLY not in json.dumps(payload)


async def test_long_text_in_a_tool_result_is_clipped() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Pod(tenant_id="demo", id="pod-a", name="Pod A"))
    await store.upsert_node(Developer(tenant_id="demo", id="dev-a", name="Ada"))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo", from_node_id="pod-a", to_node_id="dev-a", kind=EdgeKind.CONTAINS
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-a",
            as_of=AS_OF,
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="word " * 200,
        )
    )
    tool = PodCheckinsTool(
        tenant_id="demo", service=_persona_service(store), repository=store, as_of=AS_OF
    )

    payload = json.loads(await tool.run({}))

    summary = payload["pods"][0]["developers"][0]["summary"]
    assert len(summary) <= 240
    assert summary.endswith("…")
