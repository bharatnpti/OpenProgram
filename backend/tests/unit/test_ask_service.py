from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pytest

from api.dtos import RiskFindingResponse
from config.settings import Settings
from core.application.ask_service import (
    ANSWER_FORMAT_RULES,
    ASK_SYSTEM_PROMPT,
    FACT_PERIODS,
    AskResponseView,
    AskService,
    AskSource,
    DateWindow,
    GraphNeighborsTool,
    OpenRisksTool,
    PodBlockersTool,
    PodCheckinsTool,
    RecentFactsTool,
    SearchGraphNodesTool,
    _parse_answer,
    _prompt,
    fact_window,
    period_windows,
)
from core.application.blocker_resolution import BlockerResolutionService
from core.application.delivery_scope import DeliveryScopeService
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
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from core.domain.status import CheckIn, DeveloperStatus, StatusSource
from core.ports.llm import LlmProvider
from core.ports.tools import AgentTool
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
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
    tool = SearchGraphNodesTool(tenant_id="demo", repository=store, as_of=AS_OF)

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
        forecast_service=ServiceRegistry(
            Settings(
                _env_file=None,
                secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
                runtime_mode="memory",
            ),
            graph_store=store,
        ).forecast_service(),
        delivery_scope_service=DeliveryScopeService(store, store),
        model="test-model",
    )


def _tool(service: AskService, principal: Principal, name: str) -> AgentTool:
    return next(tool for tool in service._tools(principal, AS_OF) if tool.name == name)


async def _delivery_store() -> InMemoryGraphStore:
    """Checkout Revamp with one red, one amber and one green workstream on AS_OF.

    The red one also carries an open signal risk: a feature with no pull
    request, owned by a developer whose check-in says it is on track. Each
    holds work, so each is in use and on the map.
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
        Task(tenant_id="demo", id="task-login", name="Login retries"),
        Task(tenant_id="demo", id="task-cart", name="Cart totals"),
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
        ("ws-login", "task-login"),
        ("ws-cart", "task-cart"),
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
        # A developer reads their own pod's dates; /ask itself still refuses them.
        (Role.DEV, {"delivery_forecast"}),
        (
            Role.PO,
            _AGGREGATE_TOOLS | {"workstream_progress", "status_reasons", "delivery_forecast"},
        ),
        (
            Role.SM,
            _AGGREGATE_TOOLS
            | {"pod_checkins", "pod_blockers", "status_reasons", "delivery_forecast"},
        ),
        (
            Role.MGR,
            _AGGREGATE_TOOLS
            | {
                "workstream_progress",
                "portfolio_heatmap",
                "pod_checkins",
                "pod_blockers",
                "status_reasons",
                "delivery_forecast",
            },
        ),
        (
            Role.EXEC,
            _AGGREGATE_TOOLS
            | {"workstream_progress", "portfolio_heatmap", "status_reasons", "delivery_forecast"},
        ),
        (
            Role.ADMIN,
            _AGGREGATE_TOOLS
            | {
                "workstream_progress",
                "portfolio_heatmap",
                "pod_checkins",
                "pod_blockers",
                "status_reasons",
                "delivery_forecast",
            },
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

    assert "open_risks" not in {tool.name for tool in llm.requests[0].tools}
    assert llm.requests[1].tool_results[0].content == "Tool open_risks is not available."
    assert view.tools_used == ()


def test_ask_tools_all_carry_the_asked_for_date() -> None:
    service = _ask_service(InMemoryGraphStore(), FakeLlmProvider())

    tools = service._tools(_principal(Role.ADMIN), AS_OF)
    dated = [tool for tool in tools if hasattr(tool, "as_of")]

    # Every tool reads a day -- the name search too, for the workstreams in
    # use -- so omitting as_of honours the caller's period instead of jumping
    # to the host's today.
    assert {tool.name for tool in tools} == {tool.name for tool in dated}
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


async def test_recent_facts_say_states_in_words_not_in_raw_names() -> None:
    """An answer read "Issue CHK-12 moved to in_progress": the model copies what it is handed."""
    store = InMemoryGraphStore()
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="issue",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="CHK-12"),
            payload={"key": "CHK-12", "title": "Cart totals", "state": "in_progress"},
            observed_at=datetime(2026, 9, 25, 9, 0, tzinfo=UTC),
            correlation_id="fact-issue",
        )
    )
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="work_item",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.WORK_ITEM, id="wi-refunds"),
            payload={"name": "Refund flow", "from_state": "in_progress", "to_state": "done"},
            observed_at=datetime(2026, 9, 25, 8, 0, tzinfo=UTC),
            correlation_id="fact-work-item",
        )
    )
    tool = RecentFactsTool(tenant_id="demo", repository=store, as_of=AS_OF)

    facts = json.loads(await tool.run({"period": "today"}))["facts"]

    assert [fact["summary"] for fact in facts] == [
        "Issue CHK-12 moved to In progress: Cart totals",
        "Refund flow moved from In progress to Done",
    ]
    assert facts[0]["details"]["state"] == "In progress"
    assert facts[1]["details"] == {
        "from_state": "In progress",
        "to_state": "Done",
        "item_type": None,
        "repo": None,
        "branch": None,
        "pr_id": None,
    }
    assert "in_progress" not in json.dumps(facts)


async def test_workstream_flow_says_a_work_items_state_in_words() -> None:
    store = await _delivery_store()
    flow = _tool(_ask_service(store, FakeLlmProvider()), _principal(Role.MGR), "workstream_flow")

    payload = json.loads(await flow.run({"workstream_id": "ws-payments"}))

    assert [item["state"] for item in payload["work_items"]] == ["In progress"]


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


# The live qa2 program after check-in round R1: two partial updates, one
# blocker on CHK-8, and one person who did not reply -- stored before the
# rollup stopped counting that silence as a "Blocker: no confirmed reply".
# People carry chat ids as node ids, as members imported from Slack do.
_OMAR, _IRA, _HANA, _ZOE, _LIAM = (
    "U0AA1OMAR01",
    "U0AA1IRA001",
    "U0AA1HANA01",
    "U0AA1ZOE001",
    "U0AA1LIAM01",
)
_CHK8_BLOCKER = (
    "Blocker: CHK-8 payment form validation is done on storefront-web !1, but it's "
    "blocked until Noah reviews it, nobody has looked at it yet."
)
_RAW_IDS = {
    _OMAR,
    _IRA,
    _HANA,
    _ZOE,
    _LIAM,
    "program-platform",
    "project-checkout",
    "pod-payments",
    "pod-data",
    "pod-storefront",
}


def _ref(kind: NodeKind, node_id: str) -> EntityRef:
    return EntityRef(tenant_id="demo", kind=kind, id=node_id)


async def _attention_store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for node in (
        Program(tenant_id="demo", id="program-platform", name="Digital Platform Program"),
        Project(tenant_id="demo", id="project-checkout", name="Checkout Revamp"),
        Pod(tenant_id="demo", id="pod-payments", name="Payments Pod"),
        Pod(tenant_id="demo", id="pod-data", name="Data Pod"),
        Pod(tenant_id="demo", id="pod-storefront", name="Storefront Pod"),
        Developer(tenant_id="demo", id=_OMAR, name="Omar Haddad"),
        Developer(tenant_id="demo", id=_IRA, name="Ira Novak"),
        Developer(tenant_id="demo", id=_HANA, name="Hana Kobayashi"),
        Developer(tenant_id="demo", id=_ZOE, name="Zoe Almeida"),
        Developer(tenant_id="demo", id=_LIAM, name="Liam Chen"),
        Task(
            tenant_id="demo",
            id="CHK-8",
            name="Payment form validation UI",
            metadata={"key": "CHK-8", "state": "in_progress"},
        ),
    ):
        await store.upsert_node(node)
    for parent, child in (
        ("program-platform", "project-checkout"),
        ("project-checkout", "pod-payments"),
        ("project-checkout", "pod-data"),
        ("project-checkout", "pod-storefront"),
        ("project-checkout", "CHK-8"),
        ("pod-payments", _OMAR),
        ("pod-payments", _IRA),
        ("pod-payments", _ZOE),
        ("pod-data", _HANA),
        ("pod-storefront", _LIAM),
    ):
        await store.add_edge(
            GraphEdge(
                tenant_id="demo", from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
            )
        )

    def partial(developer_id: str) -> RollupFactor:
        return RollupFactor(
            description="Status is partial and needs blocker or ETA confirmation.",
            contributes=Rag.AMBER,
            source_ref=_ref(NodeKind.DEVELOPER, developer_id),
            kind=FactorKind.STATUS,
        )

    chk8 = RollupFactor(
        description=_CHK8_BLOCKER,
        contributes=Rag.AMBER,
        source_ref=_ref(NodeKind.TASK, "CHK-8"),
        kind=FactorKind.BLOCKER,
        blocker_id="blocker-chk8",
        work_item_ref=_ref(NodeKind.TASK, "CHK-8"),
        applies_to_pod_ids=("pod-payments",),
    )
    no_reply = RollupFactor(
        description="Blocker: no confirmed reply",
        contributes=Rag.AMBER,
        source_ref=_ref(NodeKind.DEVELOPER, _HANA),
        kind=FactorKind.BLOCKER,
        blocker_id=f"legacy:{_HANA}:1",
        unattributed=True,
    )
    green = RollupFactor(
        description="Confirmed status has no blockers.",
        contributes=Rag.GREEN,
        source_ref=_ref(NodeKind.DEVELOPER, _LIAM),
        kind=FactorKind.STATUS,
    )
    rows: tuple[tuple[NodeKind, str, Rag, StatusSource, tuple[RollupFactor, ...]], ...] = (
        (
            NodeKind.PROGRAM,
            "program-platform",
            Rag.RED,
            StatusSource.PARTIAL,
            (partial(_OMAR), partial(_IRA), chk8, no_reply),
        ),
        (
            NodeKind.PROJECT,
            "project-checkout",
            Rag.RED,
            StatusSource.PARTIAL,
            (partial(_OMAR), partial(_IRA), chk8, no_reply),
        ),
        (
            NodeKind.POD,
            "pod-payments",
            Rag.AMBER,
            StatusSource.PARTIAL,
            (partial(_OMAR), partial(_IRA), chk8),
        ),
        (NodeKind.POD, "pod-data", Rag.AMBER, StatusSource.INFERRED, (no_reply,)),
        (NodeKind.POD, "pod-storefront", Rag.GREEN, StatusSource.CONFIRMED, (green,)),
        (NodeKind.DEVELOPER, _OMAR, Rag.AMBER, StatusSource.PARTIAL, (partial(_OMAR),)),
        (NodeKind.DEVELOPER, _IRA, Rag.AMBER, StatusSource.PARTIAL, (partial(_IRA),)),
        (NodeKind.DEVELOPER, _HANA, Rag.AMBER, StatusSource.INFERRED, (no_reply,)),
        (NodeKind.DEVELOPER, _ZOE, Rag.AMBER, StatusSource.CONFIRMED, (chk8,)),
        (NodeKind.DEVELOPER, _LIAM, Rag.GREEN, StatusSource.CONFIRMED, (green,)),
    )
    for kind, node_id, rag, source, factors in rows:
        await store.record_node_status(
            NodeStatus(
                entity_ref=_ref(kind, node_id), rag=rag, source=source, factors=factors, as_of=AS_OF
            )
        )
    return store


def _labels_in(payload: object) -> list[str]:
    """Every value a tool result offers as a name or label, however deep."""
    if isinstance(payload, dict):
        found = [
            value
            for key, value in payload.items()
            if key in {"name", "label", "about"} and isinstance(value, str)
        ]
        return found + [label for value in payload.values() for label in _labels_in(value)]
    if isinstance(payload, list):
        return [label for value in payload for label in _labels_in(value)]
    return []


def test_ask_system_prompt_carries_every_answer_format_rule() -> None:
    """Answers ran to a paragraph that restated the question, lumped partial
    updates with no reply, ended on generic advice and cited chat ids."""
    for rule in ANSWER_FORMAT_RULES:
        assert rule in ASK_SYSTEM_PROMPT
    rules = " ".join(ANSWER_FORMAT_RULES)
    for phrase in (
        "one verdict line",
        "at most 4 bullet lines starting with '• '",
        "optionally one line on what is fine",
        "80 words at most",
        "Name people by display name, issues by key",
        "One bullet per kind of driver, naming everyone it applies to",
        "give counts exactly",
        "Never put a raw id in answer",
        "U0AA1OMAR01",
        "pod-data",
        "'partial' is a partial update and 'no reply' is no reply",
        "no restating the question",
        "no recommendations or next steps unless the question asks what to do",
        "say so in one line",
    ):
        assert phrase in rules
    # The guardrails the format must not displace.
    assert "Never mention raw DM/reply content." in ASK_SYSTEM_PROMPT
    assert "look the facts up before answering" in ASK_SYSTEM_PROMPT
    assert "single JSON object" in ASK_SYSTEM_PROMPT


def test_ask_prompt_asks_for_the_answer_shape() -> None:
    prompt = _prompt("Why does the program need attention?", AS_OF)

    assert "a verdict line, at most 4 '• ' bullets, 80 words at most" in prompt
    assert "rather than ids" in prompt
    assert prompt.endswith("Question: Why does the program need attention?")


def test_ask_calls_merge_requests_mrs_never_prs() -> None:
    """A live answer read "merged PRs, issues not closed" for GitLab merge
    requests: the facts, risk rules and flow fields say pr or pull_request
    whatever the provider, and tool descriptions spoke of PRs too."""
    rule = next(rule for rule in ANSWER_FORMAT_RULES if "never a PR" in rule)
    assert rule in ASK_SYSTEM_PROMPT
    assert "a merge request or MR, e.g. '2 merged MRs'" in rule
    assert "PR, pr or pull_request" in rule
    assert "merge requests (MRs, never PRs)" in _prompt("What merged this week?", AS_OF)

    tools = _ask_service(InMemoryGraphStore(), FakeLlmProvider())._tools(
        _principal(Role.ADMIN), AS_OF
    )
    descriptions = {tool.name: tool.description for tool in tools}
    pr_wording = re.compile(r"\bPRs?\b|[Pp]ull requests?")

    # Nothing a tool says about itself primes the model to write PR.
    assert [name for name, text in descriptions.items() if pr_wording.search(text)] == []
    for name in ("recent_facts", "workstream_flow", "portfolio_flow", "open_risks"):
        assert "merge request (MR)" in descriptions[name] or "(MRs)" in descriptions[name]
    recent_facts = next(tool for tool in tools if tool.name == "recent_facts")
    assert "vcs_pull_request holds the merge requests (MRs)" in json.dumps(recent_facts.parameters)


async def test_status_reasons_give_the_rollups_own_reasons_by_name() -> None:
    """The live answer read "Omar Haddad, Ira Novak, and Hana Kobayashi have not
    provided complete check-ins" -- two partial updates and a non-reply as one."""
    store = await _attention_store()
    tool = _tool(_ask_service(store, FakeLlmProvider()), _principal(Role.EXEC), "status_reasons")

    payload = json.loads(await tool.run({"node_id": "program-platform"}))

    assert (payload["node"]["name"], payload["rag"]) == ("Digital Platform Program", "red")
    # The stored placeholder is a non-reply, not a second blocker.
    assert payload["open_blocker_count"] == 1
    assert payload["reasons"] == [
        {
            "contributes": "amber",
            "kind": "status",
            "says": "Status is partial and needs blocker or ETA confirmation.",
            "checkin": "partial",
            "people": [{"id": _OMAR, "name": "Omar Haddad"}, {"id": _IRA, "name": "Ira Novak"}],
        },
        {
            "contributes": "amber",
            "kind": "blocker",
            "says": _CHK8_BLOCKER,
            "people": [{"id": _ZOE, "name": "Zoe Almeida"}],
            "about": [{"id": "CHK-8", "kind": "task", "label": "CHK-8"}],
        },
        {
            "contributes": "amber",
            "kind": "status",
            "says": "No confirmed reply to the check-in.",
            "checkin": "no reply",
            "people": [{"id": _HANA, "name": "Hana Kobayashi"}],
        },
    ]
    assert [(part["name"], part["rag"]) for part in payload["parts"]] == [
        ("Checkout Revamp", "red"),
        ("Data Pod", "amber"),
        ("Payments Pod", "amber"),
        ("Storefront Pod", "green"),
    ]
    # Ids are there to cite, never offered as a name.
    labels = _labels_in(payload)
    assert "Omar Haddad" in labels
    assert not _RAW_IDS & set(labels)


async def test_status_reasons_carry_the_open_signals_beneath_the_node() -> None:
    """Told to call open_risks beside it, the live model never did, so issues
    merged but still open in the tracker went unmentioned."""
    store = await _delivery_store()
    tool = _tool(_ask_service(store, FakeLlmProvider()), _principal(Role.EXEC), "status_reasons")

    checkout = json.loads(await tool.run({"node_id": "project-checkout"}))
    cart = json.loads(await tool.run({"node_id": "ws-cart"}))

    assert checkout["open_signal_count"] == 2
    # Worst first: the red risk, then the amber drift on the same item.
    assert checkout["signals"] == [
        {
            "signal": "risk",
            "type": "feature_no_pr",
            "severity": "red",
            "about": {"id": "wi-refunds", "label": "Refund flow"},
            "owner": {"id": "dev-ada", "name": "Ada"},
            "says": "Refund flow has been active for 10 days with no linked pull request.",
        },
        {
            "signal": "drift",
            "type": "claimed_progress_no_activity",
            "severity": "amber",
            "about": {"id": "wi-refunds", "label": "Refund flow"},
            "owner": {"id": "dev-ada", "name": "Ada"},
            "says": (
                "Refund flow owner reported progress but there has been no Git/PR "
                "activity in 10 days."
            ),
        },
    ]
    # Only what lies beneath the node asked about.
    assert (cart["open_signal_count"], cart["signals"]) == (0, [])
    assert _RAW_REPLY not in json.dumps(checkout)


async def test_status_reasons_serve_a_kind_only_where_its_rest_route_would() -> None:
    store = await _attention_store()
    service = _ask_service(store, FakeLlmProvider())
    scrum_master = _tool(service, _principal(Role.SM), "status_reasons")
    product_owner = _tool(service, _principal(Role.PO), "status_reasons")

    # /pods/{id}/rollup serves a scrum master; /programs/{id}/tree does not.
    pod = json.loads(await scrum_master.run({"node_id": "pod-payments"}))
    assert pod["rag"] == "amber"
    assert (
        "not available"
        in json.loads(await scrum_master.run({"node_id": "program-platform"}))["error"]
    )
    # /projects/{id}/progress serves a product owner; /pods/{id}/rollup does not.
    assert json.loads(await product_owner.run({"node_id": "project-checkout"}))["rag"] == "red"
    assert (
        "not available" in json.loads(await product_owner.run({"node_id": "pod-payments"}))["error"]
    )
    assert "is a task" in json.loads(await product_owner.run({"node_id": "CHK-8"}))["error"]


async def test_workstream_factors_name_the_person_they_cite() -> None:
    store = await _delivery_store()
    await store.record_node_status(
        NodeStatus(
            entity_ref=_ref(NodeKind.WORKSTREAM, "ws-login"),
            rag=Rag.AMBER,
            source=StatusSource.PARTIAL,
            factors=(
                RollupFactor(
                    description="Status is partial and needs blocker or ETA confirmation.",
                    contributes=Rag.AMBER,
                    source_ref=_ref(NodeKind.DEVELOPER, "dev-ben"),
                ),
            ),
            as_of=AS_OF,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo", from_node_id="ws-login", to_node_id="dev-ben", kind=EdgeKind.CONTAINS
        )
    )
    progress = _tool(
        _ask_service(store, FakeLlmProvider()), _principal(Role.MGR), "workstream_progress"
    )

    payload = json.loads(await progress.run({"workstream_id": "ws-login"}))

    assert payload["factors"] == [
        {
            "description": "Status is partial and needs blocker or ETA confirmation.",
            "contributes": "amber",
            "about": "Ben",
        }
    ]


async def test_an_answer_names_people_and_items_and_labels_its_sources() -> None:
    """The console showed U0AA1OMAR01, pod-data and program-platform as chips,
    and the model's answer could carry them too."""
    store = await _attention_store()
    answer = (
        "Program is red because:\n"
        "- 1 open blocker: CHK-8 (U0AA1ZOE001), waiting on a review\n"
        "- Partial updates: Omar Haddad (U0AA1OMAR01), `U0AA1IRA001` · no reply: Hana Kobayashi\n"
        "Storefront Pod is green; pod-payments and pod-data amber."
    )
    references = [
        "program-platform",
        "CHK-8",
        _ZOE,
        _OMAR,
        _OMAR,
        "pod-data",
        "U0AA1IRA01",  # one character short: the live model's own slip
    ]
    llm = FakeLlmProvider(
        responses=[
            _llm_response(
                tool_calls=(
                    LlmToolCall(
                        id="call-1",
                        name="status_reasons",
                        arguments={"node_id": "program-platform"},
                    ),
                )
            ),
            _llm_response(text=json.dumps({"answer": answer, "references": references})),
        ]
    )

    view = await _ask_service(store, llm).ask(
        principal=_principal(Role.EXEC),
        question="why Digital Platform Program needs attention",
        correlation_id="ask-test",
        as_of=AS_OF,
    )

    assert view.answer == (
        "Program is red because:\n"
        "• 1 open blocker: CHK-8 (Zoe Almeida), waiting on a review\n"
        "• Partial updates: Omar Haddad, Ira Novak · no reply: Hana Kobayashi\n"
        "Storefront Pod is green; Payments Pod and Data Pod amber."
    )
    assert not any(raw_id in view.answer for raw_id in _RAW_IDS)
    assert view.tools_used == ("status_reasons",)
    # References stay as sent, once each; sources label them for a reader.
    assert view.references == (
        "program-platform",
        "CHK-8",
        _ZOE,
        _OMAR,
        "pod-data",
        "U0AA1IRA01",
    )
    assert view.sources == (
        AskSource(id="program-platform", kind=NodeKind.PROGRAM, label="Digital Platform Program"),
        AskSource(id="CHK-8", kind=NodeKind.TASK, label="CHK-8"),
        AskSource(id=_ZOE, kind=NodeKind.DEVELOPER, label="Zoe Almeida"),
        AskSource(id=_OMAR, kind=NodeKind.DEVELOPER, label="Omar Haddad"),
        AskSource(id="pod-data", kind=NodeKind.POD, label="Data Pod"),
        # Matches nobody, so it stays an id rather than a guessed name.
        AskSource(id="U0AA1IRA01", kind=None, label=None),
    )


async def test_a_member_cited_by_chat_id_is_named_and_their_own_id_wins() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(
        Developer(
            tenant_id="demo",
            id="dev-ira",
            name="Ira Novak",
            metadata={"chat_external_id": "U0C77ABCDE"},
        )
    )
    await store.upsert_node(
        Developer(
            tenant_id="demo",
            id="dev-other",
            name="Other Person",
            metadata={"chat_external_id": "dev-ira"},
        )
    )
    llm = FakeLlmProvider(
        responses=[
            _llm_response(
                text=json.dumps(
                    {
                        "answer": "U0C77ABCDE replied in part; 1 item is open.",
                        "references": ["U0C77ABCDE", "dev-ira"],
                    }
                )
            )
        ]
    )

    view = await _ask_service(store, llm).ask(
        principal=_principal(Role.EXEC),
        question="Who replied in part?",
        correlation_id="ask-test",
        as_of=AS_OF,
    )

    assert [source.label for source in view.sources] == ["Ira Novak", "Ira Novak"]
    assert view.answer == "Ira Novak replied in part; 1 item is open."


def test_a_fenced_or_wrapped_answer_is_unwrapped() -> None:
    fenced = '```json\n{"answer": "Program is red.", "references": ["program-platform"]}\n```'

    parsed = _parse_answer(fenced)

    assert parsed.answer == "Program is red."
    assert parsed.references == ("program-platform",)
    assert _parse_answer("Plain text, no JSON.").answer == "Plain text, no JSON."


# N25: in two of three live runs after deploy 6 the model answered in prose,
# ending on "References: [...]". The line reached the reader -- its ids
# swapped for labels by the raw-id backstop -- and sources came back empty.
_LIVE_PROSE = (
    "Digital Platform Program is amber because:\n"
    "• Blocker: CHK-8 payment form validation is blocked until Noah reviews it "
    "(Zoe Almeida).\n"
    "Most pods and projects are green."
)
_LABELLED_SOURCES = (
    AskSource(id="program-platform", kind=NodeKind.PROGRAM, label="Digital Platform Program"),
    AskSource(id="CHK-8", kind=NodeKind.TASK, label="CHK-8"),
    AskSource(id=_ZOE, kind=NodeKind.DEVELOPER, label="Zoe Almeida"),
)


async def _ask_once(store: InMemoryGraphStore, reply: str) -> AskResponseView:
    llm = FakeLlmProvider(responses=[_llm_response(text=reply)])
    return await _ask_service(store, llm).ask(
        principal=_principal(Role.EXEC),
        question="why Digital Platform Program needs attention",
        correlation_id="ask-test",
        as_of=AS_OF,
    )


@pytest.mark.parametrize(
    "references_line",
    [
        # The ids, as the model was told to cite them.
        f"References: [program-platform, CHK-8, {_ZOE}]",
        f'References: ["program-platform", "CHK-8", "{_ZOE}"]',
        # The labels, as the live line read.
        "References: [Digital Platform Program, CHK-8, Zoe Almeida]",
        f"**Sources:** program-platform; CHK-8; Zoe Almeida ({_ZOE}).",
        f"References:\n- program-platform\n- CHK-8\n- {_ZOE}",
        # An array written one id to a line, as a model pretty-prints it.
        f'References: [\n  "program-platform",\n  "CHK-8",\n  "{_ZOE}"\n]',
        f'References:\n[\n  "program-platform",\n  "CHK-8",\n  "{_ZOE}"\n]',
        '**References:**\n```json\n["Digital Platform Program",\n "CHK-8",\n "Zoe Almeida"\n]\n```',
        f'References: ["program-platform",\n  "CHK-8", "{_ZOE}"]',
        "References: [\n  Digital Platform Program\n  CHK-8\n  Zoe Almeida\n]",
    ],
)
async def test_a_plain_text_answer_loses_its_references_line_and_labels_its_sources(
    references_line: str,
) -> None:
    store = await _attention_store()

    view = await _ask_once(store, f"{_LIVE_PROSE}\n\n{references_line}")

    assert view.answer == _LIVE_PROSE
    assert view.references == ("program-platform", "CHK-8", _ZOE)
    assert view.sources == _LABELLED_SOURCES


async def test_a_references_array_over_several_lines_never_ends_up_in_the_answer() -> None:
    """Live, "What is blocking Checkout?" came back with its answer ending on
    `"Checkout Revamp",`, `"Payments Pod"` and `]`: the model had listed its
    references as an array with one name to a line, and only the heading line
    was cut. The array is the sources, in the order it lists them, not text."""
    store = await _attention_store()
    answer = (
        "Checkout is not currently blocked.\n"
        "Everything related to Checkout has no open blockers today.\n"
        "Payments Pod, which covers Checkout, has zero blockers."
    )
    reply = f'{answer}\n\nReferences: [\n  "Checkout Revamp",\n  "Payments Pod"\n]'

    view = await _ask_once(store, reply)

    assert view.answer == answer
    assert view.references == ("project-checkout", "pod-payments")
    assert view.sources == (
        AskSource(id="project-checkout", kind=NodeKind.PROJECT, label="Checkout Revamp"),
        AskSource(id="pod-payments", kind=NodeKind.POD, label="Payments Pod"),
    )


async def test_a_references_array_inside_a_json_answer_is_cut_whole() -> None:
    store = await _attention_store()
    reply = json.dumps(
        {
            "answer": f'{_LIVE_PROSE}\nReferences: [\n  "Digital Platform Program",\n  "CHK-8"\n]',
            "references": ["program-platform", _ZOE],
        }
    )

    view = await _ask_once(store, reply)

    assert view.answer == _LIVE_PROSE
    assert view.references == ("program-platform", _ZOE, "CHK-8")


async def test_a_references_line_keeps_only_what_names_a_node() -> None:
    """A References line is free text: an item no node answers to is dropped,
    and a label two nodes share names neither of them -- never a guess."""
    store = await _attention_store()
    await store.upsert_node(Pod(tenant_id="demo", id="pod-twin-a", name="Twin Pod"))
    await store.upsert_node(Pod(tenant_id="demo", id="pod-twin-b", name="Twin Pod"))
    reply = (
        "Program is amber because of CHK-8. "
        "(References: program-platform, status_reasons, Twin Pod, CHK-8, CHK-8)"
    )

    view = await _ask_once(store, reply)

    assert view.answer == "Program is amber because of CHK-8."
    assert view.references == ("program-platform", "CHK-8")
    assert [source.label for source in view.sources] == ["Digital Platform Program", "CHK-8"]


async def test_a_json_answer_keeps_its_references_and_cuts_a_restated_list() -> None:
    """Raw newlines inside the answer string, as models write them, used to
    fail the strict decode and show the whole object; a references list
    restated inside the answer adds only what the array lacks."""
    store = await _attention_store()
    answer = f"{_LIVE_PROSE}\nReferences: [Digital Platform Program, Zoe Almeida, Data Pod]"
    reply = (
        "Here is the answer:\n```json\n"
        + json.dumps({"answer": answer, "references": ["program-platform", "CHK-8", _ZOE]})
        .replace("\\n", "\n")
        .replace("\\u2022", "•")
        + "\n```"
    )

    view = await _ask_once(store, reply)

    assert view.answer == _LIVE_PROSE
    assert view.references == ("program-platform", "CHK-8", _ZOE, "pod-data")
    assert view.sources == (
        *_LABELLED_SOURCES,
        AskSource(id="pod-data", kind=NodeKind.POD, label="Data Pod"),
    )


async def test_a_json_reference_written_as_a_label_becomes_its_id() -> None:
    store = await _attention_store()
    reply = json.dumps(
        {"answer": _LIVE_PROSE, "references": ["Digital Platform Program", "CHK-8", "zoe almeida"]}
    )

    view = await _ask_once(store, reply)

    assert view.answer == _LIVE_PROSE
    assert view.references == ("program-platform", "CHK-8", _ZOE)
    assert view.sources == _LABELLED_SOURCES


@pytest.mark.parametrize(
    "reply",
    [
        _LIVE_PROSE,
        json.dumps({"answer": _LIVE_PROSE, "references": []}),
        json.dumps({"answer": _LIVE_PROSE}),
    ],
)
async def test_an_answer_with_no_references_takes_its_sources_from_the_names_it_uses(
    reply: str,
) -> None:
    store = await _attention_store()

    view = await _ask_once(store, reply)

    assert view.answer == _LIVE_PROSE
    # In the order the answer names them; "Noah" is no member's whole name.
    assert view.references == ("program-platform", "CHK-8", _ZOE)
    assert view.sources == _LABELLED_SOURCES


async def test_an_answer_naming_nothing_has_no_sources_and_a_longer_name_wins() -> None:
    store = await _attention_store()
    await store.upsert_node(Pod(tenant_id="demo", id="pod-platform", name="Platform"))

    nothing = await _ask_once(store, "No status has been recorded for that yet.")
    program = await _ask_once(store, "Digital Platform Program is green.")

    assert nothing.answer == "No status has been recorded for that yet."
    assert nothing.references == ()
    assert nothing.sources == ()
    assert program.references == ("program-platform",)


async def test_an_empty_reply_says_no_answer_came_back() -> None:
    view = await _ask_once(await _attention_store(), "")

    assert view.answer == "No answer came back for this question. Please ask again."
    assert view.references == ()
    assert view.sources == ()


@pytest.mark.parametrize(
    ("reply", "answer", "references", "cited"),
    [
        # Cut off before the object closed.
        (
            '{"answer": "Program is red because:\\n• Blocker: CHK-8", '
            '"references": ["program-platform", "CHK-8"',
            "Program is red because:\n• Blocker: CHK-8",
            ("program-platform", "CHK-8"),
            (),
        ),
        # A trailing comma no decoder accepts.
        (
            '{"answer": "Program is red.", "references": ["program-platform",],}',
            "Program is red.",
            ("program-platform",),
            (),
        ),
        # The answer in prose, the references in an object after it.
        (
            'Program is red because:\n• Blocker: CHK-8\n{"references": ["program-platform"]}',
            "Program is red because:\n• Blocker: CHK-8",
            ("program-platform",),
            (),
        ),
        # Lines as a list, references as one string.
        (
            '{"answer": ["Program is red because:", "• Blocker: CHK-8"], '
            '"references": "program-platform, CHK-8"}',
            "Program is red because:\n• Blocker: CHK-8",
            ("program-platform", "CHK-8"),
            (),
        ),
        (
            "Answer: Program is amber. References: [program-platform, CHK-8]",
            "Program is amber.",
            (),
            ("program-platform", "CHK-8"),
        ),
        # A source named inside a bullet is the answer's own text.
        (
            "• 2 issues merged but still open (source: Jira)",
            "• 2 issues merged but still open (source: Jira)",
            (),
            (),
        ),
        # A list over several lines is cut whole, closed or cut off.
        (
            'Program is red.\nReferences: [\n  "program-platform",\n  "CHK-8"\n]',
            "Program is red.",
            (),
            ("program-platform", "CHK-8"),
        ),
        (
            'Program is red.\nReferences:\n[\n  "program-platform",\n  "CHK-8"',
            "Program is red.",
            (),
            ("program-platform", "CHK-8"),
        ),
        # A bracket inside an item does not close the list early.
        (
            'Program is red.\nReferences: [\n  "Checkout [v2]",\n  "CHK-8"\n]',
            "Program is red.",
            (),
            ("Checkout [v2]", "CHK-8"),
        ),
        # Prose after the list is the answer's again.
        (
            'References: [\n  "CHK-8"\n]\nProgram is red.',
            "Program is red.",
            (),
            ("CHK-8",),
        ),
        # Only a references heading opens a list: a "[" line elsewhere is the answer's.
        (
            "[Draft\nProgram is red.",
            "[Draft\nProgram is red.",
            (),
            (),
        ),
    ],
)
def test_every_reply_shape_parses_to_an_answer_and_its_references(
    reply: str, answer: str, references: tuple[str, ...], cited: tuple[str, ...]
) -> None:
    parsed = _parse_answer(reply)

    assert parsed.answer == answer
    assert parsed.references == references
    assert parsed.cited == cited
