from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

import structlog
from opentelemetry import trace

from core.domain.llm import LlmRequest, LlmResponse, LlmTool, LlmToolCall, LlmToolResult
from core.ports.llm import LlmProvider
from core.ports.tools import AgentTool

_tracer = trace.get_tracer("openprogram.application.agents.tool_loop")
_logger = structlog.get_logger(__name__)

# What a tool call the cap left unrun says in place of its result. The answer
# at the cap used to be written as if the calls had never been asked for, and
# Ask said a project had no blockers while the read that would show them was
# one of them.
NOT_RUN = (
    "Not run: the step limit was reached before this read, so nothing is known of what "
    "it would have returned."
)


@dataclass(frozen=True, kw_only=True)
class ToolLoopResult:
    """The loop's last response, and the tool calls the cap left unrun."""

    response: LlmResponse
    #: Asked for after the last round the cap allows: never run, so never read.
    unrun: tuple[LlmToolCall, ...] = ()

    @property
    def cap_reached(self) -> bool:
        return bool(self.unrun)


@dataclass(frozen=True)
class ToolCallingAgent:
    llm_provider: LlmProvider
    max_tool_iterations: int = 3
    #: Added to the system prompt of the answer written at the cap, when set:
    #: how a caller's answer says what it could not check.
    cap_notice: str | None = None
    # Guardrails (docs/guardrails.md). Calls past the first few in one turn
    # are answered with a refusal, so every call id still gets its result.
    max_tool_calls_per_turn: int = 8
    # A tool's answer is cut to this many characters before the model reads it.
    max_tool_result_chars: int = 12_000

    async def run(
        self,
        request: LlmRequest,
        tools: Iterable[AgentTool] = (),
    ) -> LlmResponse:
        return (await self.run_loop(request, tools)).response

    async def run_loop(
        self,
        request: LlmRequest,
        tools: Iterable[AgentTool] = (),
    ) -> ToolLoopResult:
        """Run tool rounds up to the cap, then answer, saying which calls never ran.

        At the cap the model answers with no tools, from every result it got;
        each call it asked for in the round past the cap is answered NOT_RUN, so
        it knows that read did not happen.
        """
        tools = tuple(tools)
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
                return ToolLoopResult(response=response)
            if iteration >= max_iterations:
                _logger.warning(
                    "tool_loop_cap_reached",
                    correlation_id=request.correlation_id,
                    iteration=iteration,
                    tool_call_count=len(response.tool_calls),
                )
                unrun = response.tool_calls
                final = replace(
                    current_request,
                    tools=(),
                    tool_calls=(*previous_tool_calls, *unrun),
                    tool_results=(
                        *previous_tool_results,
                        *(LlmToolResult(tool_call_id=call.id, content=NOT_RUN) for call in unrun),
                    ),
                    system=_with_notice(current_request.system, self.cap_notice),
                )
                return ToolLoopResult(response=await self.llm_provider.complete(final), unrun=unrun)

            tool_results = await self._run_tool_calls(
                response.tool_calls, tool_registry, request.correlation_id
            )
            previous_tool_calls = (*previous_tool_calls, *response.tool_calls)
            previous_tool_results = (*previous_tool_results, *tool_results)
            current_request = replace(
                request,
                tools=current_request.tools,
                tool_calls=previous_tool_calls,
                tool_results=previous_tool_results,
            )

        return ToolLoopResult(response=response)

    async def _run_tool_calls(
        self,
        tool_calls: tuple[LlmToolCall, ...],
        tool_registry: Mapping[str, AgentTool],
        correlation_id: str,
    ) -> tuple[LlmToolResult, ...]:
        limit = max(0, self.max_tool_calls_per_turn)
        if len(tool_calls) > limit:
            _logger.warning(
                "tool_loop_call_limit_reached",
                correlation_id=correlation_id,
                tool_call_count=len(tool_calls),
                limit=limit,
            )
        results: list[LlmToolResult] = []
        for index, tool_call in enumerate(tool_calls):
            with _tracer.start_as_current_span("tool_loop.run_tool") as span:
                span.set_attribute("tool.name", tool_call.name)
                tool = tool_registry.get(tool_call.name)
                if index >= limit:
                    content = (
                        f"Tool {tool_call.name} was not run: at most {limit} tool calls a turn."
                    )
                elif tool is None:
                    content = f"Tool {tool_call.name} is not available."
                else:
                    content = _clip(await tool.run(tool_call.arguments), self.max_tool_result_chars)
                results.append(LlmToolResult(tool_call_id=tool_call.id, content=content))
        return tuple(results)


def _with_notice(system: str | None, notice: str | None) -> str | None:
    if not notice:
        return system
    return f"{system} {notice}" if system else notice


def _clip(content: str, max_chars: int) -> str:
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    return f"{content[:max_chars]}\n[cut: the tool returned more than {max_chars} characters]"


def _llm_tool(tool: AgentTool) -> LlmTool:
    return LlmTool(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
    )
