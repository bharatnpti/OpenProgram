from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

import structlog

from core.application.agents.tool_loop import ToolCallingAgent
from core.application.checkin_drift import eta_duration_days
from core.application.conversation_history import llm_messages_from_turns
from core.application.json_parsing import extract_json_object
from core.application.outbound_text import carries_link_or_markup, reveals_internals
from core.application.reply_without_status import reads_without_status
from core.domain.blockers import BlockerReport, DeveloperBlocker, normalize_blocker_key
from core.domain.conversation import ConversationTurn
from core.domain.cross_person import CrossPersonRequestKind, is_placeholder_name, issue_keys_in
from core.domain.llm import LlmRequest, LlmResponse
from core.domain.status import CheckInSignals, CrossPersonMention, IssueClaim
from core.ports.llm import LlmProvider
from core.ports.tools import AgentTool

# Appended to a structured-output call's instructions on a single retry when the
# first response is not valid JSON, before any safe fallback.
JSON_RETRY_REMINDER = (
    "\n\nYour previous response was not valid JSON. Respond again with only a single "
    "valid JSON object matching the requested schema and no other text."
)
# Safe fallback question used when the model output cannot be parsed at all, so a
# garbled reply routes to a clarification instead of silently finalizing as healthy.
GENERIC_CLARIFICATION_QUESTION = (
    "Thanks. Could you share your progress, any blockers, and your ETA so I can "
    "record today's status?"
)
# A drafted follow-up longer than one direct message, or one carrying a link,
# chat markup or anything about OpenProgram's insides, is not sent: the generic
# question goes instead (guardrails.md).
MAX_CLARIFICATION_QUESTION_CHARS = 320

# A request's note is sent to the named person as the ask, so it must be the ask
# and not the reply: the person it names has not seen the reply and is not meant to.
_REQUEST_NOTE_RULE = (
    "A requests note is a short phrase of at most 100 characters saying what is needed "
    "from that person, in your own words; never copy or quote the reply into it."
)
# A request DMs the person it names, so the name has to be a person's.
_REQUEST_NAME_RULE = (
    "A requests name is a person's name. A role or placeholder such as reviewer, someone, "
    "anyone, the team, QA, a dev or my lead names nobody: add no request for it."
)
# The progress note is stored as the person's summary, which is read without the
# reply, so it keeps the issues the reply names ("About half done." for IDP-9).
_PROGRESS_NOTE_RULE = (
    "progress_note is a short summary of the reply that names every issue key the reply "
    "names, each with what the reply says about it (for example 'IDP-9 is about half "
    "done'); never leave out an issue key the reply gives."
)
# eta_change_days is an ETA that moved, never how long the work takes from now (N45).
_ETA_CHANGE_RULE = (
    "eta_change_days is only for an ETA the reply says moved: the days it moved by, positive "
    "when later ('pushed by 2 days' is 2, 'slipped a day' is 1, 'moved from Tuesday to "
    "Thursday' is 2). A time from now such as '2-3 days to finish', 'in 3 days' or 'a couple "
    "of days' is an ETA, not a change: leave eta_change_days null, set eta_answered true and "
    "keep the words in the issue_updates note of the issue it is for."
)
# An ETA that moved, in the person's words: "pushed by 2 days", "slipped a day",
# "delayed by 2 days", "moved from Tuesday to Thursday", "2 days behind".
_ETA_MOVED = re.compile(
    r"\b(?:slip\w*|delay\w*|postpon\w*|resched\w*|defer\w*|overrun\w*|behind|"
    r"push(?:ed|es|ing)?\s+(?:it\s+)?(?:back|out|by)|"
    r"mov(?:ed|es|ing)\s+(?:it\s+)?(?:from|out|back|by)|"
    r"(?:later|longer|more)\s+than|extra\s+days?|instead\s+of|rather\s+than|"
    r"(?:eta|deadline|target|due date)\s+(?:is\s+|has\s+|was\s+)?(?:now\s+)?"
    r"(?:moved|changed|shifted|pushed|slipped))\b"
)

PARSE_REPLY_SYSTEM_PROMPT = (
    "Extract structured status signals from the current reply. Use prior conversation turns only "
    "as context. Do not invent blockers; use an empty blocker list when no blocker is stated. "
    "Track whether the reply explicitly answered blocker and ETA questions. "
    "Use available Jira and Git tools to verify issue-specific claims when needed, but keep tools "
    "read-only. "
    "Return only valid JSON."
)
CLARIFICATION_EVALUATOR_SYSTEM_PROMPT = (
    "Decide whether a status check-in reply has enough concrete progress, blocker, and ETA "
    "information to finalize the check-in. Use prior conversation turns as context. "
    "Classify whether the reply is a status update. Do not invent blockers. "
    "Track whether the reply explicitly answered blocker and ETA questions. "
    "Use available Jira and Git tools to verify issue-specific claims. If the reply contradicts "
    "the source of truth, for example says an issue is done while Jira is not done, set sufficient "
    "false and ask one targeted clarification. "
    "If more information is needed, draft one concise follow-up question. The question names "
    "the issue key it is about (for example 'What is your ETA for CHK-4?'), never only 'it' or "
    "'this work'. Return only valid JSON."
)
# Added to the clarification instructions when this person's claims on their own issues
# are written to the tracker without asking (write-back on, consent auto_apply).
_TRACKER_WRITE_BACK_NOTE = (
    "\n\nOpenProgram updates the issue tracker itself, after this check-in, for issues "
    "assigned to this person. When the reply says one of their own issues changed state "
    "(for example it is merged or done) and the tracker does not show it yet, that is not "
    "a contradiction: do not ask about it, and never ask them to update the tracker "
    "themselves."
)
_logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class ClarificationDecision:
    sufficient: bool
    question: str | None = None
    signals: CheckInSignals | None = None
    is_status_update: bool = True


class StatusParser:
    def __init__(
        self,
        llm_provider: LlmProvider,
        model: str,
        tool_agent: ToolCallingAgent | None = None,
    ) -> None:
        self._llm_provider = llm_provider
        self._model = model
        self._tool_agent = tool_agent

    async def parse_reply(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        raw_reply: str,
        correlation_id: str,
        conversation_turns: Iterable[ConversationTurn] = (),
        tools: Iterable[AgentTool] = (),
        prior_blockers: Sequence[DeveloperBlocker] = (),
    ) -> CheckInSignals:
        prior_text, handles = _prior_blocker_prompt(prior_blockers)
        request = LlmRequest(
            tenant_id=tenant_id,
            prompt=_parser_prompt(raw_reply, prior_text=prior_text),
            model=self._model,
            correlation_id=correlation_id,
            system=PARSE_REPLY_SYSTEM_PROMPT,
            instructions=_parser_instructions(),
            messages=llm_messages_from_turns(conversation_turns),
            json_mode=True,
            metadata={
                "service": "status_parser",
                "purpose": "parse_checkin_signals",
                "developer_id": developer_id,
            },
        )
        parsed, response = await _complete_json_with_retry(self._complete, request, tuple(tools))
        if parsed is None:
            _logger.warning(
                "status_parser_json_decode_failed",
                tenant_id=tenant_id,
                developer_id=developer_id,
                correlation_id=correlation_id,
                purpose="parse_checkin_signals",
                trace_id=response.trace_id,
            )
            # Keep the raw progress note but mark it unverified: with no answered
            # blocker/ETA signals and parser_confident False, it cannot roll up green.
            return CheckInSignals(progress_note=raw_reply, parser_confident=False)
        return _signals_from_json(
            parsed, fallback_progress_note=raw_reply, prior_blocker_handles=handles
        )

    async def _complete(
        self,
        request: LlmRequest,
        tools: Iterable[AgentTool],
    ) -> LlmResponse:
        if self._tool_agent is not None:
            return await self._tool_agent.run(request, tools)
        return await self._llm_provider.complete(request)


class ClarificationEvaluator:
    def __init__(
        self,
        llm_provider: LlmProvider,
        model: str,
        tool_agent: ToolCallingAgent | None = None,
    ) -> None:
        self._llm_provider = llm_provider
        self._model = model
        self._tool_agent = tool_agent

    async def evaluate(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        raw_reply: str,
        correlation_id: str,
        conversation_turns: Iterable[ConversationTurn] = (),
        tools: Iterable[AgentTool] = (),
        prior_blockers: Sequence[DeveloperBlocker] = (),
        tracker_write_back: bool = False,
        checkin_text: str | None = None,
        has_own_work: bool | None = None,
    ) -> ClarificationDecision:
        """Read whether ``raw_reply`` gives a status, and whether it is enough to finalize.

        Rules first, the model only when they cannot tell: a reply
        whose words carry no status ("thanks, nothing from me today") is never
        the model's to call a status. The rules read ``checkin_text``, all of the
        person's messages on this check-in with this one last, when given, so a
        short answer to a follow-up after a status is still the model's.
        ``has_own_work`` is whether the tracker lists open work for the person,
        None when unknown (see reply_without_status.reads_without_status).
        """
        if _is_trivial_non_status_reply(raw_reply) or reads_without_status(
            raw_reply if checkin_text is None else checkin_text, has_own_work=has_own_work
        ):
            return ClarificationDecision(sufficient=False, is_status_update=False)

        prior_text, handles = _prior_blocker_prompt(prior_blockers)
        request = LlmRequest(
            tenant_id=tenant_id,
            prompt=_clarification_prompt(raw_reply, prior_text=prior_text),
            model=self._model,
            correlation_id=correlation_id,
            system=CLARIFICATION_EVALUATOR_SYSTEM_PROMPT,
            instructions=_clarification_instructions(tracker_write_back=tracker_write_back),
            messages=llm_messages_from_turns(conversation_turns),
            json_mode=True,
            metadata={
                "service": "status_parser",
                "purpose": "evaluate_checkin_clarification",
                "developer_id": developer_id,
            },
        )
        parsed, response = await _complete_json_with_retry(self._complete, request, tuple(tools))
        if parsed is None:
            _logger.warning(
                "clarification_evaluator_json_decode_failed",
                tenant_id=tenant_id,
                developer_id=developer_id,
                correlation_id=correlation_id,
                purpose="evaluate_checkin_clarification",
                trace_id=response.trace_id,
            )
            # Safety-critical: never default an unparseable reply to sufficient, or a
            # garbled/blocked check-in would finalize as healthy. Ask for a status.
            return ClarificationDecision(
                sufficient=False,
                question=GENERIC_CLARIFICATION_QUESTION,
            )
        return _clarification_decision_from_json(
            parsed, fallback_progress_note=raw_reply, prior_blocker_handles=handles
        )

    async def _complete(
        self,
        request: LlmRequest,
        tools: Iterable[AgentTool],
    ) -> LlmResponse:
        if self._tool_agent is not None:
            return await self._tool_agent.run(request, tools)
        return await self._llm_provider.complete(request)


async def _complete_json_with_retry(
    complete: Callable[[LlmRequest, tuple[AgentTool, ...]], Awaitable[LlmResponse]],
    request: LlmRequest,
    tools: tuple[AgentTool, ...],
) -> tuple[dict[str, object] | None, LlmResponse]:
    """Run the LLM, extract a JSON object, and retry once before giving up.

    Returns ``(parsed_or_none, last_response)``. The single retry appends a terse
    "return only valid JSON" reminder; a persistent failure returns ``None`` so the
    caller applies its safe fallback.
    """
    response = await complete(request, tools)
    parsed = extract_json_object(response.text)
    if parsed is not None:
        return parsed, response
    # The reminder is an instruction, so it joins the instructions, never the input.
    retry_request = replace(
        request, instructions=(request.instructions or "") + JSON_RETRY_REMINDER
    )
    retry_response = await complete(retry_request, tools)
    return extract_json_object(retry_response.text), retry_response


def _parser_instructions() -> str:
    """The parse task's instructions: a system message of their own, right before the reply."""
    return (
        "Extract structured check-in signals from the reply: the text after 'Reply:' in the "
        "last message. Earlier messages are context only; judge every field by what that "
        "reply itself says. "
        "Return only a JSON object with keys: progress_note string, "
        "blockers array of strings, blocker_details array, resolved_blocker_ids array of "
        "strings, eta_change_days integer or null, blockers_answered boolean, "
        "eta_answered boolean, requests array, and issue_updates array. "
        "Each blocker_details item uses keys: description string, issue_key string or null, "
        "and pod string or null; include one item per blocker stated in the reply, copying an "
        "issue key or pod/team name only when the reply states it. Keep the blockers array "
        "equal to the blocker_details descriptions. "
        f"{_PROGRESS_NOTE_RULE} "
        "Each requests item uses keys: name string, kind dependency/review/input, "
        "note string, email string or null. "
        f"{_REQUEST_NOTE_RULE} "
        "Each issue_updates item uses keys: issue_key string, claimed_done boolean, "
        "claimed_state string or null, and note string. "
        "Do not invent blockers; use empty blockers and blocker_details arrays when no "
        "blocker is stated. "
        "Set blockers_answered true only when the reply explicitly says there are no blockers "
        "or names one or more blockers. Set eta_answered true only when the reply explicitly "
        "gives an ETA, ETA change, or says there is no ETA change. "
        f"{_ETA_CHANGE_RULE} "
        "Only include a request when the reply explicitly needs a deliverable, review, "
        "or input from a specific named person. Use an empty requests array otherwise. "
        f"{_REQUEST_NAME_RULE} "
        "Only include an issue_updates item when the reply explicitly names an issue key or "
        "unambiguously refers to an active issue in context. "
        "Previously open blockers are context only. When the reply says a previously open "
        "blocker is resolved, add its bracketed id to resolved_blocker_ids; never mark a "
        "blocker resolved otherwise. When the reply gives a pod or work item for a previously "
        "open blocker, add a blocker_details item repeating that blocker's description with "
        "the stated issue_key or pod. When the reply restates a previously open blocker in "
        "different words, reuse that blocker's description verbatim rather than writing a new "
        "one, so the same obstacle is not tracked twice and keeps its original age."
    )


def _parser_prompt(raw_reply: str, *, prior_text: str = "") -> str:
    """The parse call's input: data only, no instruction (core/domain/prompt_safety.py)."""
    return _input_text(prior_text, "Reply", raw_reply)


def _clarification_instructions(*, tracker_write_back: bool = False) -> str:
    """The evaluation task's instructions: a system message of their own, right before the reply."""
    return (
        "Evaluate the latest check-in reply: the text after 'Latest reply:' in the last "
        "message. Earlier messages are context only; judge every field by what the latest "
        "reply itself says. Return only a JSON object with keys: "
        "is_status_update boolean, sufficient boolean, question string or null, and "
        "signals object or null. Set is_status_update false for acknowledgements, thanks, "
        "reactions, or questions that do not provide status progress, blockers, or ETA. "
        "The signals object uses keys: progress_note string, blockers array of strings, "
        "blocker_details array, resolved_blocker_ids array of strings, "
        "eta_change_days integer or null, blockers_answered boolean, eta_answered boolean, "
        "requests array, and issue_updates array. Each blocker_details item uses keys: "
        "description string, issue_key string or null, and pod string or null. "
        "Each requests item uses keys: name string, "
        "kind dependency/review/input, note string, email string or null. Each issue_updates "
        "item uses keys: issue_key string, claimed_done boolean, claimed_state string or null, "
        "and note string. "
        f"{_PROGRESS_NOTE_RULE} "
        "Do not invent blockers. Previously open blockers are context only. When the reply "
        "says a previously open blocker is resolved, add its bracketed id to "
        "resolved_blocker_ids; never mark a blocker resolved otherwise. When the reply gives "
        "a pod or work item for a previously open blocker, add a blocker_details item "
        "repeating that blocker's description with the stated issue_key or pod. When the "
        "reply restates a previously open blocker in different words, reuse that blocker's "
        "description verbatim rather than writing a new one, so the same obstacle is not "
        "tracked twice and keeps its original age. "
        "If the latest reply answers a question about which pod or work item a previously "
        "open blocker belongs to, treat it as a status update, set sufficient true, and "
        "record the attribution in blocker_details. "
        "Only include a request when the reply "
        "explicitly needs a deliverable, review, or input from a specific named person. "
        f"{_REQUEST_NAME_RULE} "
        f"{_REQUEST_NOTE_RULE} "
        "Use Jira/Git tools when available to cross-check issue and progress claims. If a reply "
        "says an issue is done but Jira is not done, or claims substantial progress while recent "
        "Git activity for that issue is absent or contradictory, set sufficient false and ask "
        "one targeted clarification about that contradiction. "
        "Set blockers_answered true only when the reply explicitly says there are no blockers "
        "or names one or more blockers. Set eta_answered true only when the reply explicitly "
        "gives an ETA, ETA change, or says there is no ETA change. "
        f"{_ETA_CHANGE_RULE} "
        "When sufficient is false, question must ask only for the missing status detail."
    ) + (_TRACKER_WRITE_BACK_NOTE if tracker_write_back else "")


def _clarification_prompt(raw_reply: str, *, prior_text: str = "") -> str:
    """The evaluation call's input: data only, no instruction."""
    return _input_text(prior_text, "Latest reply", raw_reply)


def _input_text(prior_text: str, label: str, raw_reply: str) -> str:
    prior = prior_text.strip()
    reply = f"{label}:\n{raw_reply}"
    return f"{prior}\n\n{reply}" if prior else reply


def _clarification_decision_from_json(
    value: object,
    *,
    fallback_progress_note: str,
    prior_blocker_handles: Mapping[str, str] = {},
) -> ClarificationDecision:
    if not isinstance(value, Mapping):
        # Unparseable/invalid shape must not finalize as healthy; ask for a status.
        return ClarificationDecision(sufficient=False, question=GENERIC_CLARIFICATION_QUESTION)

    is_status_update = value.get("is_status_update")
    if isinstance(is_status_update, bool) and not is_status_update:
        return ClarificationDecision(sufficient=False, is_status_update=False)

    sufficient = value.get("sufficient")
    if not isinstance(sufficient, bool):
        # Missing/invalid sufficiency flag defaults to a clarification, never green.
        return ClarificationDecision(
            sufficient=False,
            question=GENERIC_CLARIFICATION_QUESTION,
            signals=_signals_from_json(
                value,
                fallback_progress_note=fallback_progress_note,
                prior_blocker_handles=prior_blocker_handles,
            ),
        )

    raw_signals = value.get("signals")
    signals = (
        _signals_from_json(
            raw_signals,
            fallback_progress_note=fallback_progress_note,
            prior_blocker_handles=prior_blocker_handles,
        )
        if isinstance(raw_signals, Mapping)
        else None
    )
    question = value.get("question")
    clean_question = _sendable_question(question)
    if sufficient:
        return ClarificationDecision(
            sufficient=True,
            signals=signals or CheckInSignals(progress_note=fallback_progress_note),
        )
    return ClarificationDecision(sufficient=False, question=clean_question, signals=signals)


def _sendable_question(question: object) -> str | None:
    """The model's follow-up as it may be sent, the generic one, or None when none was drafted."""
    if not isinstance(question, str) or not question.strip():
        return None
    flat = " ".join(question.split())
    if (
        len(flat) > MAX_CLARIFICATION_QUESTION_CHARS
        or carries_link_or_markup(flat)
        or reveals_internals(
            flat, prompts=(CLARIFICATION_EVALUATOR_SYSTEM_PROMPT, _clarification_instructions())
        )
    ):
        return GENERIC_CLARIFICATION_QUESTION
    return flat


def _prior_blocker_prompt(
    prior_blockers: Sequence[DeveloperBlocker],
) -> tuple[str, dict[str, str]]:
    """Render prior open blockers with bracketed handles, returning the handle map.

    Handles (``B1``, ``B2``, …) are positional per call; the returned map lets
    the deserializer translate model-reported handles back to real blocker ids
    so the collector never sees handles.
    """
    if not prior_blockers:
        return "", {}
    lines: list[str] = []
    handles: dict[str, str] = {}
    for index, blocker in enumerate(prior_blockers, start=1):
        handle = f"B{index}"
        handles[handle] = blocker.blocker_id
        suffix = f" ({blocker.work_item_id})" if blocker.work_item_id else ""
        lines.append(f"- [{handle}] {blocker.description}{suffix}")
    text = "\n\nPreviously open blockers, each with its bracketed id:\n" + "\n".join(lines)
    return text, handles


def _is_trivial_non_status_reply(raw_reply: str) -> bool:
    normalized = " ".join(raw_reply.casefold().strip().split())
    return normalized in {
        "k",
        "kk",
        "ok",
        "okay",
        "sure",
        "thanks",
        "thank you",
        "ty",
    }


def _signals_from_json(
    value: object,
    *,
    fallback_progress_note: str,
    prior_blocker_handles: Mapping[str, str] = {},
) -> CheckInSignals:
    if not isinstance(value, Mapping):
        return CheckInSignals(progress_note=fallback_progress_note)

    progress_note = value.get("progress_note")
    legacy_blockers = _string_tuple(value.get("blockers"))
    details = _blocker_report_tuple(value.get("blocker_details"))
    blocker_reports = _merge_blocker_reports(details, legacy_blockers)
    blockers = tuple(report.description for report in blocker_reports)
    resolved_blocker_ids = _resolved_blocker_ids(
        value.get("resolved_blocker_ids"), prior_blocker_handles
    )
    issue_updates = _issue_claim_tuple(value.get("issue_updates"))
    eta_change_days = _optional_int(value.get("eta_change_days"))
    eta_answered = eta_change_days is not None or _optional_bool(value.get("eta_answered"))
    # Every caller passes the person's raw reply as the fallback note.
    if eta_change_days is not None and _is_duration_from_now(
        eta_change_days, (fallback_progress_note, *_claim_texts(issue_updates))
    ):
        # The duration is the ETA, which stays answered. Its window is read
        # from the claim's words when the check-in records the issue's ETA.
        eta_change_days = None
    return CheckInSignals(
        progress_note=_note_naming_reply_issues(progress_note.strip(), fallback_progress_note)
        if isinstance(progress_note, str) and progress_note.strip()
        else fallback_progress_note,
        blockers=blockers,
        eta_change_days=eta_change_days,
        blockers_answered=bool(blockers)
        or bool(resolved_blocker_ids)
        or _optional_bool(value.get("blockers_answered")),
        eta_answered=eta_answered,
        requests=_request_tuple(value.get("requests")),
        issue_updates=issue_updates,
        blocker_reports=blocker_reports,
        resolved_blocker_ids=resolved_blocker_ids,
    )


def _note_naming_reply_issues(note: str, reply: str) -> str:
    """``note`` naming every issue key ``reply`` names.

    In a live run Hana wrote "IDP-9 (Enterprise SSO onboarding guide) is in
    progress, about half done. ...", and the model's progress note, stored as
    her summary, read "About half done.": her card no longer said which task.
    The note's words are kept, and the keys the reply names that it leaves out
    are added by plain text rules: a note naming no issue leads with them
    ("IDP-9: About half done."), and one that names some ends with the rest
    ("IDP-3 is in review. Also about IDP-9.").
    """
    named = issue_keys_in(note.upper())
    missing = sorted(issue_keys_in(reply) - named, key=reply.find)
    if not missing:
        return note
    keys = ", ".join(missing)
    if not named:
        return f"{keys}: {note}"
    end = "" if note.endswith((".", "!", "?")) else "."
    return f"{note}{end} Also about {keys}."


def _is_duration_from_now(eta_change_days: int, texts: Iterable[str]) -> bool:
    """Whether the model's ETA change is a duration from now the reply gave (N45).

    R5 live: asked for CHK-14's ETA, Sofia said "2-3 days to have the test plan
    reviewed and finalized." The model gave eta_change_days 2, and the Signals
    feed read "eta change +2d", as if the ETA had slipped two days. An ETA
    change is an ETA that moved ("pushed by 2 days", "slipped a day", "moved
    from Tuesday to Thursday"). So when no reply or claim words say it moved,
    and a duration from now in them holds the number, the number is that
    duration. Read deterministically; a real change is kept as the model gave it.
    """
    words = tuple(text.lower() for text in texts if text)
    if eta_change_days <= 0 or any(_ETA_MOVED.search(text) for text in words):
        return False
    for text in words:
        days = eta_duration_days(text)
        if days is not None and days[0] <= eta_change_days <= days[1]:
            return True
    return False


def _claim_texts(claims: Iterable[IssueClaim]) -> tuple[str, ...]:
    return tuple(
        part for claim in claims for part in (claim.note, claim.claimed_state or "") if part
    )


def _blocker_report_tuple(value: object) -> tuple[BlockerReport, ...]:
    if not isinstance(value, list | tuple):
        return ()
    reports: list[BlockerReport] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        description = _clean_string(item.get("description"))
        if description is None:
            continue
        reports.append(
            BlockerReport(
                description=description,
                issue_key=_clean_string(item.get("issue_key") or item.get("key")),
                pod_id=_clean_string(item.get("pod") or item.get("pod_id")),
            )
        )
    return tuple(reports)


def _merge_blocker_reports(
    details: tuple[BlockerReport, ...], legacy: tuple[str, ...]
) -> tuple[BlockerReport, ...]:
    """Details win; legacy strings not covered by a detail append as plain reports."""
    covered = {normalize_blocker_key(report.description) for report in details}
    extras = tuple(
        BlockerReport(description=item)
        for item in legacy
        if normalize_blocker_key(item) not in covered
    )
    return details + extras


def _resolved_blocker_ids(value: object, handles: Mapping[str, str]) -> tuple[str, ...]:
    """Translate bracketed handles (or raw ids) to real blocker ids, deduped."""
    known_ids = set(handles.values())
    resolved: list[str] = []
    for token in _string_tuple(value):
        stripped = token.strip("[]")
        blocker_id = handles.get(stripped) or (stripped if stripped in known_ids else None)
        if blocker_id is not None and blocker_id not in resolved:
            resolved.append(blocker_id)
    return tuple(resolved)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _optional_bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _request_tuple(value: object) -> tuple[CrossPersonMention, ...]:
    if not isinstance(value, list | tuple):
        return ()
    requests: list[CrossPersonMention] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        raw_name = _clean_string(item.get("name") or item.get("raw_name"))
        kind = _clean_kind(item.get("kind"))
        note = _clean_string(item.get("note"))
        if raw_name is None or kind is None or note is None:
            continue
        email = _clean_string(item.get("email"))
        if email is None and is_placeholder_name(raw_name):
            # "Waiting on reviewer" names nobody. The blocker it describes is
            # kept; a request, a directory lookup and a "could not find
            # reviewer" question are not.
            continue
        requests.append(
            CrossPersonMention(
                raw_name=raw_name,
                kind=kind.value,
                note=note,
                email=email,
            )
        )
    return tuple(requests)


def _issue_claim_tuple(value: object) -> tuple[IssueClaim, ...]:
    if not isinstance(value, list | tuple):
        return ()
    claims: list[IssueClaim] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        issue_key = _clean_string(item.get("issue_key") or item.get("key"))
        if issue_key is None:
            continue
        claimed_state = _clean_string(item.get("claimed_state"))
        note = _clean_string(item.get("note")) or ""
        claims.append(
            IssueClaim(
                issue_key=issue_key,
                claimed_done=_optional_bool(item.get("claimed_done")),
                claimed_state=claimed_state,
                note=note,
            )
        )
    return tuple(claims)


def _clean_kind(value: object) -> CrossPersonRequestKind | None:
    if not isinstance(value, str):
        return None
    try:
        return CrossPersonRequestKind(value.strip().lower())
    except ValueError:
        return None


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
