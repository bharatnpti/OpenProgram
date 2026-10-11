"""Investigate: Ask's deep mode.

A quick Ask is one tool loop of a few rounds that answers in 80 words. Some
questions need more than that -- why a pod slipped, what is really blocking a
project -- because the answer means following a blocker to another pod, reading
weeks of activity and the status reasons at several levels. An investigation
splits the question into steps, has a researcher look each step up with its own
context, and writes one answer from the researchers' notes.

How the steps are planned and run is an engine's business
(``core.ports.investigation``); this service gives the engine the asker's own
tools and the model, holds it to the time limit, makes what it reports
readable, and logs what it cost. Each step reads with exactly the tools a quick
Ask offers the same person (``AskService.tools_for``), so an investigation never
reads more than they could fetch themselves. It writes nothing and sends
nothing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import date
from time import perf_counter
from typing import Literal

import structlog

from core.application.ask_conversation import AskConversation, context_for
from core.application.ask_service import (
    ANSWER_FORMAT_RULES,
    ASK_TOOL_GUIDANCE,
    FOLLOW_UPS_FIELD,
    OWN_FIELDS_RULE,
    AnswerReader,
    AskResponseView,
    AskService,
    remembered,
    said_it_ran_out,
    without_unread_negatives,
)
from core.domain.auth import Principal
from core.domain.llm import LlmRequest, LlmResponse
from core.ports.investigation import (
    EngineAnswer,
    EngineEvent,
    EnginePlan,
    EngineStepDone,
    EngineStepFailed,
    InvestigationEngine,
    InvestigationRun,
)
from core.ports.llm import LlmProvider

_logger = structlog.get_logger(__name__)

# A question that asks what to do gets one. Asked why a project was at risk and
# what to do first, an answer said "Not known: No recommendations or next steps
# are stated in the notes" -- the notes are facts, and a first action is chosen
# from them, not found in them.
FIRST_ACTION_RULE = (
    "When the question asks what to do, or what to do first, add one line after the "
    "bullets starting 'First:' that names the one action the notes most support -- the "
    "missing blocking criterion, the oldest blocker, or the requirements with no ETA or due "
    "date, whichever holds the most up -- and who acts on it where a note says. It is "
    "chosen from the notes, so it is never 'Not known' while they hold a gap, a blocker or "
    "an undated requirement."
)

# The answer's shape. The quick answer's rules hold, except its first: an
# investigation's answer may give each driver its evidence and say what led to
# what, so it is allowed more bullets and more words.
INVESTIGATE_FORMAT_RULES: tuple[str, ...] = (
    "Shape: one verdict line that answers the question in one sentence, then one bullet "
    "line starting with '• ' for each cause or driver the notes support, 2 to 6 of them "
    "whenever the notes hold two or more, each with its evidence -- counts, dates, how many "
    "days -- and, where the notes say so, what it waits on and what it holds up (e.g. "
    "'• Finance sign-off: Refund edge cases (Noah Weber) blocked 19 days, waiting on the "
    "refund rounding rules'); then optionally one line starting 'Not known:' for what the "
    "notes could not establish. 160 words at most.",
    "Never predict a date, a slip or a miss that no note states: a colour is not a "
    "forecast, so when the question asks about a date the notes do not hold, the 'Not "
    "known:' line says so.",
    FIRST_ACTION_RULE,
    *ANSWER_FORMAT_RULES[1:],
)

# What makes a good step, whoever plans them.
STEP_GUIDANCE = (
    "Write each step as a plain question that names what it is about as the question "
    "names it -- a program, project, workstream, pod, person or issue -- and the period it "
    "covers; never name a tool. Each researcher looks names up itself, so no step only "
    "finds a node or an id. "
    "Between them the steps cover what the question needs of: the current status and the "
    "reasons for it; what lies behind each blocker or risk -- who or what it waits on, "
    "since when, and what it holds up; and how things changed over the period asked about. "
    "A question about a date -- will it ship, is it on track, is it late -- gets a step "
    "for the delivery forecast of each project or pod it names: the committed date, the "
    "50% and 85% finish dates and the verdict. A question about what blocks or holds "
    "something up, or about risk, gets a step of its own for the open blockers of each pod "
    "involved, or of every pod -- what is blocked, whose blocker it is, for how many days, "
    "and what it waits on. A question about release readiness -- is it ready, can it go "
    "to production, what is missing before the release -- gets a step for the release "
    "readiness of each project, release or pod it names: each criterion, which are missing "
    "or unsure, and their evidence. A question about what to do, or what to do first, or "
    "why something is at risk and what to do about it, covers every kind of thing that "
    "can hold the date up, a step each: the delivery forecast and the requirements with no "
    "ETA or due date; the release readiness; and the open blockers and risks of the pods "
    "involved -- so the answer can weigh them and name the first action. A "
    "question about every project covers each of them, its status, forecast and blockers. "
    "No two steps cover the same ground, and a question one look-up answers gets one step."
)

RESEARCH_SYSTEM_PROMPT = (
    "You research one step of a larger investigation over a delivery graph. "
    + ASK_TOOL_GUIDANCE
    + "Before you report any blocker, read pod_blockers where it is offered -- for the pod "
    "it is in, or every pod when none is named -- and give each blocker's owner, what it "
    "waits on and its days open, not how long its issue has gone without a change. "
    "Follow the trail within the step: when a status, blocker or risk points at another "
    "pod, person, issue or merge request, look that up too. "
    "Name people by display name, issues by key and merge requests by the ref the data "
    "gives, and call a merge request an MR, never a PR. "
    "When a tool result says the tool call limit is exceeded, or says 'Not run', you have "
    "run out of steps: write your notes from what you did read, put each read you could "
    "not make in gaps as 'Ran out of steps before checking …', and write no finding about "
    "what it would have shown -- never a negative such as no blockers or nothing missing. "
    "Once you have the facts, reply with a single JSON object and nothing else: findings, "
    "an array of at most 6 short factual lines, each with the counts, dates and days the "
    "tools gave; references, an array of the node ids the findings rest on, copied exactly "
    "as the tools return them; and gaps, an array of what you looked for and did not find."
)

# How the answer is written from the researchers' notes.
ANSWER_RULES = (
    "The notes are all you know: use no fact they do not hold, keep each fact with the "
    "people, issues, merge requests and numbers its note gives it -- never move one from "
    "an item to another -- keep each number counting what its note says it counts (days "
    "without a change are not days blocked), and where two notes disagree, say so. "
    "A step that failed, ran out of steps or lists gaps leaves its ground unchecked: say so "
    "in the 'Not known:' line, e.g. 'I ran out of steps before checking …', and never state "
    "a negative -- no blockers, no risks, nothing missing -- that no note establishes. "
    "Write the answer to these rules: "
    + " ".join(INVESTIGATE_FORMAT_RULES)
    + " Reply with a single JSON object and nothing else: answer, an array of strings with "
    "one line of the answer each -- the verdict first, then each bullet, then any 'First:' "
    "line, then any 'Not known:' line -- references, an array of the ids of the nodes the "
    "answer names, and only those, copied from the notes' references, and "
    + FOLLOW_UPS_FIELD
    + " "
    + OWN_FIELDS_RULE
)

# Said again right before the answer is written: a rule read just before the
# input holds better than one in the system prompt alone, and an answer once
# claimed a red project "will not make its date" with no forecast in any note.
ANSWER_REMINDER = (
    "Answer the question from these notes alone, in the required shape: the verdict, 2 to "
    "6 '• ' bullets with their evidence, then a 'First:' line if the question asks what to "
    "do, then any 'Not known:' line. Each person, issue and "
    "number stays with the item its note ties it to. Whether a date will be made or missed "
    "is said only when a note says it; a red or amber status alone does not. Questions to "
    "ask next go in follow_ups, never inside answer."
)

# Said once, when the planner would answer from one step, or from a step that
# came back thin (no findings, failed, or with gaps): an investigation of "why
# is it at risk, and what should we do first?" ran one step and answered
# without the forecast, the readiness gap or a first action.
PLAN_MORE_REMINDER = (
    "Before you answer: {reason}. Check what the question needs that the notes do not "
    "cover yet -- for a question about a date, risk, readiness or what to do: the delivery "
    "forecast and the requirements with no ETA or due date, the release readiness, and the "
    "open blockers and risks -- and send a step for each with the task tool. Answer now "
    "only if the notes already cover what the question needs."
)
ONE_STEP_REASON = "you have checked one step only"
THIN_STEP_REASON = "a step came back with little or nothing"

TIMED_OUT = (
    "The investigation took too long and was stopped. Ask again, or ask a narrower question."
)
ANSWER_FAILED = "The answer could not be written from what was found. Please ask again."
STEP_TIMED_OUT = "Ran out of time."
STEP_FAILED = "Could not be looked up."
STEP_OUT_OF_STEPS = "Ran out of steps."

_MAX_FINDINGS = 6
_MAX_FINDING_CHARS = 400
_MAX_GAPS = 4
_MAX_JSON_STARTS = 50
_JSON = json.JSONDecoder(strict=False)
_BULLET_EDGES = " \t-*•"

type StepStatus = Literal["running", "done", "failed"]


@dataclass(frozen=True, kw_only=True)
class InvestigationStep:
    """One step of an investigation, as the reader is shown it.

    ``findings`` are the step's own lines, made readable (no raw ids); a failed
    step has none and says why in ``error``.
    """

    index: int
    question: str
    status: StepStatus
    tools_used: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, kw_only=True)
class PlanEvent:
    """Every step so far. Sent again, with the new ones, when steps are added."""

    steps: tuple[InvestigationStep, ...]


@dataclass(frozen=True, kw_only=True)
class StepEvent:
    step: InvestigationStep


@dataclass(frozen=True, kw_only=True)
class AnswerEvent:
    view: AskResponseView
    steps: tuple[InvestigationStep, ...]


@dataclass(frozen=True, kw_only=True)
class FailedEvent:
    message: str
    steps: tuple[InvestigationStep, ...] = ()


type InvestigationEvent = PlanEvent | StepEvent | AnswerEvent | FailedEvent


@dataclass(frozen=True, kw_only=True)
class Notes:
    """What a step's researcher wrote down, before it is made readable."""

    findings: tuple[str, ...]
    references: tuple[str, ...]
    gaps: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class InvestigationLimits:
    max_steps: int = 3
    max_tool_iterations: int = 6
    timeout_seconds: float = 120.0


@dataclass
class _MeteredProvider:
    """Counts an investigation's completions and what they cost, for its log line."""

    inner: LlmProvider
    calls: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        response = await self.inner.complete(request)
        self.total_tokens += response.usage.total_tokens
        self.cost_usd += response.usage.cost_usd
        return response


class InvestigationService:
    def __init__(
        self,
        *,
        ask_service: AskService,
        llm_provider: LlmProvider,
        engine: InvestigationEngine,
        model: str,
        limits: InvestigationLimits | None = None,
    ) -> None:
        self._ask_service = ask_service
        self._llm_provider = llm_provider
        self._engine = engine
        self._model = model
        self._limits = limits or InvestigationLimits()

    async def investigate(
        self,
        *,
        principal: Principal,
        question: str,
        correlation_id: str,
        as_of: date | None = None,
        conversation: AskConversation | None = None,
    ) -> AsyncGenerator[InvestigationEvent]:
        """The engine's plan, each step as it finishes, then the answer.

        Or a failure, which ends the stream too. A consumer that stops reading
        closes the engine, which stops what it still runs.
        """
        asked_for = as_of or date.today()
        llm = _MeteredProvider(inner=self._llm_provider)
        reader = await self._ask_service.reader(principal.tenant_id, asked_for)
        context = await context_for(
            conversation,
            llm=llm,
            model=self._model,
            tenant_id=principal.tenant_id,
            correlation_id=correlation_id,
        )
        run = InvestigationRun(
            tenant_id=principal.tenant_id,
            question=question,
            correlation_id=correlation_id,
            as_of=asked_for,
            model=self._model,
            llm=llm,
            tools=self._ask_service.tools_for(principal, asked_for),
            max_steps=self._limits.max_steps,
            max_tool_iterations=self._limits.max_tool_iterations,
            history=context.messages(),
        )
        deadline = asyncio.get_running_loop().time() + self._limits.timeout_seconds
        started = perf_counter()
        steps: list[InvestigationStep] = []
        gaps: dict[int, tuple[str, ...]] = {}
        outcome = "failed"
        try:
            async with aclosing(self._engine.run(run)) as events:
                while True:
                    try:
                        async with asyncio.timeout_at(deadline):
                            event = await anext(events)
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        outcome = "timed_out"
                        yield FailedEvent(message=TIMED_OUT, steps=_cut_off(steps))
                        return
                    # Any failure: the stream has started, so it must still end
                    # with a reason.
                    except Exception as exc:
                        _logger.warning(
                            "ask_investigation_engine_failed",
                            correlation_id=correlation_id,
                            engine=self._engine.name,
                            error_type=type(exc).__name__,
                        )
                        yield FailedEvent(message=ANSWER_FAILED, steps=tuple(steps))
                        return
                    shown = _shown(event, steps, reader, gaps)
                    if shown is None:
                        continue
                    if isinstance(shown, AnswerEvent):
                        shown = replace(shown, view=remembered(shown.view, context))
                    if isinstance(shown, AnswerEvent):
                        outcome = "answered"
                    yield shown
                    if isinstance(shown, AnswerEvent):
                        return
            yield FailedEvent(message=ANSWER_FAILED, steps=tuple(steps))
        finally:
            _logger.info(
                "ask_investigation_finished",
                correlation_id=correlation_id,
                tenant_id=principal.tenant_id,
                engine=self._engine.name,
                outcome=outcome,
                steps=len(steps),
                failed_steps=sum(1 for step in steps if step.status == "failed"),
                llm_calls=llm.calls,
                total_tokens=llm.total_tokens,
                cost_usd=round(llm.cost_usd, 6),
                duration_ms=round((perf_counter() - started) * 1000),
            )


def _shown(
    event: EngineEvent,
    steps: list[InvestigationStep],
    reader: AnswerReader,
    gaps: dict[int, tuple[str, ...]],
) -> InvestigationEvent | None:
    """What the reader is shown of an engine's event; ``steps`` and ``gaps`` keep up with it.

    A step the plan never named is nothing to show. A step still running when
    the answer comes never ran: the engine ran out of steps for it.
    """
    if isinstance(event, EngineStepDone | EngineStepFailed) and not 0 <= event.index < len(steps):
        return None
    match event:
        case EnginePlan():
            for index, question in enumerate(event.steps):
                if index == len(steps):
                    steps.append(
                        InvestigationStep(index=index, question=question, status="running")
                    )
            return PlanEvent(steps=tuple(steps))
        case EngineStepDone():
            notes = step_notes(event.notes)
            readable = (reader.readable(finding) for finding in notes.findings)
            step = replace(
                steps[event.index],
                status="done",
                tools_used=tuple(dict.fromkeys(event.tools_used)),
                findings=tuple(line for line in readable if line),
            )
            steps[event.index] = step
            gaps[event.index] = notes.gaps
            return StepEvent(step=step)
        case EngineStepFailed():
            step = replace(
                steps[event.index],
                status="failed",
                tools_used=tuple(dict.fromkeys(event.tools_used)),
                error=STEP_OUT_OF_STEPS if event.out_of_steps else STEP_FAILED,
            )
            steps[event.index] = step
            return StepEvent(step=step)
        case EngineAnswer():
            steps[:] = [
                replace(step, status="failed", error=STEP_OUT_OF_STEPS)
                if step.status == "running"
                else step
                for step in steps
            ]
            tools_used = (tool for step in steps for tool in step.tools_used)
            view = reader.view(event.text, trace_id=event.trace_id, tools_used=tools_used)
            return AnswerEvent(view=held_to_the_notes(view, steps, gaps), steps=tuple(steps))


def held_to_the_notes(
    view: AskResponseView,
    steps: Sequence[InvestigationStep],
    gaps: Mapping[int, Sequence[str]],
) -> AskResponseView:
    """An answer whose steps were cut short, saying so and claiming no unread absence.

    A step was cut short when it failed, never ran, or its researcher ran out
    of steps (a gap says so). Then a claim of no blockers, no risks or nothing
    missing stays only where a finished step read that kind
    (``without_unread_negatives``), and a 'Not known:' line names what went
    unchecked unless the answer already says it could not check everything.
    """
    out_of_steps = [
        step.question
        for step in steps
        if step.error == STEP_OUT_OF_STEPS
        or (step.status == "done" and any(said_it_ran_out(gap) for gap in gaps.get(step.index, ())))
    ]
    failed = [
        step.question
        for step in steps
        if step.status == "failed" and step.error != STEP_OUT_OF_STEPS
    ]
    if not out_of_steps and not failed:
        return view
    read = {tool for step in steps if step.status == "done" for tool in step.tools_used}
    answer = without_unread_negatives(view.answer, read=read)
    if not said_it_ran_out(answer):
        said = []
        if out_of_steps:
            said.append(f"I ran out of steps before checking {_quoted(out_of_steps)}")
        if failed:
            said.append(f"{_quoted(failed)} could not be looked up")
        answer = "\n".join(line for line in (answer, f"Not known: {'; '.join(said)}.") if line)
    return replace(view, answer=answer)


def _quoted(questions: Sequence[str]) -> str:
    quoted = [f"\u201c{question.strip()}\u201d" for question in dict.fromkeys(questions)]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _cut_off(steps: Sequence[InvestigationStep]) -> tuple[InvestigationStep, ...]:
    """The steps when time ran out: those still running did not finish."""
    return tuple(
        replace(step, status="failed", error=STEP_TIMED_OUT) if step.status == "running" else step
        for step in steps
    )


def step_notes(text: str) -> Notes:
    """A researcher's reply as notes; a reply that is not the object reads as its lines."""
    found = _object_with(text, ("findings", "references", "gaps"))
    if found is None:
        lines = (line.strip(_BULLET_EDGES) for line in text.splitlines())
        return Notes(
            findings=_strings(line for line in lines if line)[:_MAX_FINDINGS],
            references=(),
            gaps=(),
        )
    return Notes(
        findings=_strings(_items(found.get("findings")))[:_MAX_FINDINGS],
        references=_strings(_items(found.get("references"))),
        gaps=_strings(_items(found.get("gaps")))[:_MAX_GAPS],
    )


def _object_with(text: str, keys: Sequence[str]) -> Mapping[str, object] | None:
    """The first JSON object in ``text`` carrying any of ``keys``, casefolded."""
    start = text.find("{")
    for _ in range(_MAX_JSON_STARTS):
        if start == -1:
            return None
        try:
            decoded, _end = _JSON.raw_decode(text, start)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(decoded, dict):
                folded = {str(key).casefold(): value for key, value in decoded.items()}
                if any(key in folded for key in keys):
                    return folded
        start = text.find("{", start + 1)
    return None


def _items(value: object) -> Iterable[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return (value,)
    return ()


def _strings(items: Iterable[object]) -> tuple[str, ...]:
    texts = (
        item.strip(_BULLET_EDGES)[:_MAX_FINDING_CHARS]
        for item in items
        if isinstance(item, str) and item.strip(_BULLET_EDGES)
    )
    return tuple(dict.fromkeys(texts))
