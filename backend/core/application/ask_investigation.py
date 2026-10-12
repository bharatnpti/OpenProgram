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
import re
from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field, replace
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
from core.domain.graph import JsonScalar
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
from core.ports.tools import AgentTool

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
    "50% and 85% finish dates and the verdict. A question about why a date moved -- what "
    "changed, was added, delayed or impacted a project's, release's or pod's timeline, or "
    "'show me what moved X' -- gets one step per project, release or pod it names for "
    "what moved its forecast over the period asked about (since planning unless it names "
    "another): each driver in working days and the requirements behind it, and the "
    "committed date's moves; and, when slower finishing leads, a step for the open "
    "blockers of the pods involved. A question about what blocks or holds "
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

# Said after the reminder when the steps' tools returned facts the answer must
# quote as they are (AnswerFacts): Investigate once said one requirement had
# no ETA, from a step that read release readiness, while the forecast another
# step had read counted six.
FACTS_REMINDER = (
    "The tools returned these facts, whatever the notes say: wherever the answer speaks of "
    "them, give them as they are, with their counts and keys."
)

# Said once, when the planner would answer from one step, or from a step that
# came back thin (no findings, failed, or with gaps): an investigation of "why
# is it at risk, and what should we do first?" ran one step and answered
# without the forecast, the readiness gap or a first action.
PLAN_MORE_REMINDER = (
    "Before you answer: {reason}. Check what the question needs that the notes do not "
    "cover yet -- for a question about a date, risk, readiness or what to do: the delivery "
    "forecast and the requirements with no ETA or due date, the release readiness, and the "
    "open blockers and risks; for a question about why a date moved: what moved it -- "
    "and send a step for each with the task tool. Answer now "
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
        # What the steps' tools return that the answer must say as returned.
        facts = AnswerFacts(as_of=asked_for)
        run = InvestigationRun(
            tenant_id=principal.tenant_id,
            question=question,
            correlation_id=correlation_id,
            as_of=asked_for,
            model=self._model,
            llm=llm,
            tools=tuple(
                facts.recording(tool) for tool in self._ask_service.tools_for(principal, asked_for)
            ),
            max_steps=self._limits.max_steps,
            max_tool_iterations=self._limits.max_tool_iterations,
            history=context.messages(),
            answer_facts=facts.lines,
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
                    shown = _shown(event, steps, reader, gaps, facts)
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
    facts: AnswerFacts,
) -> InvestigationEvent | None:
    """What the reader is shown of an engine's event; ``steps`` and ``gaps`` keep up with it.

    A step the plan never named is nothing to show. A step still running when
    the answer comes never ran: the engine ran out of steps for it. The answer
    is held to the notes (no unread absence) and to the facts the tools gave.
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
            view = held_to_the_facts(held_to_the_notes(view, steps, gaps), facts)
            return AnswerEvent(view=view, steps=tuple(steps))


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


# ---- facts the answer quotes as the tools returned them ---------------------------------

_FORECAST_TOOL = "delivery_forecast"
# The keys an answer names; the count always says how many there are.
_SHOWN_KEYS = 10


@dataclass(frozen=True, kw_only=True)
class UndatedRequirements:
    """A project's open requirements with no ETA or due date, as delivery_forecast read them."""

    scope_name: str
    count: int
    open: int
    keys: tuple[str, ...]

    def clause(self, *, of_scope: bool = True, finite: bool = True) -> str:
        """'6 of 13 open requirements of Checkout Revamp have no ETA or due date (CHK-1, \u2026)'.

        ``finite`` gives the clause its own verb; otherwise it reads as a noun
        phrase ('6 of 13 open requirements with no ETA or due date'), for an
        answer that used one ('First: date the 6 requirements with no ETA').
        """
        scope = f" of {self.scope_name}" if of_scope else ""
        if self.count == 0:
            return f"every open requirement{scope} has an ETA or due date"
        verb = ("has no" if self.count == 1 else "have no") if finite else "with no"
        shown = list(self.keys[:_SHOWN_KEYS])
        if shown and self.count > len(shown):
            shown.append(f"{self.count - len(shown)} more")
        listed = (
            ""
            if not shown
            else f" ({shown[0]})"
            if len(shown) == 1
            else f" ({', '.join(shown[:-1])} and {shown[-1]})"
        )
        return (
            f"{self.count} of {self.open} open requirements{scope} {verb} ETA or due date{listed}"
        )

    def sentence(self) -> str:
        clause = self.clause()
        return f"{clause[:1].upper()}{clause[1:]}."


@dataclass
class AnswerFacts:
    """What the steps' tools returned that the answer must say as returned.

    Recorded as the tools run (``recording``), whichever step ran them and
    whatever its notes kept: a researcher once noted that only one requirement
    had no ETA, reading release readiness, while the forecast a step before had
    read counted six. Only reads of the day asked about count, and only a
    project's own count: a pod's part of a project is not the project's.
    """

    as_of: date
    undated: dict[str, UndatedRequirements] = field(default_factory=dict)

    def recording(self, tool: AgentTool) -> AgentTool:
        return _FactRecorder(inner=tool, facts=self) if tool.name == _FORECAST_TOOL else tool

    def record(self, result: str) -> None:
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return
        if (
            not isinstance(payload, dict)
            or "error" in payload
            or "pod_id" in payload
            or payload.get("as_of") != self.as_of.isoformat()
        ):
            return
        for project in _items(payload.get("projects")):
            fact = _undated_of(project)
            if fact is not None:
                self.undated[fact.scope_name.casefold()] = fact

    def lines(self) -> tuple[str, ...]:
        """The facts as sentences, for the answer to quote (InvestigationRun.answer_facts)."""
        return tuple(fact.sentence() for fact in self.undated.values() if fact.open > 0)


@dataclass(frozen=True, kw_only=True)
class _FactRecorder:
    """An Ask tool that hands what it returns to AnswerFacts as well as to the model."""

    inner: AgentTool
    facts: AnswerFacts

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
        result = await self.inner.run(arguments)
        self.facts.record(result)
        return result


def _undated_of(project: object) -> UndatedRequirements | None:
    if not isinstance(project, dict):
        return None
    name, count, open_ = (
        project.get("project_name"),
        project.get("open_without_date"),
        project.get("open_requirements"),
    )
    if not isinstance(name, str) or not isinstance(count, int) or not isinstance(open_, int):
        return None
    keys = _items(project.get("open_without_date_keys"))
    return UndatedRequirements(
        scope_name=name,
        count=count,
        open=open_,
        keys=tuple(key for key in keys if isinstance(key, str)),
    )


def answer_reminder(facts: Sequence[str]) -> str:
    """ANSWER_REMINDER, with the facts the tools returned when there are any."""
    if not facts:
        return ANSWER_REMINDER
    return " ".join((ANSWER_REMINDER, FACTS_REMINDER, *facts))


_COUNT_WORDS: Mapping[str, int] = {
    "no": 0,
    "none of the": 0,
    "zero": 0,
    "a single": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_COUNT = r"\d+|none\s+of\s+the|a\s+single|" + "|".join(
    word for word in _COUNT_WORDS if " " not in word
)
_THINGS = r"requirements?|issues?|stor(?:y|ies)|tickets?|items?"
_DATES = r"(?:ETAs?|due\s+dates?)(?:\s+(?:or|and|nor|/)\s+(?:an?\s+)?(?:ETAs?|due\s+dates?))?"
# A count of requirements with no ETA or due date: '6 open requirements have
# no ETA or due date', 'only one requirement lacks an ETA', 'the 3
# requirements with no due date', '2 undated requirements'. What sits between
# the things and the dates holds no digit, so '6 are in testing and 2 issues
# have no ETA' is read as the 2.
_UNDATED_CLAIMS = (
    re.compile(
        rf"\b(?:(?:only|just)\s+)?(?P<count>{_COUNT})\s+(?:of\s+(?:the\s+)?\d+\s+)?"
        rf"(?:[\w-]+\s+){{0,2}}?(?:{_THINGS})\b[^.;:\n\d]{{0,60}}?"
        rf"(?:\b(?P<finite>ha(?:s|ve)\s+no|lacks?(?:\s+an?)?)"
        rf"|\b(?:with(?:out)?(?:\s+(?:an?|no))?|lacking(?:\s+an?)?|missing(?:\s+an?)?))"
        rf"\s+{_DATES}(?P<note>\s*\([^()]*\))?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:(?:only|just)\s+)?(?P<count>{_COUNT})\s+(?:open\s+)?undated\s+"
        rf"(?:open\s+)?(?:{_THINGS})\b(?P<note>\s*\([^()]*\))?",
        re.IGNORECASE,
    ),
)
_ABOUT_KEYS = re.compile(r"\bkeys?\b|\b[A-Z][A-Z0-9]+-\d+\b")
_FIRST = "First:"


def held_to_the_facts(view: AskResponseView, facts: AnswerFacts) -> AskResponseView:
    """An answer whose count of undated requirements is the forecast's, with their keys.

    The same hold as ``held_to_the_notes`` keeps on unread absences: whatever
    the model wrote, a count of open requirements with no ETA or due date that
    is not the one delivery_forecast returned, or that leaves their keys out,
    is rewritten from that read. A what-to-do answer (one with a 'First:'
    line) that leaves the undated requirements out gets them as a bullet
    before that line, since its first action is weighed against them
    (FIRST_ACTION_RULE). An answer is left as written when no forecast was
    read, and a count it cannot tie to one project is left alone.
    """
    if not facts.undated:
        return view
    said = False
    lines: list[str] = []
    for line in view.answer.split("\n"):
        held, found = _undated_held(line, view.answer, facts)
        said = said or found
        lines.append(held)
    if not said:
        lines = _with_undated_bullet(lines, view.answer, facts)
    answer = "\n".join(lines)
    return view if answer == view.answer else replace(view, answer=answer)


def _undated_held(line: str, answer: str, facts: AnswerFacts) -> tuple[str, bool]:
    """The line with each undated count it states made the forecast's, and whether it states one."""
    found = False
    for pattern in _UNDATED_CLAIMS:
        held: list[str] = []
        end = 0
        for match in pattern.finditer(line):
            fact = _fact_for(line, answer, facts)
            if fact is None:
                continue
            found = True
            held.append(line[end : match.start()])
            held.append(_claim_held(match, line, fact))
            end = match.end()
        line = "".join(held) + line[end:]
    return line, found


def _claim_held(match: re.Match[str], line: str, fact: UndatedRequirements) -> str:
    claim = match.group(0)
    count = _count(match.group("count"))
    shown = fact.keys[:_SHOWN_KEYS]
    if count == fact.count and all(key in line for key in shown):
        return claim
    note = match.group("note") or ""
    if note and _ABOUT_KEYS.search(note):
        # '(issue keys not provided)': the keys are given now.
        claim = claim[: len(claim) - len(note)]
        note = ""
    rest = line[: match.start()] + line[match.end() :]
    # 'have no ETA' keeps a verb; 'with no ETA' and '2 undated requirements' are
    # things a sentence names ('First: date the 2 undated requirements').
    clause = fact.clause(
        of_scope=fact.scope_name.casefold() not in rest.casefold(),
        finite=bool(match.groupdict().get("finite")),
    )
    if claim[:1].isupper():
        clause = clause[:1].upper() + clause[1:]
    return clause + note


def _count(text: str) -> int | None:
    words = " ".join(text.casefold().split())
    return int(words) if words.isdigit() else _COUNT_WORDS.get(words)


def _fact_for(line: str, answer: str, facts: AnswerFacts) -> UndatedRequirements | None:
    """The project a count is about: named in its line, else the one the answer names."""
    known = list(facts.undated.values())
    for text in (line, answer):
        named = [fact for fact in known if fact.scope_name.casefold() in text.casefold()]
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            return None
    counted = [fact for fact in known if fact.open > 0]
    return counted[0] if len(counted) == 1 else None


def _with_undated_bullet(lines: list[str], answer: str, facts: AnswerFacts) -> list[str]:
    first = next((i for i, line in enumerate(lines) if line.strip().startswith(_FIRST)), None)
    if first is None:
        return lines
    fact = _fact_for("", answer, facts)
    if fact is None or fact.count == 0:
        return lines
    return [*lines[:first], f"\u2022 {fact.sentence()}", *lines[first:]]


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
