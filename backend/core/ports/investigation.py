from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from core.domain.llm import LlmMessage
from core.ports.llm import LlmProvider
from core.ports.tools import AgentTool


@dataclass(frozen=True, kw_only=True)
class InvestigationRun:
    """What an engine is given for one investigation.

    ``tools`` are the asker's own Ask tools, exactly as a quick answer offers
    them; an engine must read with these and nothing else. ``llm`` is the
    model to call, through OpenProgram's own provider.
    """

    tenant_id: str
    question: str
    correlation_id: str
    as_of: date
    model: str
    llm: LlmProvider
    tools: tuple[AgentTool, ...]
    max_steps: int
    max_tool_iterations: int
    # The conversation before the question, oldest first: what it refers to.
    history: tuple[LlmMessage, ...] = ()


@dataclass(frozen=True, kw_only=True)
class EnginePlan:
    """Every step so far, in order: sent again whenever the engine adds steps."""

    steps: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class EngineStepDone:
    index: int
    # The researcher's reply as it wrote it, notes and raw ids and all.
    notes: str
    tools_used: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class EngineStepFailed:
    index: int
    tools_used: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class EngineAnswer:
    # The answer as the model wrote it, before it is made readable.
    text: str
    trace_id: str


type EngineEvent = EnginePlan | EngineStepDone | EngineStepFailed | EngineAnswer


class InvestigationEngine(Protocol):
    """Runs one investigation, saying what it does as it does it.

    It sends an ``EnginePlan`` before any step of it finishes, each step's end
    by its index in the plan, then one ``EngineAnswer``. Closing the stream
    stops whatever is still running.
    """

    @property
    def name(self) -> str: ...

    def run(self, run: InvestigationRun) -> AsyncGenerator[EngineEvent]: ...
