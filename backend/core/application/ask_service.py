from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from core.application.agents.tool_loop import ToolCallingAgent
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.flow_metrics_service import (
    FlowMetricsService,
    PortfolioFlowView,
    WorkstreamFlowView,
)
from core.application.persona_views import (
    PersonaViewService,
    PortfolioHeatmapView,
    WorkstreamProgressView,
)
from core.application.portfolio_feed_service import DEFAULT_FEED_SOURCES, PortfolioFeedService
from core.application.risk_service import RiskService
from core.domain.auth import Principal
from core.domain.errors import GraphNotFound
from core.domain.graph import EdgeKind, GraphEdge, GraphNode, JsonScalar, NodeKind
from core.domain.llm import LlmMessage, LlmRequest
from core.domain.risk import DriftFinding, RiskFinding
from core.domain.rollup import Rag
from core.ports.llm import LlmProvider
from core.ports.repositories import GraphRepository, TimeSeriesRepository
from core.ports.tools import AgentTool

ASK_SYSTEM_PROMPT = (
    "You answer program-management questions over a delivery graph. "
    "You know nothing about this tenant's programs, people or status except what "
    "the provided tools return, so look the facts up before answering. "
    "Questions about health -- what is red, amber, at risk, blocked, stuck or "
    "behind -- are answered from the status and risk tools: call them before you "
    "say any status is unavailable, and say which day the status is for. "
    "Relationships -- who is assigned to what, who belongs to which pod, what "
    "contains what -- live on edges, so call graph_neighbors before reporting that "
    "something has none, and never tell the user their data is missing or needs "
    "updating when you have not traversed its edges. "
    "Never mention raw DM/reply content. "
    "Once you have the facts, reply with a single JSON object and nothing else: "
    "answer holds the prose, references an array of the node ids it rests on. "
    "Do not restate references inside answer."
)

# Named periods a time-window question maps onto, resolved against the as-of
# date the question is asked for -- never the host's clock or the model's.
FACT_PERIODS = ("today", "yesterday", "this_week", "last_week", "last_7_days", "last_30_days")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MAX_FACT_WINDOW_DAYS = 31
# The feed reads newest first with no upper bound, so a window ending before
# today is cut from a bounded scan rather than an unbounded read.
_FACT_SCAN_LIMIT = 500
_MAX_PODS = 20
_MAX_TEXT_CHARS = 240
_RAG_ORDER: dict[Rag, int] = {Rag.RED: 0, Rag.AMBER: 1, Rag.UNKNOWN: 2, Rag.GREEN: 3}
_AS_OF_PARAMETER: Mapping[str, object] = {
    "type": "string",
    "format": "date",
    "description": "An earlier day to look at. Leave unset for today.",
}


@dataclass(frozen=True, kw_only=True)
class DateWindow:
    """An inclusive run of days."""

    start: date
    end: date


def period_windows(as_of: date) -> dict[str, DateWindow]:
    """Every named period, resolved against the day the question is asked for."""
    monday = as_of - timedelta(days=as_of.weekday())
    yesterday = as_of - timedelta(days=1)
    return {
        "today": DateWindow(start=as_of, end=as_of),
        "yesterday": DateWindow(start=yesterday, end=yesterday),
        "this_week": DateWindow(start=monday, end=as_of),
        "last_week": DateWindow(start=monday - timedelta(days=7), end=monday - timedelta(days=1)),
        "last_7_days": DateWindow(start=as_of - timedelta(days=6), end=as_of),
        "last_30_days": DateWindow(start=as_of - timedelta(days=29), end=as_of),
    }


@dataclass(frozen=True, kw_only=True)
class AskResponseView:
    answer: str
    references: tuple[str, ...]
    tools_used: tuple[str, ...]
    trace_id: str


@dataclass(frozen=True, kw_only=True)
class ParsedAnswer:
    answer: str
    references: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class SearchGraphNodesTool:
    tenant_id: str
    repository: GraphRepository

    name: str = "search_graph_nodes"
    description: str = (
        "Find graph nodes -- programs, projects, workstreams, pods, developers, tasks "
        "and work items -- by text in their id, name or metadata, optionally of given "
        "kinds. Returns each match's id, kind, name and metadata, but no status. Use it "
        "to turn a name into the id the other tools take."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search across name, id, and metadata.",
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": [kind.value for kind in NodeKind]},
                    "description": "Optional node kinds to restrict the search to.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "description": "Maximum number of matches to return.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        query = _string_argument(arguments.get("query"))
        kinds = _kinds_argument(arguments.get("kinds"))
        limit = _bounded_int(arguments.get("limit"), default=10, maximum=25)
        nodes = await self.repository.list_nodes(self.tenant_id)
        if kinds:
            kind_set = {NodeKind(kind) for kind in kinds}
            nodes = [node for node in nodes if node.kind in kind_set]
        if query:
            query_value = query.lower()
            nodes = [
                node
                for node in nodes
                if query_value in node.id.lower()
                or query_value in node.name.lower()
                or _metadata_text(node).find(query_value) >= 0
            ]
        matches = [
            {
                "id": node.id,
                "kind": node.kind.value,
                "name": node.name,
                "metadata": dict(node.metadata),
            }
            for node in nodes[:limit]
        ]
        return json.dumps(matches, ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class GraphNeighborsTool:
    """Edge traversal, without which no relationship question is answerable.

    `search_graph_nodes` finds a node but says nothing about what it connects
    to, so questions like "which tasks is this developer on" used to come back
    as "no tasks found in the current graph" -- blaming correct data for a
    missing tool.
    """

    tenant_id: str
    repository: GraphRepository
    as_of: date

    name: str = "graph_neighbors"
    description: str = (
        "List the edges touching one graph node, with the node at the other end "
        "resolved to its kind and name. Use this for any question about how things "
        "relate: which tasks or work items a developer is assigned to, who belongs "
        "to a pod, which pod or workstream holds an item, or what a project "
        "contains. Edge kinds are 'contains' (parent to child, including pod to "
        "member), 'assigned_to' (developer to task, pod to workstream) and "
        "'depends_on'. Call search_graph_nodes first if you only know a name."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": (
                        "Node whose edges to list, e.g. a developer id like U1004, "
                        "a pod id, or a work item key."
                    ),
                },
                "direction": {
                    "type": "string",
                    "enum": ["out", "in", "both"],
                    "description": (
                        "'out' for edges leaving the node (a developer's "
                        "assignments), 'in' for edges arriving at it (the pod that "
                        "contains a developer). Defaults to 'both'."
                    ),
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional edge kinds to keep, e.g. ['assigned_to'].",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of edges to return.",
                },
            },
            "required": ["node_id"],
            "additionalProperties": False,
        }
    )

    def _wanted(self, edge: GraphEdge, kinds: set[EdgeKind]) -> bool:
        """Active on the asked-for date, and of a kind the caller wants."""
        return edge.is_active_on(self.as_of) and (not kinds or edge.kind in kinds)

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        node_id = _string_argument(arguments.get("node_id"))
        if node_id is None:
            return json.dumps({"error": "node_id is required"})
        direction = _string_argument(arguments.get("direction")) or "both"
        if direction not in {"out", "in", "both"}:
            direction = "both"
        kinds = set(_edge_kinds_argument(arguments.get("kinds")))
        limit = _bounded_int(arguments.get("limit"), default=25, maximum=50)

        found: list[tuple[str, str, str]] = []
        if direction in {"out", "both"}:
            for edge in await self.repository.list_edges(self.tenant_id, from_node_id=node_id):
                if self._wanted(edge, kinds):
                    found.append(("out", edge.kind.value, edge.to_node_id))
        if direction in {"in", "both"}:
            for edge in await self.repository.list_edges(self.tenant_id, to_node_id=node_id):
                if self._wanted(edge, kinds):
                    found.append(("in", edge.kind.value, edge.from_node_id))

        found = found[:limit]
        names = {
            node.id: node
            for node in await self.repository.list_nodes(self.tenant_id)
            if node.id in {neighbor for _, _, neighbor in found}
        }
        node = await self.repository.get_node(self.tenant_id, node_id)
        return json.dumps(
            {
                "node": (
                    {"id": node.id, "kind": node.kind.value, "name": node.name}
                    if node is not None
                    else {"id": node_id}
                ),
                "as_of": self.as_of.isoformat(),
                "edges": [
                    {
                        "direction": edge_direction,
                        "edge_kind": edge_kind,
                        "neighbor_id": neighbor_id,
                        "neighbor_kind": (
                            names[neighbor_id].kind.value if neighbor_id in names else None
                        ),
                        "neighbor_name": (
                            names[neighbor_id].name if neighbor_id in names else None
                        ),
                    }
                    for edge_direction, edge_kind, neighbor_id in found
                ],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class RecentFactsTool:
    """What happened over a window of days that ends no later than the as-of date.

    It used to count back from the host's clock, so a question asked as of an
    earlier day read the wrong week, and "since Monday" left the model to do
    date arithmetic in a ``since_days`` integer.
    """

    tenant_id: str
    repository: TimeSeriesRepository
    as_of: date

    name: str = "recent_facts"
    description: str = (
        "The activity log, newest first: work-item transitions, issue updates, commits, "
        "pull requests, check-in updates (counts only, never reply text), risks opened "
        "or cleared, and cross-person requests. Covers a window of days ending no later "
        "than today; pick it with period, or with since/until dates, and it defaults to "
        "the last 7 days. Use it for what changed or happened over a period -- 'since "
        "Monday', 'yesterday', 'this week' -- not for current status."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": list(FACT_PERIODS),
                    "description": (
                        "A named window, resolved against today. Weeks start on Monday, "
                        "so this_week runs from Monday to today."
                    ),
                },
                "since": {
                    "type": "string",
                    "format": "date",
                    "description": "First day of the window, when no period fits.",
                },
                "until": {
                    "type": "string",
                    "format": "date",
                    "description": "Last day of the window. Defaults to today.",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(DEFAULT_FEED_SOURCES)},
                    "description": "Optional fact sources to keep.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of facts to return.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        window = fact_window(arguments, self.as_of)
        limit = _bounded_int(arguments.get("limit"), default=20, maximum=50)
        sources = _string_list_argument(arguments.get("sources"))
        since = datetime.combine(window.start, time.min, tzinfo=UTC)
        until = datetime.combine(window.end + timedelta(days=1), time.min, tzinfo=UTC)
        feed = PortfolioFeedService(self.repository)
        view = await feed.feed(
            self.tenant_id, since=since, sources=sources or None, limit=_FACT_SCAN_LIMIT
        )
        items = [item for item in view.items if item.observed_at < until]
        return json.dumps(
            {
                "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
                "fact_count": len(items),
                # A full scan may have stopped short of the window's oldest facts.
                "may_be_incomplete": len(view.items) >= _FACT_SCAN_LIMIT,
                "facts": [
                    {
                        "source": item.source,
                        "kind": item.kind,
                        "summary": item.summary,
                        "entity_kind": item.entity_ref.kind.value,
                        "entity_id": item.entity_ref.id,
                        "observed_at": item.observed_at.isoformat(),
                        "details": dict(item.details),
                    }
                    for item in items[:limit]
                ],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class WorkstreamFlowTool:
    tenant_id: str
    service: FlowMetricsService
    as_of: date

    name: str = "workstream_flow"
    description: str = (
        "Flow metrics for one workstream on one day, today unless as_of names an "
        "earlier one: active, in-flight, completed, stale and abandoned work-item "
        "counts, average cycle time and PR age, and each work item's state and age. "
        "It describes movement and carries no RAG status."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "workstream_id": {"type": "string"},
                "as_of": _AS_OF_PARAMETER,
            },
            "required": ["workstream_id"],
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        workstream_id = _required_string(arguments.get("workstream_id"), "workstream_id")
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        view = await self.service.workstream_flow(self.tenant_id, workstream_id, as_of)
        return json.dumps(_workstream_flow_payload(view), ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class PortfolioFlowTool:
    tenant_id: str
    service: FlowMetricsService
    as_of: date

    name: str = "portfolio_flow"
    description: str = (
        "Flow metrics for every workstream on one day, today unless as_of names an "
        "earlier one: active, in-flight, completed, stale and abandoned counts, and "
        "average cycle time and PR age. It describes movement and carries no RAG status."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "as_of": _AS_OF_PARAMETER,
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        view = await self.service.portfolio_flow(self.tenant_id, as_of)
        return json.dumps(_portfolio_flow_payload(view), ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class WorkstreamProgressTool:
    tenant_id: str
    service: PersonaViewService
    as_of: date

    name: str = "workstream_progress"
    description: str = (
        "RAG status of one workstream on one day, today unless as_of names an earlier "
        "one: its colour, source and confidence, percent complete, task counts by "
        "colour, the factors behind the colour, and its red and amber tasks. Use it to "
        "say whether, and why, a workstream is at risk."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "workstream_id": {"type": "string"},
                "as_of": _AS_OF_PARAMETER,
            },
            "required": ["workstream_id"],
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        workstream_id = _required_string(arguments.get("workstream_id"), "workstream_id")
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        view = await self.service.workstream_progress(self.tenant_id, workstream_id, as_of)
        return json.dumps(_workstream_progress_payload(view), ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class PortfolioHeatmapTool:
    tenant_id: str
    service: PersonaViewService
    repository: GraphRepository
    as_of: date

    name: str = "portfolio_heatmap"
    description: str = (
        "RAG status of everything in the portfolio -- programs, projects, workstreams, "
        "pods, developers and tasks -- on one day, today unless as_of names an earlier "
        "one. Worst first, each with its colour, source and the main reason for it. "
        "This is the status behind the dashboard heat rows: use it for 'what is at "
        "risk', 'what is red or amber' and 'which workstreams are behind'. Narrow it "
        "with kinds, e.g. ['workstream']."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "as_of": _AS_OF_PARAMETER,
                "program_root_id": {"type": "string"},
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": [kind.value for kind in NodeKind]},
                    "description": "Optional node kinds to keep.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum number of cells to return.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        program_root_id = _string_argument(arguments.get("program_root_id"))
        kinds = set(_kinds_argument(arguments.get("kinds")))
        limit = _bounded_int(arguments.get("limit"), default=60, maximum=100)
        view = await self.service.portfolio_heatmap(self.tenant_id, as_of, program_root_id)
        names = await _node_names(self.repository, self.tenant_id)
        return json.dumps(
            _portfolio_heatmap_payload(view, names, kinds=kinds, limit=limit),
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class OpenRisksTool:
    """The Signals list, so "what are the top risks?" reads what the screen shows."""

    tenant_id: str
    service: RiskService
    repository: GraphRepository
    as_of: date

    name: str = "open_risks"
    description: str = (
        "The risks and drift findings open today -- the list the Signals screen shows. "
        "A risk is signal-derived (a feature with no PR, an ageing PR, a stale work "
        "item) and carries its severity, days open, the reason, its evidence, what the "
        "owner's latest check-in says, and whether it is a watermelon (the owner "
        "reports fine while the signals do not). A drift finding is where a stated "
        "status and the hard signals disagree. Use it for 'top risks', 'what is at "
        "risk or stuck' and 'where do owners and signals disagree'. Narrow it with "
        "project_id or workstream_id."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "workstream_id": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "description": "Maximum number of risks, and of drift findings.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        project_id = _string_argument(arguments.get("project_id"))
        workstream_id = _string_argument(arguments.get("workstream_id"))
        limit = _bounded_int(arguments.get("limit"), default=15, maximum=30)
        if project_id is None:
            risks = await self.service.portfolio_risks(self.tenant_id, self.as_of)
            drift = await self.service.portfolio_drift(self.tenant_id, self.as_of)
        else:
            risks = await self.service.project_risks(self.tenant_id, project_id, self.as_of)
            drift = await self.service.project_drift(self.tenant_id, project_id, self.as_of)
        if workstream_id is not None:
            risks = [risk for risk in risks if risk.workstream_id == workstream_id]
            drift = [finding for finding in drift if finding.workstream_id == workstream_id]
        names = await _node_names(self.repository, self.tenant_id)
        return json.dumps(
            {
                "as_of": self.as_of.isoformat(),
                "risk_count": len(risks),
                "drift_count": len(drift),
                "risks": [_risk_payload(risk, names) for risk in risks[:limit]],
                "drift": [_drift_payload(finding, names) for finding in drift[:limit]],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class PodCheckinsTool:
    tenant_id: str
    service: PersonaViewService
    repository: GraphRepository
    as_of: date

    name: str = "pod_checkins"
    description: str = (
        "Who in a pod has checked in today: each developer's state (confirmed, "
        "partial, stale or missing), the day their status was last given and its "
        "parsed summary -- never the reply itself -- with counts per state. Omit "
        "pod_id to cover every pod."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"pod_id": {"type": "string"}},
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        pods = await _pods(self.repository, self.tenant_id, arguments.get("pod_id"))
        if isinstance(pods, str):
            return json.dumps({"error": pods})
        views = [
            await self.service.pod_checkins(self.tenant_id, pod.id, self.as_of) for pod in pods
        ]
        return json.dumps(
            {
                "as_of": self.as_of.isoformat(),
                "pods": [
                    {
                        "pod_id": view.pod_id,
                        "pod_name": view.pod_name,
                        "confirmed": view.confirmed,
                        "partial": view.partial,
                        "stale": view.stale,
                        "missing": view.missing,
                        "developers": [
                            {
                                "developer_id": developer.developer_id,
                                "developer_name": developer.developer_name,
                                "state": developer.state,
                                "source": developer.source.value,
                                "status_as_of": _iso(developer.status_as_of),
                                "summary": _clip(developer.summary),
                            }
                            for developer in view.developers[:25]
                        ],
                    }
                    for view in views
                ],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class PodBlockersTool:
    tenant_id: str
    service: PersonaViewService
    repository: GraphRepository
    as_of: date

    name: str = "pod_blockers"
    description: str = (
        "Blockers open today in a pod, oldest first: what is blocked, for how many "
        "days, whose blocker it is and which work item it is attributed to. Omit "
        "pod_id to cover every pod."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "pod_id": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of blockers to return.",
                },
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        pods = await _pods(self.repository, self.tenant_id, arguments.get("pod_id"))
        if isinstance(pods, str):
            return json.dumps({"error": pods})
        limit = _bounded_int(arguments.get("limit"), default=20, maximum=50)
        blockers = [
            (view.pod_id, view.pod_name, blocker)
            for view in [
                await self.service.pod_blockers(self.tenant_id, pod.id, self.as_of) for pod in pods
            ]
            for blocker in view.blockers
        ]
        blockers.sort(key=lambda item: -item[2].age_days)
        return json.dumps(
            {
                "as_of": self.as_of.isoformat(),
                "blocker_count": len(blockers),
                "blockers": [
                    {
                        "pod_id": pod_id,
                        "pod_name": pod_name,
                        "blocker_id": blocker.blocker_id,
                        "description": _clip(blocker.description),
                        "age_days": blocker.age_days,
                        "first_seen_on": blocker.first_seen_on.isoformat(),
                        "owner_id": blocker.owner_id,
                        "owner_name": blocker.owner_name,
                        "work_item_id": (
                            blocker.work_item_ref.id if blocker.work_item_ref else None
                        ),
                        "unattributed": blocker.unattributed,
                    }
                    for pod_id, pod_name, blocker in blockers[:limit]
                ],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class _RecordedTool:
    """Runs a tool and notes that it ran.

    ``tools_used`` used to be whatever the model wrote into its JSON, so an
    answer could claim tools it never called -- or report none when it had.
    This records the calls that actually happened. A missing id or a bad
    argument comes back to the model as an error it can correct rather than
    failing the whole question.
    """

    inner: AgentTool
    calls: list[str]

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def description(self) -> str:
        return self.inner.description

    @property
    def parameters(self) -> Mapping[str, object]:
        return self.inner.parameters

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        self.calls.append(self.inner.name)
        try:
            return await self.inner.run(arguments)
        except (GraphNotFound, ValueError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _reads_aggregate(principal: Principal) -> bool:
    """The team-or-exec read that /ask itself and the portfolio routes require."""
    policy = AuthorizationPolicy()
    return policy.can(principal, Capability.READ_TEAM_AGGREGATE) or policy.can(
        principal, Capability.READ_EXEC_AGGREGATE
    )


def _has(capability: Capability) -> Callable[[Principal], bool]:
    return lambda principal: AuthorizationPolicy().can(principal, capability)


class AskService:
    def __init__(
        self,
        llm_provider: LlmProvider,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
        flow_metrics_service: FlowMetricsService,
        persona_view_service: PersonaViewService,
        risk_service: RiskService,
        model: str,
        tool_agent: ToolCallingAgent | None = None,
    ) -> None:
        self._llm_provider = llm_provider
        self._graph_repository = graph_repository
        self._time_series_repository = time_series_repository
        self._flow_metrics_service = flow_metrics_service
        self._persona_view_service = persona_view_service
        self._risk_service = risk_service
        self._model = model
        self._tool_agent = tool_agent or ToolCallingAgent(llm_provider=llm_provider)

    async def ask(
        self,
        *,
        principal: Principal,
        question: str,
        correlation_id: str,
        as_of: date | None = None,
    ) -> AskResponseView:
        asked_for = as_of or date.today()
        request = LlmRequest(
            tenant_id=principal.tenant_id,
            prompt=_prompt(question, asked_for),
            model=self._model,
            correlation_id=correlation_id,
            system=ASK_SYSTEM_PROMPT,
            messages=(
                LlmMessage(
                    role="user",
                    content=question,
                ),
            ),
            metadata={
                "agent": "ask_service",
                "purpose": "graph_question",
                "as_of": asked_for.isoformat(),
            },
        )
        calls: list[str] = []
        tools = tuple(
            _RecordedTool(inner=tool, calls=calls) for tool in self._tools(principal, asked_for)
        )
        response = await self._tool_agent.run(request, tools)
        parsed = _parse_answer(response.text)
        return AskResponseView(
            answer=parsed.answer,
            references=parsed.references,
            tools_used=tuple(dict.fromkeys(calls)),
            trace_id=response.trace_id,
        )

    def _tools(self, principal: Principal, as_of: date) -> tuple[AgentTool, ...]:
        """The tools this principal may use, each gated as its REST twin is.

        Ask must never read more than the asker could fetch directly, so a tool
        is offered only when the endpoint serving the same data would answer
        them. A tool left out is simply absent: the model cannot see it, and a
        call to it by name comes back as not available.
        """
        tenant_id = principal.tenant_id
        graph = self._graph_repository
        personas = self._persona_view_service
        offered: tuple[tuple[AgentTool, Callable[[Principal], bool]], ...] = (
            # Graph reads behind /ask's own aggregate check.
            (SearchGraphNodesTool(tenant_id=tenant_id, repository=graph), _reads_aggregate),
            (
                GraphNeighborsTool(tenant_id=tenant_id, repository=graph, as_of=as_of),
                _reads_aggregate,
            ),
            # /portfolio/feed
            (
                RecentFactsTool(
                    tenant_id=tenant_id, repository=self._time_series_repository, as_of=as_of
                ),
                _reads_aggregate,
            ),
            # /workstreams/{id}/flow and /portfolio/flow
            (
                WorkstreamFlowTool(
                    tenant_id=tenant_id, service=self._flow_metrics_service, as_of=as_of
                ),
                _reads_aggregate,
            ),
            (
                PortfolioFlowTool(
                    tenant_id=tenant_id, service=self._flow_metrics_service, as_of=as_of
                ),
                _reads_aggregate,
            ),
            # /portfolio/risks and /projects/{id}/risks
            (
                OpenRisksTool(
                    tenant_id=tenant_id,
                    service=self._risk_service,
                    repository=graph,
                    as_of=as_of,
                ),
                _reads_aggregate,
            ),
            # /workstreams/{id}/progress
            (
                WorkstreamProgressTool(tenant_id=tenant_id, service=personas, as_of=as_of),
                _has(Capability.READ_PROJECT_PROGRESS),
            ),
            # /portfolio/heatmap
            (
                PortfolioHeatmapTool(
                    tenant_id=tenant_id, service=personas, repository=graph, as_of=as_of
                ),
                _has(Capability.READ_PORTFOLIO_HEATMAP),
            ),
            # /pods/{id}/checkins
            (
                PodCheckinsTool(
                    tenant_id=tenant_id, service=personas, repository=graph, as_of=as_of
                ),
                _has(Capability.READ_POD_CHECKINS),
            ),
            # /pods/{id}/blockers
            (
                PodBlockersTool(
                    tenant_id=tenant_id, service=personas, repository=graph, as_of=as_of
                ),
                _has(Capability.READ_POD_BLOCKERS),
            ),
        )
        return tuple(tool for tool, allowed in offered if allowed(principal))


def fact_window(arguments: Mapping[str, JsonScalar], as_of: date) -> DateWindow:
    """The days a recent_facts call covers, never running past the as-of date."""
    period = _string_argument(arguments.get("period"))
    windows = period_windows(as_of)
    if period in windows:
        return windows[period]
    until = _optional_date(arguments.get("until"))
    end = min(until, as_of) if until is not None else as_of
    since = _optional_date(arguments.get("since"))
    start = since if since is not None else windows["last_7_days"].start
    start = min(max(start, end - timedelta(days=_MAX_FACT_WINDOW_DAYS - 1)), end)
    return DateWindow(start=start, end=end)


def _prompt(question: str, as_of: date) -> str:
    windows = period_windows(as_of)
    yesterday = windows["yesterday"].start
    this_week = windows["this_week"]
    last_week = windows["last_week"]
    last_7_days = windows["last_7_days"]
    return (
        f"Today is {_WEEKDAYS[as_of.weekday()]} {as_of.isoformat()}. "
        f"Yesterday was {yesterday.isoformat()}. This week runs from Monday "
        f"{this_week.start.isoformat()} to today, so 'since Monday' starts "
        f"{this_week.start.isoformat()}; last week ran {last_week.start.isoformat()} "
        f"to {last_week.end.isoformat()}; the last 7 days run from "
        f"{last_7_days.start.isoformat()} to today. "
        "Resolve any relative period against these dates, and never guess a date. "
        "Status and risk tools describe one day: for questions about now, this week "
        "or what is at risk, leave their as_of unset to get today's. For what "
        "happened over a period, give recent_facts a period, or since and until. "
        "A tool asked about the wrong period comes back empty, and empty is not "
        "the same as nothing being wrong. "
        "Keep the answer concise and specific, naming the people and items "
        "involved, and list the id of each one in references. "
        f"Question: {question}"
    )


def _parse_answer(text: str) -> ParsedAnswer:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return ParsedAnswer(answer=text.strip(), references=())
    if not isinstance(parsed, dict):
        return ParsedAnswer(answer=text.strip(), references=())
    answer = parsed.get("answer")
    references = parsed.get("references")
    return ParsedAnswer(
        answer=answer if isinstance(answer, str) and answer else text.strip(),
        references=tuple(str(item) for item in references) if isinstance(references, list) else (),
    )


def _workstream_flow_payload(view: WorkstreamFlowView) -> dict[str, object]:
    return {
        "workstream_id": view.workstream_id,
        "workstream_name": view.workstream_name,
        "as_of": view.as_of.isoformat(),
        "active_count": view.active_count,
        "features_in_flight": view.features_in_flight,
        "completed_count": view.completed_count,
        "stale_count": view.stale_count,
        "abandoned_count": view.abandoned_count,
        "avg_cycle_time_days": view.avg_cycle_time_days,
        "avg_pr_age_days": view.avg_pr_age_days,
        "work_items": [
            {
                "id": item.id,
                "name": item.name,
                "state": item.state,
                "item_type": item.item_type,
                "repo": item.repo,
                "branch": item.branch,
                "pr_id": item.pr_id,
                "workstream_ids": list(item.workstream_ids),
                "age_days": item.age_days,
                "cycle_time_days": item.cycle_time_days,
                "last_transition_at": item.last_transition_at.isoformat()
                if item.last_transition_at is not None
                else None,
            }
            for item in view.work_items
        ],
    }


def _portfolio_flow_payload(view: PortfolioFlowView) -> dict[str, object]:
    return {
        "as_of": view.as_of.isoformat(),
        "active_count": view.active_count,
        "features_in_flight": view.features_in_flight,
        "completed_count": view.completed_count,
        "stale_count": view.stale_count,
        "abandoned_count": view.abandoned_count,
        "avg_cycle_time_days": view.avg_cycle_time_days,
        "avg_pr_age_days": view.avg_pr_age_days,
        "workstreams": [
            {
                "workstream_id": item.workstream_id,
                "workstream_name": item.workstream_name,
                "active_count": item.active_count,
                "features_in_flight": item.features_in_flight,
                "completed_count": item.completed_count,
                "stale_count": item.stale_count,
                "abandoned_count": item.abandoned_count,
                "avg_cycle_time_days": item.avg_cycle_time_days,
                "avg_pr_age_days": item.avg_pr_age_days,
            }
            for item in view.workstreams
        ],
    }


def _workstream_progress_payload(view: WorkstreamProgressView) -> dict[str, object]:
    return {
        "workstream_id": view.workstream_id,
        "workstream_name": view.workstream_name,
        "as_of": view.as_of.isoformat(),
        "rag": view.rag.value,
        "source": view.source.value,
        "confidence": view.confidence,
        "percent_complete": view.percent_complete,
        "total_tasks": view.total_tasks,
        "green_tasks": view.green_tasks,
        "amber_tasks": view.amber_tasks,
        "red_tasks": view.red_tasks,
        "unknown_tasks": view.unknown_tasks,
        "factors": [
            {"description": _clip(factor.description), "contributes": factor.contributes.value}
            for factor in view.factors[:5]
        ],
        "red_and_amber_tasks": [
            {
                "id": task.id,
                "name": task.name,
                "rag": task.rag.value,
                "source": task.source.value,
                "confidence": task.confidence,
                "deadline": _iso(task.deadline),
            }
            for task in sorted(
                (task for task in view.tasks if task.rag in {Rag.RED, Rag.AMBER}),
                key=lambda task: _RAG_ORDER[task.rag],
            )[:10]
        ],
    }


def _portfolio_heatmap_payload(
    view: PortfolioHeatmapView,
    names: Mapping[str, str],
    *,
    kinds: set[str],
    limit: int,
) -> dict[str, object]:
    cells = sorted(
        (cell for cell in view.cells if not kinds or cell.entity_ref.kind.value in kinds),
        key=lambda cell: (_RAG_ORDER[cell.rag], cell.entity_ref.kind.value, cell.entity_ref.id),
    )
    return {
        "as_of": view.as_of.isoformat(),
        "cell_count": len(cells),
        "cells": [
            {
                "kind": cell.entity_ref.kind.value,
                "id": cell.entity_ref.id,
                "name": names.get(cell.entity_ref.id),
                "rag": cell.rag.value,
                "source": cell.source.value,
                "why": _clip(cell.why),
                "why_ref": {"kind": cell.source_ref.kind.value, "id": cell.source_ref.id},
            }
            for cell in cells[:limit]
        ],
    }


def _risk_payload(risk: RiskFinding, names: Mapping[str, str]) -> dict[str, object]:
    return {
        "severity": risk.severity.value,
        "rule": risk.rule_id.value,
        "entity_kind": risk.entity_ref.kind.value,
        "entity_id": risk.entity_ref.id,
        "entity_name": names.get(risk.entity_ref.id),
        "workstream_id": risk.workstream_id,
        "workstream_name": names.get(risk.workstream_id) if risk.workstream_id else None,
        "reason": _clip(risk.reason),
        "evidence": risk.evidence.identifier,
        "days_open": risk.age_days,
        "detected_on": risk.detected_at.date().isoformat(),
        "owner_id": risk.owner_id,
        "owner_name": names.get(risk.owner_id) if risk.owner_id else None,
        "owner_says": _clip(risk.owner_status_summary),
        "owner_status_source": (
            risk.owner_status_source.value if risk.owner_status_source is not None else None
        ),
        "owner_status_as_of": _iso(risk.owner_status_as_of),
        "owner_has_blockers": risk.owner_status_has_blockers,
        "watermelon": risk.is_watermelon,
    }


def _drift_payload(finding: DriftFinding, names: Mapping[str, str]) -> dict[str, object]:
    return {
        "severity": finding.severity.value,
        "kind": finding.kind.value,
        "entity_kind": finding.entity_ref.kind.value,
        "entity_id": finding.entity_ref.id,
        "entity_name": names.get(finding.entity_ref.id),
        "workstream_id": finding.workstream_id,
        "reason": _clip(finding.reason),
        "detected_on": finding.detected_at.date().isoformat(),
        "owner_id": finding.owner_id,
        "owner_name": names.get(finding.owner_id) if finding.owner_id else None,
        "stated_source": finding.stated_source.value if finding.stated_source else None,
        "evidence": finding.evidence.identifier if finding.evidence is not None else None,
    }


async def _node_names(repository: GraphRepository, tenant_id: str) -> dict[str, str]:
    return {node.id: node.name for node in await repository.list_nodes(tenant_id)}


async def _pods(
    repository: GraphRepository, tenant_id: str, pod_id: JsonScalar
) -> Sequence[GraphNode] | str:
    """The pod asked about, or every pod; an error message when the id is not a pod."""
    requested = _string_argument(pod_id)
    if requested is None:
        return (await repository.list_nodes(tenant_id, NodeKind.POD))[:_MAX_PODS]
    node = await repository.get_node(tenant_id, requested)
    if node is None:
        return f"pod {requested} not found; search_graph_nodes with kinds ['pod'] finds its id"
    if node.kind is not NodeKind.POD:
        return f"{requested} is a {node.kind.value}, not a pod"
    return (node,)


def _clip(text: str | None) -> str | None:
    """Bound free text so one long summary cannot crowd out the rest of a result."""
    if text is None or len(text) <= _MAX_TEXT_CHARS:
        return text
    return text[: _MAX_TEXT_CHARS - 1].rstrip() + "…"


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _snapshot_date(value: JsonScalar, as_of: date) -> date:
    """The day a snapshot tool reads: the as-of date, or an earlier one asked for.

    A question asked as of a day cannot see past it, so a later date -- the
    model's own idea of today, say -- falls back to the as-of date.
    """
    requested = _optional_date(value)
    return requested if requested is not None and requested <= as_of else as_of


def _required_string(value: JsonScalar, field: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"{field} must be provided")


def _string_argument(value: JsonScalar) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_list_argument(value: JsonScalar) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in (_string_argument(item) for item in value) if item]
    item = _string_argument(value)
    return [item] if item else []


def _kinds_argument(value: JsonScalar) -> list[str]:
    return _string_list_argument(value)


def _edge_kinds_argument(value: JsonScalar) -> list[EdgeKind]:
    """Parse requested edge kinds, ignoring any the graph does not have."""
    kinds: list[EdgeKind] = []
    for item in _string_list_argument(value):
        try:
            kinds.append(EdgeKind(item.strip().lower()))
        except ValueError:
            continue
    return kinds


def _optional_date(value: JsonScalar) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _bounded_int(
    value: JsonScalar,
    *,
    default: int,
    maximum: int,
) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(value, maximum)
    return min(default, maximum)


def _metadata_text(node: GraphNode) -> str:
    values = [str(value).lower() for value in node.metadata.values() if value is not None]
    return " ".join([node.id.lower(), node.name.lower(), *values])
