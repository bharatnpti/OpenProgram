from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
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
    TreeNodeView,
    WorkstreamProgressView,
)
from core.application.portfolio_feed_service import (
    DEFAULT_FEED_SOURCES,
    PortfolioFeedService,
    state_label,
)
from core.application.risk_service import RiskService
from core.application.rollup_service import NO_WORK_REASON
from core.application.status_summaries import NO_REPLY_BLOCKER
from core.domain.auth import Principal
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    JsonScalar,
    NodeKind,
    workstreams_in_use,
)
from core.domain.llm import LlmMessage, LlmRequest
from core.domain.risk import DriftFinding, RiskFinding
from core.domain.rollup import FactorKind, Rag, RollupFactor
from core.domain.status import StatusSource
from core.ports.llm import LlmProvider
from core.ports.repositories import GraphRepository, TimeSeriesRepository
from core.ports.tools import AgentTool

# How an answer reads. Answers used to be a paragraph that restated the
# question, lumped a partial update in with no reply at all, closed on a
# generic "follow up with ..." and cited people by chat id. Each rule stands
# alone so a test can hold the prompt to it. They also called GitLab merge
# requests "merged PRs": the facts, risk rules and flow fields Ask reads say
# pr or pull_request whatever the provider, and Ask cannot tell which one it is.
ANSWER_FORMAT_RULES: tuple[str, ...] = (
    "Shape: one verdict line that answers the question in a few words (e.g. 'Digital "
    "Platform Program is red because:'), then at most 4 bullet lines starting with '• ', "
    "one concrete driver each, then optionally one line on what is fine. 80 words at most.",
    "Name people by display name, issues by key (CHK-8), merge requests by the ref the "
    "data gives (storefront-web !1), and programs, projects, workstreams and pods by name. "
    "One bullet per kind of driver, naming everyone it applies to, e.g. 'Partial updates: "
    "Omar Haddad, Ira Novak · no reply: Hana Kobayashi'.",
    "Call a merge request a merge request or MR, e.g. '2 merged MRs', never a PR or pull "
    "request -- even where a tool's text, type or field name says PR, pr or pull_request.",
    "Never put a raw id in answer: no chat user ids such as U0AA1OMAR01 and no node ids "
    "such as pod-data or program-platform; an issue key is the one id that reads as a "
    "name. Ids go in references only, copied exactly as the tools return them: the id of "
    "every person, issue, program, project, workstream and pod the answer names.",
    "Ground every bullet in the rollup factors and signals the tools return, in their own "
    "terms: 'partial' is a partial update and 'no reply' is no reply, so never merge or "
    "upgrade one into the other; give counts exactly (1 open blocker), and never call "
    "something blocked or late unless a factor or signal says so.",
    "No filler, no preamble, no restating the question, and no recommendations or next "
    "steps unless the question asks what to do.",
    "If data you need is missing or a tool comes back empty, say so in one line instead "
    "of guessing.",
)

ASK_SYSTEM_PROMPT = (
    "You answer program-management questions over a delivery graph. "
    "You know nothing about this tenant's programs, people or status except what "
    "the provided tools return, so look the facts up before answering. "
    "Questions about health -- what is red, amber, at risk, blocked, stuck or "
    "behind -- are answered from the status and risk tools: call them before you "
    "say any status is unavailable, and say which day the status is for. "
    "Why something has its colour, or needs attention, is answered from "
    "status_reasons: the rollup's own factors, and the open risk and drift signals "
    "beneath it. "
    "Relationships -- who is assigned to what, who belongs to which pod, what "
    "contains what -- live on edges, so call graph_neighbors before reporting that "
    "something has none, and never tell the user their data is missing or needs "
    "updating when you have not traversed its edges. "
    "Never mention raw DM/reply content. "
    "Write the answer to these rules: " + " ".join(ANSWER_FORMAT_RULES) + " "
    "Once you have the facts, reply with a single JSON object and nothing else: "
    "answer holds the text, with a newline between lines, and references an array "
    "of the node ids it rests on. Do not restate references inside answer."
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
_MAX_REASONS = 15
_MAX_PARTS = 20
_MAX_SIGNALS = 10
_RAG_ORDER: dict[Rag, int] = {Rag.RED: 0, Rag.AMBER: 1, Rag.UNKNOWN: 2, Rag.GREEN: 3}
_AS_OF_PARAMETER: Mapping[str, object] = {
    "type": "string",
    "format": "date",
    "description": "An earlier day to look at. Leave unset for today.",
}

# Whose reasons a principal may read, per node kind: the capabilities the REST
# route serving those reasons checks -- /programs/{id}/tree,
# /projects/{id}/progress, /workstreams/{id}/progress and /pods/{id}/rollup.
_REASON_CAPABILITIES: Mapping[NodeKind, tuple[Capability, ...]] = {
    NodeKind.PROGRAM: (Capability.READ_PROGRAM_ROLLUP,),
    NodeKind.PROJECT: (Capability.READ_PROJECT_PROGRESS,),
    NodeKind.WORKSTREAM: (Capability.READ_PROJECT_PROGRESS,),
    NodeKind.POD: (Capability.READ_POD_BLOCKERS, Capability.READ_POD_CHECKINS),
}

# A person's check-in state in the words an answer may use. The collector
# writes an inferred, stale or unknown status only when someone has not
# answered a check-in, so those read "no reply"; unknown also covers a person
# nothing was ever recorded for, so it claims no more than "no status".
_CHECKIN_WORDS: Mapping[StatusSource, str] = {
    StatusSource.CONFIRMED: "confirmed",
    StatusSource.PARTIAL: "partial",
    StatusSource.INFERRED: "no reply",
    StatusSource.STALE: "no reply",
    StatusSource.UNKNOWN: "no status",
}
_NO_REPLY_SAYS = "No confirmed reply to the check-in."
_BULLET = re.compile(r"^\s*[-*•]\s+")

# The model is asked for a JSON object, yet a live reply can come back as
# prose ending in a "References: [...]" line, the object can carry raw
# newlines inside its strings or be cut off, and the answer can restate its
# references. Each shape is taken apart here, so the reader always gets the
# answer's own lines and the references always reach sources.
_JSON = json.JSONDecoder(strict=False)
_MAX_OBJECT_STARTS = 50
_REFERENCE_WORD = r"(?:references?|refs|sources?|citations?)"
# Only the plural ends a line, so "(source: Jira)" in a bullet stays.
_REFERENCES_WORD = r"(?:references|refs|sources|citations)"
# "References: a, b", "**Sources:** [a, b]", "- References (node ids):".
_REFERENCES_LINE = re.compile(
    rf"^\s*(?:[-*•]\s*)?[*_`]*{_REFERENCE_WORD}(?:\s*\([^)]*\))?[*_`]*\s*[:：]\s*[*_`]*"
    r"(?P<items>.*?)[*_`]*\s*$",
    re.IGNORECASE,
)
# "... green. (References: a, b)" or "... green. [Sources: a, b]".
_TRAILING_REFERENCES = re.compile(
    rf"\s*[(\[]\s*{_REFERENCES_WORD}\s*[:：]\s*(?P<items>[^()\[\]]*)[)\]]\s*\.?\s*$",
    re.IGNORECASE,
)
# "... green. References: [a, b]".
_TRAILING_REFERENCE_LIST = re.compile(
    rf"\s*\b{_REFERENCES_WORD}\s*[:：]\s*(?P<items>\[[^\[\]]*\])\s*\.?\s*$",
    re.IGNORECASE,
)
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(?P<item>.+?)\s*$")
_ANSWER_LABEL = re.compile(r"^\s*[*_]*answer[*_]*\s*[:：]\s*", re.IGNORECASE)
_FENCE_LINE = re.compile(r"^\s*```[\w-]*\s*$")
# A JSON reply that does not decode: cut off, or with a trailing comma.
_ANSWER_FIELD = re.compile(r'"answer"\s*:\s*"(?P<text>(?:[^"\\]|\\.)*)', re.IGNORECASE)
_REFERENCES_FIELD = re.compile(
    r'"(?:references|sources)"\s*:\s*(?P<items>\[[^\]]*\]?)', re.IGNORECASE
)
_ITEM_SPLIT = re.compile(r"\s*(?:[,;\n]|\s·\s)\s*")
_ITEM_EDGES = " \t\"'`*_"
# Text in brackets inside one item: "Zoe Almeida (U0AA1ZOE001)".
_BRACKETED = re.compile(r"[(\[]\s*(?P<inner>[^()\[\]]+?)\s*[)\]]")
# The node kinds an answer names, as the references rule lists them.
_NAMED_KINDS = frozenset(
    {
        NodeKind.PROGRAM,
        NodeKind.PROJECT,
        NodeKind.WORKSTREAM,
        NodeKind.POD,
        NodeKind.DEVELOPER,
        NodeKind.TASK,
        NodeKind.WORK_ITEM,
    }
)
_MIN_NAMED_LABEL = 3
# What the answer says when the reply held no answer text at all.
_NO_ANSWER = "No answer came back for this question. Please ask again."


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
class AskSource:
    """One reference, with the words a reader knows it by.

    ``label`` is a person's display name, an issue's key, a merge request's
    ref, or a program, project, workstream or pod name. It is None when the id
    matches no node -- a reference the model got wrong stays an id, never a
    guessed name.
    """

    id: str
    kind: NodeKind | None
    label: str | None


@dataclass(frozen=True, kw_only=True)
class AskResponseView:
    answer: str
    references: tuple[str, ...]
    tools_used: tuple[str, ...]
    trace_id: str
    sources: tuple[AskSource, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ParsedAnswer:
    """The model's reply taken apart, before any node is looked up.

    ``references`` is the references array of a JSON reply, as the model sent
    it. ``cited`` is what a "References: [...]" line listed instead -- a line
    cut out of ``answer``, so it is never shown -- still to be matched to nodes.
    """

    answer: str
    references: tuple[str, ...]
    cited: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SearchGraphNodesTool:
    """Name to id, over the nodes the views show.

    Workstreams are optional: one holding no task or work item on ``as_of``
    is left out, as the navigator and heat rows leave it out, so a list of
    workstreams never names an empty one. Asked for by its exact id or name,
    it still comes back, marked not in use with the reason, so Ask says it
    holds no work rather than that it does not exist.
    """

    tenant_id: str
    repository: GraphRepository
    as_of: date

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
        nodes = await self.repository.list_nodes(self.tenant_id, as_of=self.as_of)
        in_use = workstreams_in_use(
            nodes,
            await self.repository.list_edges(self.tenant_id, kind=EdgeKind.CONTAINS),
            self.as_of,
        )
        if kinds:
            kind_set = {NodeKind(kind) for kind in kinds}
            nodes = [node for node in nodes if node.kind in kind_set]
        query_value = (query or "").lower()
        if query_value:
            nodes = [
                node
                for node in nodes
                if query_value in node.id.lower()
                or query_value in node.name.lower()
                or _metadata_text(node).find(query_value) >= 0
            ]
        nodes = [
            node
            for node in nodes
            if node.kind is not NodeKind.WORKSTREAM
            or node.id in in_use
            or query_value in {node.id.lower(), node.name.lower()}
        ]
        matches = [_search_match(node, in_use) for node in nodes[:limit]]
        return json.dumps(matches, ensure_ascii=False)


# The details keys that hold a tracker or work-item state ("in_progress").
_STATE_DETAILS = ("state", "from_state", "to_state")


def _spoken_states(details: Mapping[str, JsonScalar]) -> dict[str, JsonScalar]:
    """A fact's details with its states in words ("In progress", not "in_progress").

    A model that is handed ``in_progress`` writes it into the answer as it came.
    """
    return {
        key: state_label(value)
        if key in _STATE_DETAILS and isinstance(value, str) and value
        else value
        for key, value in details.items()
    }


def _search_match(node: GraphNode, in_use: frozenset[str]) -> dict[str, object]:
    match: dict[str, object] = {
        "id": node.id,
        "kind": node.kind.value,
        "name": node.name,
        "metadata": dict(node.metadata),
    }
    if node.kind is NodeKind.WORKSTREAM and node.id not in in_use:
        match["in_use"] = False
        match["note"] = NO_WORK_REASON
    return match


@dataclass(frozen=True, kw_only=True)
class GraphNeighborsTool:
    """Edge traversal, without which no relationship question is answerable.

    `search_graph_nodes` finds a node but says nothing about what it connects
    to, so questions like "which tasks is this developer on" used to come back
    as "no tasks found in the current graph" -- blaming correct data for a
    missing tool. A workstream holding no work on ``as_of`` is no neighbor:
    workstreams are optional, and the views leave an empty one out.
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

        nodes = await self.repository.list_nodes(self.tenant_id, as_of=self.as_of)
        # An empty workstream is optional set-up, no relation worth naming.
        in_use = workstreams_in_use(
            nodes,
            await self.repository.list_edges(self.tenant_id, kind=EdgeKind.CONTAINS),
            self.as_of,
        )
        empty = {
            node.id for node in nodes if node.kind is NodeKind.WORKSTREAM and node.id not in in_use
        }
        found = [item for item in found if item[2] not in empty][:limit]
        wanted = {neighbor for _, _, neighbor in found}
        names = {node.id: node for node in nodes if node.id in wanted}
        node = await self.repository.get_node(self.tenant_id, node_id, as_of=self.as_of)
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
        "merge requests (MRs), check-in updates (counts only, never reply text), risks opened "
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
                    "description": (
                        "Optional fact sources to keep; vcs_pull_request holds the merge "
                        "requests (MRs)."
                    ),
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
                        "details": _spoken_states(item.details),
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
        "counts, average cycle time and merge request (MR) age, and each work item's state "
        "and age. "
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
        "average cycle time and merge request (MR) age. It describes movement and carries "
        "no RAG status."
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
class StatusReasonsTool:
    """Why a program, project, workstream or pod has its colour, in the rollup's words.

    Without it Ask saw one clipped "why" per heatmap cell, so "why does the
    program need attention?" came back as "multiple unresolved issues and
    unconfirmed statuses": two people who had replied in part lumped in with
    one who had not replied at all, and the actual blocker buried. This hands
    over every factor behind the colour, grouped as the Delivery panel groups
    them, each naming the person or issue it comes from -- and a person's
    check-in state in the words the data supports.

    The open risk and drift signals on anything beneath the node come along:
    the model was told to call open_risks beside it and, asked why the
    program needed attention, never did -- so two issues merged but still
    open in the tracker went unmentioned.

    ``kinds`` are the node kinds this principal may read reasons for: a kind
    whose REST route would refuse them comes back as an error, never data.
    The signals are the ones open_risks lists, behind the same aggregate read
    /ask itself requires, so carrying them here widens nothing.
    """

    tenant_id: str
    service: PersonaViewService
    risks: RiskService
    repository: GraphRepository
    as_of: date
    kinds: frozenset[NodeKind]

    name: str = "status_reasons"
    description: str = (
        "Why one program, project, workstream or pod has its RAG colour on one day, today "
        "unless as_of names an earlier one: every rollup factor behind the colour, worst "
        "first, in the rollup's own words. A factor names the people and issues it comes "
        "from, and a status factor carries their check-in state as the data has it: "
        "'partial' (replied without confirming blockers or ETA), 'no reply' (did not "
        "answer the check-in) or 'no status'. It also lists the colour of every project, "
        "workstream and pod underneath, and the open risk and drift signals on anything "
        "underneath -- such as an issue still open after its merge request merged. Use it "
        "for 'why is X red or amber' and 'why does X need attention'; search_graph_nodes "
        "turns a name into the node_id."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The program, project, workstream or pod to explain.",
                },
                "as_of": _AS_OF_PARAMETER,
            },
            "required": ["node_id"],
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        node_id = _required_string(arguments.get("node_id"), "node_id")
        as_of = _snapshot_date(arguments.get("as_of"), self.as_of)
        node = await self.repository.get_node(self.tenant_id, node_id, as_of=as_of)
        if node is None:
            raise GraphNotFound(
                f"{node_id} not found; search_graph_nodes finds a program, project, "
                "workstream or pod by name"
            )
        if node.kind not in _REASON_CAPABILITIES:
            return json.dumps(
                {
                    "error": f"{node_id} is a {node.kind.value}; status_reasons explains a "
                    "program, project, workstream or pod"
                }
            )
        if node.kind not in self.kinds:
            return json.dumps(
                {"error": f"the reasons behind a {node.kind.value}'s status are not available"}
            )
        tree = await self.service.program_tree(self.tenant_id, node_id, as_of)
        labels = {
            graph_node.id: node_label(graph_node)
            for graph_node in await self.repository.list_nodes(self.tenant_id, as_of=as_of)
        }
        beneath = {tree_node.id for tree_node in tree.nodes}
        risks = [
            risk
            for risk in await self.risks.portfolio_risks(self.tenant_id, as_of)
            if risk.entity_ref.id in beneath
        ]
        drift = [
            finding
            for finding in await self.risks.portfolio_drift(self.tenant_id, as_of)
            if finding.entity_ref.id in beneath
        ]
        signals = [
            (
                finding.severity,
                _signal_payload(
                    "drift",
                    finding.kind.value,
                    finding.severity,
                    finding.entity_ref.id,
                    finding.owner_id,
                    finding.reason,
                    labels,
                ),
            )
            for finding in drift
        ] + [
            (
                risk.severity,
                _signal_payload(
                    "risk",
                    risk.rule_id.value,
                    risk.severity,
                    risk.entity_ref.id,
                    risk.owner_id,
                    risk.reason,
                    labels,
                ),
            )
            for risk in risks
        ]
        signals.sort(key=lambda signal: _RAG_ORDER[signal[0]])  # worst first
        payload = _status_reasons_payload(tree.root_id, tree.nodes, labels, as_of)
        payload["open_signal_count"] = len(signals)
        payload["signals"] = [signal for _, signal in signals[:_MAX_SIGNALS]]
        return json.dumps(payload, ensure_ascii=False)


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
        "with kinds, e.g. ['workstream']. It gives one reason per node; for why one "
        "node has its colour, status_reasons gives all of them."
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
        names = await _node_names(self.repository, self.tenant_id, as_of)
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
        "A risk is signal-derived (a feature with no merge request (MR), an ageing MR, a "
        "stale work item) and carries its severity, days open, the reason, its evidence, what the "
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
        names = await _node_names(self.repository, self.tenant_id, self.as_of)
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


def _reason_kinds(principal: Principal) -> frozenset[NodeKind]:
    """The node kinds whose reasons this principal's REST routes would serve."""
    policy = AuthorizationPolicy()
    return frozenset(
        kind
        for kind, capabilities in _REASON_CAPABILITIES.items()
        if all(policy.can(principal, capability) for capability in capabilities)
    )


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
        nodes = await _nodes_by_any_id(self._graph_repository, principal.tenant_id, asked_for)
        labelled = _nodes_by_label(nodes)
        answer = _without_raw_ids(_tidy_lines(parsed.answer), nodes) or _NO_ANSWER
        references = _references(parsed, nodes, labelled) or _named_in(answer, labelled)
        return AskResponseView(
            answer=answer,
            references=references,
            tools_used=tuple(dict.fromkeys(calls)),
            trace_id=response.trace_id,
            sources=tuple(_source(reference, nodes) for reference in references),
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
            (
                SearchGraphNodesTool(tenant_id=tenant_id, repository=graph, as_of=as_of),
                _reads_aggregate,
            ),
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
            # /programs/{id}/tree, /projects|workstreams/{id}/progress, /pods/{id}/rollup:
            # offered when any of them answers, and per kind only where it does.
            (
                StatusReasonsTool(
                    tenant_id=tenant_id,
                    service=personas,
                    risks=self._risk_service,
                    repository=graph,
                    as_of=as_of,
                    kinds=_reason_kinds(principal),
                ),
                lambda asker: bool(_reason_kinds(asker)),
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
        "Answer in the required shape -- a verdict line, at most 4 '• ' bullets, 80 "
        "words at most -- naming people, issues and merge requests (MRs, never PRs) rather "
        "than ids, and "
        "list the id of every node the answer names in references. "
        f"Question: {question}"
    )


def _parse_answer(text: str) -> ParsedAnswer:
    """Take a reply apart, whatever shape it came back in.

    A JSON object -- fenced, wrapped in prose, or with raw newlines in its
    strings -- gives the answer and its references; one that does not decode
    still gives its answer field; anything else is the answer as written. In
    each case a "References: [...]" line is cut out of the answer and its items
    kept in ``cited``, so the reader never sees the line and sources still get
    what it listed. A fenced or prose-wrapped object used to be shown whole,
    and a prose reply showed its references line.
    """
    answer, references, beside = _reply_parts(text)
    answer, cited = _clean_answer(answer)
    _, cited_beside = _clean_answer(beside)
    return ParsedAnswer(
        answer=answer,
        references=_unique(references),
        cited=_unique((*cited, *cited_beside)),
    )


def _reply_parts(text: str) -> tuple[str, list[str], str]:
    """The answer text, the references array, and any prose beside the object."""
    found = _answer_object(text)
    if found is not None:
        reply, beside = found
        answer = _field_text(_field(reply, "answer"))
        references = _field_items(_field(reply, "references", "sources"))
        if answer:
            return answer, references, beside
        # An object of references only, after the answer in prose.
        return beside, references, ""
    salvaged = _ANSWER_FIELD.search(text) if "{" in text else None
    if salvaged is not None:
        listed = _REFERENCES_FIELD.search(text)
        return (
            _json_string(salvaged.group("text")),
            _reference_items(listed.group("items")) if listed is not None else [],
            "",
        )
    return text, [], ""


def _answer_object(text: str) -> tuple[Mapping[str, object], str] | None:
    """The first JSON object in a reply with an answer or references, and the rest.

    Strings may hold raw newlines (``strict=False``): models write them, and
    a strict decode dropped the whole object for one.
    """
    start = text.find("{")
    for _ in range(_MAX_OBJECT_STARTS):
        if start == -1:
            break
        try:
            decoded, end = _JSON.raw_decode(text, start)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(decoded, dict) and any(
                str(key).casefold() in {"answer", "references", "sources"} for key in decoded
            ):
                return decoded, f"{text[:start]}\n{text[end:]}"
        start = text.find("{", start + 1)
    return None


def _field(reply: Mapping[str, object], *names: str) -> object:
    folded = {str(key).casefold(): value for key, value in reply.items()}
    return next((folded[name] for name in names if name in folded), None)


def _field_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(line.strip() for line in value if isinstance(line, str) and line.strip())
    return ""


def _field_items(value: object) -> list[str]:
    """A references value as strings: ids, ``{"id": ...}`` objects, or one listed string."""
    if isinstance(value, str):
        return _reference_items(value)
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        item = entry.get("id") if isinstance(entry, Mapping) else entry
        if isinstance(item, str | int) and not isinstance(item, bool) and str(item).strip():
            items.append(str(item).strip())
    return items


def _json_string(raw: str) -> str:
    """A JSON string's body as text, even when the reply was cut off inside it."""
    try:
        decoded = _JSON.decode(f'"{raw}"')
    except json.JSONDecodeError:
        decoded = raw.rstrip("\\").replace("\\n", "\n").replace('\\"', '"')
    return decoded.strip() if isinstance(decoded, str) else ""


def _reference_items(text: str) -> list[str]:
    """The items of a written list: a JSON array, or entries split on commas."""
    listed = text.strip().rstrip(".").strip(_ITEM_EDGES)
    if listed.startswith("["):
        try:
            decoded = _JSON.decode(listed)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            return _field_items(decoded)
        listed = listed.strip("[]")
    items = (item.strip(_ITEM_EDGES).rstrip(".") for item in _ITEM_SPLIT.split(listed))
    return [item.strip(_ITEM_EDGES) for item in items if item.strip(_ITEM_EDGES)]


def _clean_answer(text: str) -> tuple[str, list[str]]:
    """The answer's own lines, and the items of every references line cut from it.

    A line that starts with References, Refs, Sources or Citations and a colon
    goes, with the list under it when it has none of its own, and a "[" list
    that runs over several lines goes whole; a trailing "(References: ...)" or
    "References: [...]" goes from the end of a line. Code fences and a leading
    "Answer:" go too.
    """
    kept: list[str] = []
    cited: list[str] = []
    for line, listed in _reference_lines(text.splitlines()):
        if listed is not None:
            cited.extend(_reference_items(listed))
            continue
        trailing = _TRAILING_REFERENCES.search(line) or _TRAILING_REFERENCE_LIST.search(line)
        if trailing is not None:
            cited.extend(_reference_items(trailing.group("items")))
            line = line[: trailing.start()]
        kept.append(line)
    return _ANSWER_LABEL.sub("", "\n".join(kept).strip(), count=1).strip(), cited


def _reference_lines(lines: Iterable[str]) -> Iterator[tuple[str, str | None]]:
    """Each line of a reply as ``(line, None)``, or ``("", list)`` for a references list.

    A references heading and the bullets or ``[...]`` under a bare one belong to
    the list. So does an array written one id to a line, as a model pretty-prints
    it: "References: [" or a bare heading and a "[" line open it, and it runs to
    the line that closes it, or to the end of the reply when that was cut off.
    Its lines are given whole, newlines kept, for ``_reference_items`` to read.
    """
    listing = False
    opened: list[str] = []
    for line in lines:
        if _FENCE_LINE.match(line):
            continue
        if opened or (listing and _opens_list(line)):
            opened.append(line)
            if not _is_open("\n".join(opened)):
                yield "", "\n".join(opened)
                opened = []
            continue
        if listing:
            entry = _LIST_ITEM.match(line)
            if entry is not None or line.strip().startswith("[") or not line.strip():
                yield "", entry.group("item") if entry else line
                continue
            listing = False
        heading = _REFERENCES_LINE.match(line)
        if heading is None:
            yield line, None
            continue
        items = heading.group("items")
        if _opens_list(items):
            opened = [items]
        else:
            yield "", items
            listing = not items.strip()
    if opened:
        yield "", "\n".join(opened)


def _opens_list(text: str) -> bool:
    """Whether ``text`` opens a "[" list that it does not close itself."""
    return text.strip().startswith("[") and _is_open(text)


def _is_open(text: str) -> bool:
    """Whether ``text`` has more "[" than "]", so a list in it is still being written."""
    return text.count("[") > text.count("]")


def _unique(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in items if item.strip()))


def _nodes_by_label(nodes: Mapping[str, GraphNode]) -> dict[str, GraphNode]:
    """Each label exactly one node carries, casefolded; a shared label names nobody."""
    owners: dict[str, dict[str, GraphNode]] = {}
    for node in nodes.values():
        label = node_label(node)
        if label is not None and label.strip():
            owners.setdefault(label.strip().casefold(), {})[node.id] = node
    return {label: next(iter(found.values())) for label, found in owners.items() if len(found) == 1}


def _node_id(
    item: str, nodes: Mapping[str, GraphNode], labelled: Mapping[str, GraphNode]
) -> str | None:
    """The node an item names: its id as written, else the one node with that label.

    "Zoe Almeida (U0AA1ZOE001)" is tried whole, then by the bracketed part,
    then without it.
    """
    candidates = (
        item,
        *(match.group("inner") for match in _BRACKETED.finditer(item)),
        _BRACKETED.sub("", item),
    )
    for candidate in (candidate.strip(_ITEM_EDGES) for candidate in candidates):
        if not candidate:
            continue
        if candidate in nodes:
            return candidate
        node = labelled.get(candidate.casefold())
        if node is not None:
            return node.id
    return None


def _references(
    parsed: ParsedAnswer, nodes: Mapping[str, GraphNode], labelled: Mapping[str, GraphNode]
) -> tuple[str, ...]:
    """The answer's references, as node ids wherever a node matches.

    An id the model wrote stays as written; one it wrote as a label becomes
    the id of the one node with that label. A references-array entry that
    matches nothing stays, so it shows as an id rather than a guessed name; an
    item of a References line that matches nothing is dropped, as that line is
    free text. A line item adds only a node the array does not already name.
    """
    references = [_node_id(item, nodes, labelled) or item for item in parsed.references]
    named = {nodes[reference].id for reference in references if reference in nodes}
    for item in parsed.cited:
        found = _node_id(item, nodes, labelled)
        if found is not None and nodes[found].id not in named:
            references.append(found)
            named.add(nodes[found].id)
    return tuple(dict.fromkeys(references))


def _named_in(answer: str, labelled: Mapping[str, GraphNode]) -> tuple[str, ...]:
    """The nodes an answer names by label, in the order it names them.

    Only for a reply that gave no reference at all, so its sources are not
    empty. A label counts only whole and as written, only when exactly one
    node carries it, and only for the kinds the references rule lists; a
    longer label wins over one inside it ("Digital Platform Program" over a
    "Platform" pod).
    """
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    candidates = sorted(
        (
            (label, node)
            for node in labelled.values()
            if node.kind in _NAMED_KINDS
            and (label := node_label(node)) is not None
            and len(label) >= _MIN_NAMED_LABEL
            and label in answer
        ),
        key=lambda pair: -len(pair[0]),
    )
    for label, node in candidates:
        first: int | None = None
        for match in re.finditer(rf"(?<![\w-]){re.escape(label)}(?![\w-])", answer):
            start, end = match.span()
            if any(start < other_end and other_start < end for other_start, other_end in taken):
                continue
            taken.append((start, end))
            first = start if first is None else first
        if first is not None:
            found.append((first, node.id))
    return tuple(dict.fromkeys(node_id for _, node_id in sorted(found)))


def _tidy_lines(answer: str) -> str:
    """One bullet marker, no trailing spaces, no blank lines between lines."""
    lines = (_BULLET.sub("• ", line.rstrip()) for line in answer.strip().splitlines())
    return "\n".join(line for line in lines if line.strip())


def node_label(node: GraphNode) -> str | None:
    """The words a reader knows a node by -- never its raw id, unless that id is the name.

    An issue's key is how everyone refers to it, so a task reads as its key; a
    merge request as its ref; a person as their display name; everything else
    as its name. A person whose name is only their id has no label.
    """
    if node.kind is NodeKind.TASK:
        return _string_metadata(node, "key") or node.id
    if node.kind is NodeKind.WORK_ITEM:
        repo = _string_metadata(node, "repo")
        pr_id = _string_metadata(node, "pr_id")
        if repo and pr_id:
            return f"{repo}#{pr_id}"
    name = node.name.strip()
    if not name or (node.kind is NodeKind.DEVELOPER and name == node.id):
        return None
    return name


async def _nodes_by_any_id(
    repository: GraphRepository, tenant_id: str, as_of: date
) -> dict[str, GraphNode]:
    """Every node of ``as_of`` by its id, and each member also by their chat id.

    A model may cite a person by the chat id a tool showed it. A member's own
    node id always wins over another member's chat id.
    """
    nodes = await repository.list_nodes(tenant_id, as_of=as_of)
    by_id: dict[str, GraphNode] = {}
    for node in nodes:
        chat_id = _string_metadata(node, "chat_external_id")
        if node.kind is NodeKind.DEVELOPER and chat_id is not None:
            by_id.setdefault(chat_id, node)
    by_id.update({node.id: node for node in nodes})
    return by_id


def _source(reference: str, nodes: Mapping[str, GraphNode]) -> AskSource:
    node = nodes.get(reference)
    if node is None:
        return AskSource(id=reference, kind=None, label=None)
    return AskSource(id=reference, kind=node.kind, label=node_label(node))


def _looks_like_an_id(value: str) -> bool:
    """Ids such as U0AA1OMAR01 or pod-data, not a word or a bare number."""
    return (
        len(value) >= 4
        and " " not in value
        and not value.isdigit()
        and any(char.isdigit() or char in "-_:" for char in value)
    )


def _without_raw_ids(answer: str, nodes: Mapping[str, GraphNode]) -> str:
    """Swap any raw id the model wrote into the answer for the node's label.

    The prompt forbids ids in the text; this is the backstop, so a chat id
    or a node id like pod-data never reaches the reader even when the model
    slips. An id that is its own label -- an issue key -- is left alone, and
    "Ana (U123)" becomes "Ana", not the name twice.
    """
    replacements = sorted(
        (
            (raw_id, label)
            for raw_id, node in nodes.items()
            if raw_id in answer
            and _looks_like_an_id(raw_id)
            and (label := node_label(node)) is not None
            and label != raw_id
        ),
        key=lambda item: -len(item[0]),
    )
    for raw_id, label in replacements:
        escaped = re.escape(raw_id)
        # A literal, so a backslash in a name is never read as a group reference.
        literal = label.replace("\\", "\\\\")
        answer = re.sub(rf"{re.escape(label)}\s*[(\[]\s*`?{escaped}`?\s*[)\]]", literal, answer)
        answer = re.sub(rf"(?<![\w/-])(`?){escaped}\1(?![\w/-])", literal, answer)
    return answer


def _status_reasons_payload(
    root_id: str,
    tree_nodes: Sequence[TreeNodeView],
    labels: Mapping[str, str | None],
    as_of: date,
) -> dict[str, object]:
    """A node's colour and the reasons behind it, worst first, each naming its source.

    Reasons are grouped the way the Delivery panel groups them: factors that
    say the same thing become one reason naming everyone it applies to.
    """
    context = _ReasonContext(
        root_id=root_id,
        nodes={node.id: node for node in tree_nodes},
        labels=labels,
        # A blocker attributed to an issue cites the issue; its owner is the
        # person whose own status carries the same blocker.
        owners={
            factor.blocker_id: node
            for node in tree_nodes
            if node.kind is NodeKind.DEVELOPER
            for factor in node.factors
            if factor.blocker_id
        },
    )
    root = context.nodes[root_id]
    factors = [factor for factor in root.factors if factor.contributes is not Rag.GREEN] or list(
        root.factors
    )
    factors.sort(key=lambda factor: _RAG_ORDER[factor.contributes])
    reasons: dict[tuple[str, str, str, str | None], dict[str, object]] = {}
    for factor in factors:
        context.add(reasons, factor)
    blockers = {
        factor.blocker_id or factor.description
        for factor in root.factors
        if factor.kind is FactorKind.BLOCKER and not _is_no_reply_placeholder(factor)
    }
    parts = sorted(
        (
            node
            for node in tree_nodes
            if node.id != root_id
            and node.kind in {NodeKind.PROJECT, NodeKind.WORKSTREAM, NodeKind.POD}
        ),
        key=lambda node: (_RAG_ORDER[node.rag or Rag.UNKNOWN], node.kind.value, node.name),
    )
    return {
        "node": {"id": root.id, "kind": root.kind.value, "name": root.name},
        "as_of": as_of.isoformat(),
        "rag": (root.rag or Rag.UNKNOWN).value,
        "source": (root.source or StatusSource.UNKNOWN).value,
        "open_blocker_count": len(blockers),
        "reasons": list(reasons.values())[:_MAX_REASONS],
        "parts": [
            {
                "kind": node.kind.value,
                "id": node.id,
                "name": node.name,
                "rag": (node.rag or Rag.UNKNOWN).value,
            }
            for node in parts[:_MAX_PARTS]
        ],
    }


@dataclass(frozen=True, kw_only=True)
class _ReasonContext:
    root_id: str
    nodes: Mapping[str, TreeNodeView]
    labels: Mapping[str, str | None]
    owners: Mapping[str, TreeNodeView]

    def add(
        self,
        reasons: dict[tuple[str, str, str, str | None], dict[str, object]],
        factor: RollupFactor,
    ) -> None:
        """Fold one factor into the reason that says the same thing.

        The placeholder a silent person's status used to carry ("Blocker: no
        confirmed reply") is a check-in state, not a blocker: the rollup
        stopped counting it, but rows recorded before still hold it, and read
        as written it turned one non-reply into an extra open blocker.
        """
        placeholder = _is_no_reply_placeholder(factor)
        kind = FactorKind.STATUS if placeholder else factor.kind
        says = _NO_REPLY_SAYS if placeholder else (_clip(factor.description) or "")
        cited = self.nodes.get(factor.source_ref.id)
        person = cited if cited is not None and cited.kind is NodeKind.DEVELOPER else None
        if person is None and factor.blocker_id:
            person = self.owners.get(factor.blocker_id)
        checkin = (
            _CHECKIN_WORDS[person.source or StatusSource.UNKNOWN]
            if person is not None and kind is FactorKind.STATUS
            else None
        )
        reason = reasons.setdefault(
            (factor.contributes.value, kind.value, says, checkin),
            {"contributes": factor.contributes.value, "kind": kind.value, "says": says},
        )
        if checkin is not None:
            reason["checkin"] = checkin
        if person is not None:
            _append(reason, "people", {"id": person.id, "name": self.labels.get(person.id)})
        # What the factor is about besides a person: the issue a blocker is on,
        # a task, a workstream's target date. Never the node being explained.
        subject = factor.work_item_ref or (
            factor.source_ref if factor.source_ref.kind is not NodeKind.DEVELOPER else None
        )
        if subject is not None and subject.id != self.root_id:
            _append(
                reason,
                "about",
                {
                    "id": subject.id,
                    "kind": subject.kind.value,
                    "label": self.labels.get(subject.id),
                },
            )


def _signal_payload(
    signal: str,
    kind: str,
    severity: Rag,
    entity_id: str,
    owner_id: str | None,
    reason: str,
    labels: Mapping[str, str | None],
) -> dict[str, object]:
    """A risk or drift finding, about an item by its label and owned by a named person."""
    return {
        "signal": signal,
        "type": kind,
        "severity": severity.value,
        "about": {"id": entity_id, "label": labels.get(entity_id)},
        "owner": {"id": owner_id, "name": labels.get(owner_id)} if owner_id else None,
        "says": _clip(reason),
    }


def _append(reason: dict[str, object], key: str, entry: dict[str, object]) -> None:
    entries = reason.setdefault(key, [])
    if isinstance(entries, list) and entry not in entries:
        entries.append(entry)


def _is_no_reply_placeholder(factor: RollupFactor) -> bool:
    return (
        factor.kind is FactorKind.BLOCKER
        and factor.description.removeprefix("Blocker:").strip().lower() == NO_REPLY_BLOCKER
    )


def _string_metadata(node: GraphNode, key: str) -> str | None:
    value = node.metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


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
                "state": state_label(item.state),
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
            _workstream_factor_payload(factor, view.workstream_id, view.source_names)
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


def _workstream_factor_payload(
    factor: RollupFactor, workstream_id: str, names: Mapping[str, str]
) -> dict[str, object]:
    """A factor, and who or what it is about when that is not the workstream itself."""
    payload: dict[str, object] = {
        "description": _clip(factor.description),
        "contributes": factor.contributes.value,
    }
    cited = factor.source_ref
    if cited.id == workstream_id:
        return payload
    # An issue goes by its key; anything else by its name, never by a raw id.
    about = cited.id if cited.kind is NodeKind.TASK else names.get(cited.id)
    if about is not None and (cited.kind is NodeKind.TASK or about != cited.id):
        payload["about"] = about
    return payload


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


async def _node_names(repository: GraphRepository, tenant_id: str, as_of: date) -> dict[str, str]:
    """Names by node id as of ``as_of``, leaving out a person whose only name is their raw id."""
    return {
        node.id: node.name
        for node in await repository.list_nodes(tenant_id, as_of=as_of)
        if node.name and not (node.kind is NodeKind.DEVELOPER and node.name == node.id)
    }


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
