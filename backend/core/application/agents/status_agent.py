from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypedDict, cast

from langgraph.graph import StateGraph

from core.domain.llm import LlmRequest, LlmResponse
from core.ports.llm import LlmProvider


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
    def __init__(self, llm_provider: LlmProvider, model: str) -> None:
        self._llm_provider = llm_provider
        self._model = model
        self._compiled_graph: StatusAgentGraph | None = None

    async def summarize_status(
        self,
        *,
        tenant_id: str,
        developer_name: str,
        context: str,
        correlation_id: str,
    ) -> LlmResponse:
        prompt = (
            "Summarize this developer status for a program manager. "
            "Return concise progress, blocker, and risk bullets. "
            f"Developer: {developer_name}. Context: {context}"
        )
        return await self._llm_provider.complete(
            LlmRequest(
                tenant_id=tenant_id,
                prompt=prompt,
                model=self._model,
                correlation_id=correlation_id,
                metadata={"agent": "status_agent"},
            )
        )

    async def __call__(self, state: Mapping[str, str]) -> dict[str, str]:
        result = await self._run_node(_state_from_mapping(state))
        return {"summary": result["summary"], "trace_id": result["trace_id"]}

    def graph(self) -> StatusAgentGraph:
        if self._compiled_graph is None:
            graph = StateGraph(StatusAgentState)
            graph.add_node("summarize", self._run_node)
            graph.set_entry_point("summarize")
            graph.set_finish_point("summarize")
            self._compiled_graph = cast(StatusAgentGraph, graph.compile())
        return self._compiled_graph

    async def _run_node(self, state: StatusAgentState) -> StatusAgentState:
        response = await self.summarize_status(
            tenant_id=state["tenant_id"],
            developer_name=state["developer_name"],
            context=state["context"],
            correlation_id=state["correlation_id"],
        )
        return {"summary": response.text, "trace_id": response.trace_id}


def _state_from_mapping(state: Mapping[str, str]) -> StatusAgentState:
    return {
        "tenant_id": state["tenant_id"],
        "developer_name": state["developer_name"],
        "context": state["context"],
        "correlation_id": state["correlation_id"],
    }
