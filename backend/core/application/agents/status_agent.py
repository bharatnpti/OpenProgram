from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol, TypedDict, cast

from langgraph.graph import StateGraph
from opentelemetry import trace

from core.application.agents.tool_loop import ToolCallingAgent
from core.domain.llm import LlmMessage, LlmRequest, LlmResponse
from core.ports.llm import LlmProvider
from core.ports.tools import AgentTool

_tracer = trace.get_tracer("pulseops.application.agents.status_agent")
STATUS_AGENT_SYSTEM_PROMPT = (
    "Summarize developer status for a program manager. Use only the supplied context and return "
    "concise progress, blocker, and risk bullets."
)


class StatusAgentState(TypedDict, total=False):
    tenant_id: str
    developer_name: str
    context: str
    correlation_id: str
    summary: str
    trace_id: str


class StatusAgentGraph(Protocol):
    async def ainvoke(self, input: StatusAgentState) -> StatusAgentState: ...


class StatusAgentNode:
    def __init__(
        self,
        llm_provider: LlmProvider,
        model: str,
        tool_agent: ToolCallingAgent | None = None,
    ) -> None:
        self._llm_provider = llm_provider
        self._model = model
        self._tool_agent = tool_agent
        self._compiled_graph = self._compile_graph()

    async def summarize_status(
        self,
        *,
        tenant_id: str,
        developer_name: str,
        context: str,
        correlation_id: str,
        tools: Iterable[AgentTool] = (),
    ) -> LlmResponse:
        with _tracer.start_as_current_span("status_agent.summarize_status"):
            prompt = (
                "Summarize the developer status from the supplied message context. "
                "Return only the summary."
            )
            request = LlmRequest(
                tenant_id=tenant_id,
                prompt=prompt,
                model=self._model,
                correlation_id=correlation_id,
                system=STATUS_AGENT_SYSTEM_PROMPT,
                messages=(
                    LlmMessage(
                        role="user",
                        content=f"Developer: {developer_name}\nContext:\n{context}",
                    ),
                ),
                metadata={
                    "agent": "status_agent",
                    "purpose": "summarize_status",
                    "developer_name": developer_name,
                },
            )
            if self._tool_agent is not None:
                return await self._tool_agent.run(request, tools)
            return await self._llm_provider.complete(request)

    async def __call__(self, state: Mapping[str, str]) -> dict[str, str]:
        with _tracer.start_as_current_span("status_agent.call"):
            result = await self._run_node(_state_from_mapping(state))
            return {"summary": result["summary"], "trace_id": result["trace_id"]}

    def graph(self) -> StatusAgentGraph:
        return self._compiled_graph

    async def _run_node(self, state: StatusAgentState) -> StatusAgentState:
        with _tracer.start_as_current_span("status_agent.run_node"):
            response = await self.summarize_status(
                tenant_id=state["tenant_id"],
                developer_name=state["developer_name"],
                context=state["context"],
                correlation_id=state["correlation_id"],
            )
            return {"summary": response.text, "trace_id": response.trace_id}

    def _compile_graph(self) -> StatusAgentGraph:
        with _tracer.start_as_current_span("status_agent.compile_graph"):
            graph = StateGraph(StatusAgentState)
            graph.add_node("summarize", self._run_node)
            graph.set_entry_point("summarize")
            graph.set_finish_point("summarize")
            return cast(StatusAgentGraph, graph.compile())


def _state_from_mapping(state: Mapping[str, str]) -> StatusAgentState:
    return {
        "tenant_id": state["tenant_id"],
        "developer_name": state["developer_name"],
        "context": state["context"],
        "correlation_id": state["correlation_id"],
    }
