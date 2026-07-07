from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from core.application.agents.tool_loop import ToolCallingAgent
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
from core.application.portfolio_feed_service import PortfolioFeedService
from core.domain.graph import GraphNode, JsonScalar, NodeKind
from core.domain.llm import LlmMessage, LlmRequest
from core.ports.llm import LlmProvider
from core.ports.repositories import GraphRepository, TimeSeriesRepository
from core.ports.tools import AgentTool

ASK_SYSTEM_PROMPT = (
    "You answer program-management questions over a delivery graph. "
    "Use the provided tools to inspect nodes, flow metrics, portfolio heatmaps, and recent facts. "
    "Never mention raw DM/reply content. "
    "Return a single JSON object with keys answer, references, and tools_used."
)


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
    tools_used: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class SearchGraphNodesTool:
    tenant_id: str
    repository: GraphRepository

    name: str = "search_graph_nodes"
    description: str = (
        "Search graph nodes by text and optional kinds. Use this to find work items, "
        "workstreams, projects, pods, and developers."
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
                    "items": {"type": "string"},
                    "description": ("Optional NodeKind values to restrict the search."),
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
class RecentFactsTool:
    tenant_id: str
    repository: TimeSeriesRepository

    name: str = "recent_facts"
    description: str = (
        "Fetch the most recent safe timeline facts for a tenant. "
        "Use this to understand what changed recently."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "since_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "description": "How many days back to inspect.",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional fact sources such as work_item, issue, "
                        "vcs_commit, vcs_pull_request, checkin."
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
        since_days = _bounded_int(arguments.get("since_days"), default=7, maximum=30)
        limit = _bounded_int(arguments.get("limit"), default=10, maximum=50)
        sources = _string_list_argument(arguments.get("sources"))
        since = datetime.now(tz=UTC) - timedelta(days=since_days)
        feed = PortfolioFeedService(self.repository)
        view = await feed.feed(self.tenant_id, since=since, sources=sources or None, limit=limit)
        return json.dumps(
            [
                {
                    "source": item.source,
                    "kind": item.kind,
                    "summary": item.summary,
                    "entity_kind": item.entity_ref.kind.value,
                    "entity_id": item.entity_ref.id,
                    "observed_at": item.observed_at.isoformat(),
                    "details": dict(item.details),
                }
                for item in view.items
            ],
            ensure_ascii=False,
        )


@dataclass(frozen=True, kw_only=True)
class WorkstreamFlowTool:
    tenant_id: str
    service: FlowMetricsService

    name: str = "workstream_flow"
    description: str = "Fetch TPM/SM flow metrics for a single workstream."
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "workstream_id": {"type": "string"},
                "as_of": {"type": "string", "format": "date"},
            },
            "required": ["workstream_id"],
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        workstream_id = _required_string(arguments.get("workstream_id"), "workstream_id")
        as_of = _optional_date(arguments.get("as_of")) or date.today()
        view = await self.service.workstream_flow(self.tenant_id, workstream_id, as_of)
        return json.dumps(_workstream_flow_payload(view), ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class PortfolioFlowTool:
    tenant_id: str
    service: FlowMetricsService

    name: str = "portfolio_flow"
    description: str = "Fetch portfolio-wide flow metrics grouped by workstream."
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "as_of": {"type": "string", "format": "date"},
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        as_of = _optional_date(arguments.get("as_of")) or date.today()
        view = await self.service.portfolio_flow(self.tenant_id, as_of)
        return json.dumps(_portfolio_flow_payload(view), ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class WorkstreamProgressTool:
    tenant_id: str
    service: PersonaViewService

    name: str = "workstream_progress"
    description: str = "Fetch the current progress snapshot for a workstream."
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "workstream_id": {"type": "string"},
                "as_of": {"type": "string", "format": "date"},
            },
            "required": ["workstream_id"],
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        workstream_id = _required_string(arguments.get("workstream_id"), "workstream_id")
        as_of = _optional_date(arguments.get("as_of")) or date.today()
        view = await self.service.workstream_progress(self.tenant_id, workstream_id, as_of)
        return json.dumps(_workstream_progress_payload(view), ensure_ascii=False)


@dataclass(frozen=True, kw_only=True)
class PortfolioHeatmapTool:
    tenant_id: str
    service: PersonaViewService

    name: str = "portfolio_heatmap"
    description: str = "Fetch the portfolio heatmap for the current tenant."
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "as_of": {"type": "string", "format": "date"},
                "program_root_id": {"type": "string"},
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        as_of = _optional_date(arguments.get("as_of")) or date.today()
        program_root_id = _string_argument(arguments.get("program_root_id"))
        view = await self.service.portfolio_heatmap(self.tenant_id, as_of, program_root_id)
        return json.dumps(_portfolio_heatmap_payload(view), ensure_ascii=False)


class AskService:
    def __init__(
        self,
        llm_provider: LlmProvider,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
        flow_metrics_service: FlowMetricsService,
        persona_view_service: PersonaViewService,
        model: str,
        tool_agent: ToolCallingAgent | None = None,
    ) -> None:
        self._llm_provider = llm_provider
        self._graph_repository = graph_repository
        self._time_series_repository = time_series_repository
        self._flow_metrics_service = flow_metrics_service
        self._persona_view_service = persona_view_service
        self._model = model
        self._tool_agent = tool_agent or ToolCallingAgent(llm_provider=llm_provider)

    async def ask(
        self,
        *,
        tenant_id: str,
        question: str,
        correlation_id: str,
        as_of: date | None = None,
    ) -> AskResponseView:
        request = LlmRequest(
            tenant_id=tenant_id,
            prompt=_prompt(question),
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
                "as_of": (as_of or date.today()).isoformat(),
            },
        )
        tools = self._tools(tenant_id)
        response = await self._tool_agent.run(request, tools)
        parsed = _parse_answer(response.text)
        return AskResponseView(
            answer=parsed.answer,
            references=parsed.references,
            tools_used=parsed.tools_used,
            trace_id=response.trace_id,
        )

    def _tools(self, tenant_id: str) -> tuple[AgentTool, ...]:
        return (
            SearchGraphNodesTool(tenant_id=tenant_id, repository=self._graph_repository),
            RecentFactsTool(tenant_id=tenant_id, repository=self._time_series_repository),
            WorkstreamFlowTool(tenant_id=tenant_id, service=self._flow_metrics_service),
            PortfolioFlowTool(tenant_id=tenant_id, service=self._flow_metrics_service),
            WorkstreamProgressTool(tenant_id=tenant_id, service=self._persona_view_service),
            PortfolioHeatmapTool(tenant_id=tenant_id, service=self._persona_view_service),
        )


def _prompt(question: str) -> str:
    return (
        "Answer the user's question using the tools when needed. "
        "Prefer concise, specific answers with references to node ids and workstream ids. "
        f"Question: {question}"
    )


def _parse_answer(text: str) -> ParsedAnswer:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return ParsedAnswer(answer=text.strip(), references=(), tools_used=())
    answer = parsed.get("answer")
    references = parsed.get("references")
    tools_used = parsed.get("tools_used")
    return ParsedAnswer(
        answer=answer if isinstance(answer, str) and answer else text.strip(),
        references=tuple(str(item) for item in references) if isinstance(references, list) else (),
        tools_used=tuple(str(item) for item in tools_used) if isinstance(tools_used, list) else (),
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
    }


def _portfolio_heatmap_payload(view: PortfolioHeatmapView) -> dict[str, object]:
    return {
        "as_of": view.as_of.isoformat(),
        "rows": list(view.rows),
        "columns": list(view.columns),
        "cells": [
            {
                "row": cell.row,
                "column": cell.column,
                "entity_ref": {
                    "tenant_id": cell.entity_ref.tenant_id,
                    "kind": cell.entity_ref.kind.value,
                    "id": cell.entity_ref.id,
                },
                "rag": cell.rag.value,
                "source": cell.source.value,
                "why": cell.why,
                "source_ref": {
                    "tenant_id": cell.source_ref.tenant_id,
                    "kind": cell.source_ref.kind.value,
                    "id": cell.source_ref.id,
                },
            }
            for cell in view.cells
        ],
    }


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
