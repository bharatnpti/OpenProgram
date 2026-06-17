from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

import structlog
from opentelemetry import trace

from core.domain.llm import LlmRequest, LlmResponse, LlmTool, LlmToolCall, LlmToolResult
from core.ports.llm import LlmProvider
from core.ports.tools import AgentTool

_tracer = trace.get_tracer("pulseops.application.agents.tool_loop")
_logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ToolCallingAgent:
    llm_provider: LlmProvider
    max_tool_iterations: int = 3

    async def run(
        self,
        request: LlmRequest,
        tools: Iterable[AgentTool] = (),
    ) -> LlmResponse:
        tool_registry = {tool.name: tool for tool in tools}
        current_request = replace(request, tools=tuple(_llm_tool(tool) for tool in tools))
        previous_tool_calls: tuple[LlmToolCall, ...] = ()
        previous_tool_results: tuple[LlmToolResult, ...] = ()
        max_iterations = max(0, self.max_tool_iterations)

        for iteration in range(max_iterations + 1):
            with _tracer.start_as_current_span("tool_loop.complete") as span:
                span.set_attribute("tool_loop.iteration", iteration)
                span.set_attribute("tool_loop.tool_count", len(tool_registry))
                response = await self.llm_provider.complete(current_request)
                span.set_attribute("tool_loop.tool_call_count", len(response.tool_calls))
                cap_reached = bool(response.tool_calls) and iteration >= max_iterations
                if cap_reached:
                    span.set_attribute("tool_loop.cap_reached", True)

            if not response.tool_calls:
                return response
            if iteration >= max_iterations:
                _logger.warning(
                    "tool_loop_cap_reached",
                    correlation_id=request.correlation_id,
                    iteration=iteration,
                    tool_call_count=len(response.tool_calls),
                )
                return await self.llm_provider.complete(replace(current_request, tools=()))

            tool_results = await _run_tool_calls(response.tool_calls, tool_registry)
            previous_tool_calls = (*previous_tool_calls, *response.tool_calls)
            previous_tool_results = (*previous_tool_results, *tool_results)
            current_request = replace(
                request,
                tools=current_request.tools,
                tool_calls=previous_tool_calls,
                tool_results=previous_tool_results,
            )

        return response


async def _run_tool_calls(
    tool_calls: Iterable[LlmToolCall],
    tool_registry: Mapping[str, AgentTool],
) -> tuple[LlmToolResult, ...]:
    results: list[LlmToolResult] = []
    for tool_call in tool_calls:
        with _tracer.start_as_current_span("tool_loop.run_tool") as span:
            span.set_attribute("tool.name", tool_call.name)
            tool = tool_registry.get(tool_call.name)
            if tool is None:
                content = f"Tool {tool_call.name} is not available."
            else:
                content = await tool.run(tool_call.arguments)
            results.append(LlmToolResult(tool_call_id=tool_call.id, content=content))
    return tuple(results)


def _llm_tool(tool: AgentTool) -> LlmTool:
    return LlmTool(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
    )
