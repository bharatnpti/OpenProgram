"""Investigate on deepagents: a main agent that plans and delegates, researchers that look up.

The main agent has no data tools. It splits the question into steps, hands
each to a ``researcher`` sub-agent through deepagents' ``task`` tool, and writes
the answer from the notes they return; it may send a few more steps once the
first notes are in. Each researcher gets the asker's own Ask tools and its own
context.

What the harness may do is fixed here, not left to the model:

- The models are OpenProgram's own provider (``OpenProgramChatModel``); no
  LangChain provider package is called. deepagents imports its Anthropic
  integration when it loads, so that package is installed, unused.
- No shell: ``execute`` is excluded for this provider's models, and the
  default general-purpose sub-agent is off, so ``researcher`` is the only one.
  deepagents' file tools stay, on its default in-memory state backend: files
  live in the run's state and nowhere else.
- LangChain middleware bounds the run: how many steps the main agent may send
  and how many model and tool calls each agent may make. The service holds the
  whole run to its time limit.
- LangSmith never traces a run, whatever the environment says.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfileConfig,
    SubAgent,
    create_deep_agent,
    register_harness_profile,
)
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
    ToolCallRequest,
    ToolErrorMiddleware,
)
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from langsmith.run_helpers import tracing_context

from core.application.ask_investigation import (
    ANSWER_REMINDER,
    ANSWER_RULES,
    RESEARCH_SYSTEM_PROMPT,
    STEP_GUIDANCE,
)
from core.application.ask_service import RecordedTool, date_context
from core.ports.investigation import (
    EngineAnswer,
    EngineEvent,
    EnginePlan,
    EngineStepDone,
    EngineStepFailed,
    InvestigationRun,
)
from core.ports.tools import AgentTool
from infra.adapters.llm.langchain_bridge import PROVIDER, OpenProgramChatModel

RESEARCHER: Final = "researcher"
TASK_TOOL: Final = "task"
# The main agent may send this many steps beyond its first ones, once their
# notes raise a question the answer needs.
FOLLOW_UP_STEPS: Final = 2
# The main agent's turns: its plan, perhaps a to-do list, a follow-up round, the
# answer, and room for one more. Past it the run fails rather than answering.
MAIN_MODEL_CALLS: Final = 6
# A backstop under the time limit: the main graph's own steps.
RECURSION_LIMIT: Final = 60
_LIMIT_REACHED = "Model call limits exceeded"

MAIN_SYSTEM_PROMPT = (
    "You investigate a program-management question over a delivery graph for the person "
    "asking it. You have no data tools of your own: a researcher looks each step up, so "
    f"delegate every step with the {TASK_TOOL} tool, subagent_type '{RESEARCHER}', one step "
    "per call, its description being the step's question. Split the question into the "
    "fewest steps that together answer it more deeply than one look-up would. "
    + STEP_GUIDANCE
    + " Send your first steps, at most {max_steps}, together in one turn. When their notes "
    "raise a question the answer needs, you may send up to {follow_ups} more. Each result "
    "is that researcher's notes: its findings, the node ids they rest on, and its gaps. "
    "Earlier turns of the conversation, when given, say what the question refers to; "
    "write every step so that a researcher who never saw them can answer it. "
    "Then write the answer. " + ANSWER_RULES
)

RESEARCHER_DESCRIPTION = (
    "Looks one step of the investigation up in the delivery graph and returns its notes: "
    "findings, the node ids they rest on, and gaps. Give it one plain question."
)


def _register_profile() -> None:
    """No shell and no general-purpose sub-agent, for every model of this provider."""
    register_harness_profile(
        PROVIDER,
        HarnessProfileConfig(
            excluded_tools=frozenset({"execute"}),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )


_register_profile()


class DeepAgentInvestigationEngine:
    @property
    def name(self) -> str:
        return "deepagents"

    async def run(self, run: InvestigationRun) -> AsyncGenerator[EngineEvent]:
        agent = build_agent(run)
        tracker = _Tracker(data_tools=frozenset(tool.name for tool in run.tools))
        inputs = {
            "messages": [
                *(
                    HumanMessage(content=m.content)
                    if m.role == "user"
                    else AIMessage(content=m.content)
                    for m in run.history
                ),
                HumanMessage(content=date_context(run.as_of) + f"Question: {run.question}"),
            ]
        }
        queue: asyncio.Queue[EngineEvent | Exception | None] = asyncio.Queue()

        async def pump() -> None:
            try:
                async for event in agent.astream_events(
                    inputs,
                    config={"recursion_limit": RECURSION_LIMIT},
                    version="v2",
                    include_types=["chat_model", "tool"],
                ):
                    for found in tracker.feed(event):
                        await queue.put(found)
                for found in tracker.finish():
                    await queue.put(found)
                await queue.put(None)
            except Exception as exc:
                await queue.put(exc)

        # The run happens in a context where LangSmith tracing is off, so no
        # environment setting can send a question or its data to it.
        task = asyncio.create_task(pump(), context=_untraced())
        try:
            while (item := await queue.get()) is not None:
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def build_agent(run: InvestigationRun) -> CompiledStateGraph[Any, Any, Any, Any]:
    """The main agent and its researcher, for this run's asker, model and limits."""

    def model(purpose: str) -> OpenProgramChatModel:
        return OpenProgramChatModel(
            provider=run.llm,
            model_name=run.model,
            tenant_id=run.tenant_id,
            correlation_id=run.correlation_id,
            metadata_fields={
                "agent": "ask_investigation",
                "engine": "deepagents",
                "purpose": purpose,
                "as_of": run.as_of.isoformat(),
            },
        )

    researcher_limits: list[AgentMiddleware[Any, Any, Any]] = [
        # Past its tool budget a researcher is told so, and writes its notes.
        ToolCallLimitMiddleware(run_limit=2 * run.max_tool_iterations, exit_behavior="continue"),
        ModelCallLimitMiddleware(run_limit=run.max_tool_iterations + 2, exit_behavior="end"),
    ]
    researcher: SubAgent = {
        "name": RESEARCHER,
        "description": RESEARCHER_DESCRIPTION,
        "system_prompt": RESEARCH_SYSTEM_PROMPT + " " + date_context(run.as_of),
        "tools": [langchain_tool(tool) for tool in run.tools],
        "model": model("investigation_step"),
        "middleware": researcher_limits,
    }
    main_limits: list[AgentMiddleware[Any, Any, Any]] = [
        ToolCallLimitMiddleware(
            tool_name=TASK_TOOL,
            run_limit=run.max_steps + FOLLOW_UP_STEPS,
            exit_behavior="continue",
        ),
        ModelCallLimitMiddleware(run_limit=MAIN_MODEL_CALLS, exit_behavior="error"),
        # A step that fails goes back to the main agent as a failed step.
        ToolErrorMiddleware(on_error=_step_error, tools=[TASK_TOOL]),
        AnswerReminderMiddleware(),
    ]
    return create_deep_agent(
        model=model("investigation_main"),
        tools=[],
        system_prompt=MAIN_SYSTEM_PROMPT.format(
            max_steps=run.max_steps, follow_ups=FOLLOW_UP_STEPS
        ),
        subagents=[researcher],
        middleware=main_limits,
    )


class AnswerReminderMiddleware(AgentMiddleware[Any, Any, Any]):
    """Says the answer rules again right after the researchers' notes.

    The rules sit at the top of a long system prompt, and answers written from
    the notes claimed a red project "will not make its date". Read just before
    the answer is written, the same rules held in the other engine. The
    reminder goes into that one model call only, never into the run's state.
    """

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        if _after_notes(request.messages):
            request = request.override(
                messages=[*request.messages, HumanMessage(content=ANSWER_REMINDER)]
            )
        return await handler(request)


def _after_notes(messages: Sequence[BaseMessage]) -> bool:
    """The researchers' notes are the last thing said: the main agent's next turn answers.

    True when the last assistant turn delegated steps and only their results
    have come since.
    """
    asked = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], AIMessage)
        ),
        None,
    )
    if asked is None or asked == len(messages) - 1:
        return False
    turn = messages[asked]
    delegated = isinstance(turn, AIMessage) and any(
        call["name"] == TASK_TOOL for call in turn.tool_calls
    )
    return delegated and all(isinstance(m, ToolMessage) for m in messages[asked + 1 :])


def langchain_tool(tool: AgentTool) -> BaseTool:
    """An Ask tool for LangChain: same name, words and schema; errors come back as text."""
    recorded = RecordedTool(inner=tool, calls=[])

    async def call(**arguments: Any) -> str:  # noqa: ANN401 -- the tool's own JSON arguments
        return await recorded.run(arguments)

    return StructuredTool.from_function(
        coroutine=call,
        name=tool.name,
        description=tool.description,
        args_schema=dict(tool.parameters),
        infer_schema=False,
    )


@dataclass
class _Tracker:
    """Turns the agent's event stream into the plan, each step's end, and the answer.

    A step is one ``task`` call of the main agent. The main agent's own model
    calls are those with no ``task`` run among their parents; a data tool run
    under a ``task`` run belongs to that step.

    A ``task`` run's start event carries no tool-call id, and its input is not
    always the call's arguments, so a run is matched to the step its
    description names, else to the first planned step not started yet: the
    tool node starts a turn's calls in the order the model wrote them.
    """

    data_tools: frozenset[str]
    questions: list[str] = field(default_factory=list)
    _waiting: list[int] = field(default_factory=list)
    _by_run: dict[str, int] = field(default_factory=dict)
    _tools: dict[int, list[str]] = field(default_factory=dict)
    _ended: set[int] = field(default_factory=set)
    _answer: AIMessage | None = None

    def feed(self, event: Mapping[str, Any]) -> list[EngineEvent]:
        kind = event.get("event")
        name = event.get("name")
        run_id = str(event.get("run_id"))
        step = self._step_of(event.get("parent_ids") or ())
        if kind == "on_chat_model_end" and step is None:
            return self._main_turn(event.get("data", {}).get("output"))
        if kind == "on_tool_start" and name == TASK_TOOL and step is None:
            return self._task_started(run_id, event.get("data", {}).get("input"))
        if kind == "on_tool_start" and step is not None and name in self.data_tools:
            self._tools.setdefault(step, []).append(str(name))
            return []
        if kind == "on_tool_end" and run_id in self._by_run:
            return self._task_ended(self._by_run[run_id], event.get("data", {}).get("output"))
        if kind == "on_tool_error" and run_id in self._by_run:
            return self._failed(self._by_run[run_id])
        return []

    def finish(self) -> list[EngineEvent]:
        """The answer, if the main agent's last turn gave one."""
        if self._answer is None:
            return []
        text = _text(self._answer)
        if text.startswith(_LIMIT_REACHED):
            return []
        trace_id = str(self._answer.response_metadata.get("trace_id") or "")
        return [EngineAnswer(text=text, trace_id=trace_id)]

    def _step_of(self, parents: Sequence[str]) -> int | None:
        return next((self._by_run[parent] for parent in parents if parent in self._by_run), None)

    def _main_turn(self, output: object) -> list[EngineEvent]:
        if not isinstance(output, AIMessage):
            return []
        tasks = [call for call in output.tool_calls if call["name"] == TASK_TOOL]
        if not output.tool_calls:
            self._answer = output
        if not tasks:
            return []
        self._waiting.extend(self._plan(_description(call["args"])) for call in tasks)
        return [EnginePlan(steps=tuple(self.questions))]

    def _task_started(self, run_id: str, given: object) -> list[EngineEvent]:
        named = _description(given) if isinstance(given, Mapping) else ""
        index = next((i for i in self._waiting if self.questions[i] == named), None)
        if index is None and self._waiting:
            index = self._waiting[0]
        if index is not None:
            self._waiting.remove(index)
            self._by_run[run_id] = index
            return []
        # A call the main agent's turn did not show: a step of its own.
        self._by_run[run_id] = self._plan(named)
        return [EnginePlan(steps=tuple(self.questions))]

    def _task_ended(self, index: int, output: object) -> list[EngineEvent]:
        if index in self._ended:
            return []
        message = _tool_message(output)
        text = _text(message) if message is not None else str(output or "")
        if (message is not None and message.status == "error") or text.startswith(_LIMIT_REACHED):
            return self._failed(index)
        self._ended.add(index)
        return [EngineStepDone(index=index, notes=text, tools_used=self._used(index))]

    def _failed(self, index: int) -> list[EngineEvent]:
        if index in self._ended:
            return []
        self._ended.add(index)
        return [EngineStepFailed(index=index, tools_used=self._used(index))]

    def _plan(self, question: str) -> int:
        self.questions.append(question)
        return len(self.questions) - 1

    def _used(self, index: int) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self._tools.get(index, ())))


def _untraced() -> contextvars.Context:
    context = contextvars.copy_context()
    # Entered and never left: the context is the run's alone and goes with it.
    context.run(tracing_context(enabled=False).__enter__)
    return context


def _step_error(exc: Exception, request: ToolCallRequest) -> str:
    return f"This step could not be looked up ({type(exc).__name__})."


def _description(args: Mapping[str, Any]) -> str:
    return str(args.get("description") or "").strip()


def _tool_message(output: object) -> ToolMessage | None:
    """The task's result: a ToolMessage itself, or one inside the Command it returned."""
    if isinstance(output, ToolMessage):
        return output
    if isinstance(output, Command) and isinstance(output.update, Mapping):
        messages = output.update.get("messages") or ()
        return next((m for m in reversed(messages) if isinstance(m, ToolMessage)), None)
    return None


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        part if isinstance(part, str) else str(part.get("text") or "")
        for part in content
        if isinstance(part, str) or part.get("type") == "text"
    )
