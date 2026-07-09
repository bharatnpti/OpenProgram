from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import structlog

from core.application.agents.tool_loop import ToolCallingAgent
from core.application.conversation_history import llm_messages_from_turns
from core.domain.conversation import ConversationTurn
from core.domain.cross_person import CrossPersonRequestKind
from core.domain.llm import LlmRequest, LlmResponse
from core.domain.status import CheckInSignals, CrossPersonMention
from core.ports.llm import LlmProvider
from core.ports.tools import AgentTool

PARSE_REPLY_SYSTEM_PROMPT = (
    "Extract structured status signals from the current reply. Use prior conversation turns only "
    "as context. Do not invent blockers; use an empty blocker list when no blocker is stated. "
    "Track whether the reply explicitly answered blocker and ETA questions. "
    "Return only valid JSON."
)
CLARIFICATION_EVALUATOR_SYSTEM_PROMPT = (
    "Decide whether a status check-in reply has enough concrete progress, blocker, and ETA "
    "information to finalize the check-in. Use prior conversation turns as context. "
    "Classify whether the reply is a status update. Do not invent blockers. "
    "Track whether the reply explicitly answered blocker and ETA questions. "
    "If more information is needed, draft one concise follow-up question. Return only valid JSON."
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
        prior_blockers: Iterable[str] = (),
    ) -> CheckInSignals:
        request = LlmRequest(
            tenant_id=tenant_id,
            prompt=_parser_prompt(raw_reply, prior_blockers=prior_blockers),
            model=self._model,
            correlation_id=correlation_id,
            system=PARSE_REPLY_SYSTEM_PROMPT,
            messages=llm_messages_from_turns(conversation_turns),
            metadata={
                "service": "status_parser",
                "purpose": "parse_checkin_signals",
                "developer_id": developer_id,
            },
        )
        response = await self._complete(request, tools)
        try:
            parsed = json.loads(response.text)
        except json.JSONDecodeError as exc:
            _logger.warning(
                "status_parser_json_decode_failed",
                tenant_id=tenant_id,
                developer_id=developer_id,
                correlation_id=correlation_id,
                purpose="parse_checkin_signals",
                trace_id=response.trace_id,
                error=str(exc),
            )
            return CheckInSignals(progress_note=raw_reply)
        return _signals_from_json(parsed, fallback_progress_note=raw_reply)

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
        prior_blockers: Iterable[str] = (),
    ) -> ClarificationDecision:
        if _is_trivial_non_status_reply(raw_reply):
            return ClarificationDecision(sufficient=False, is_status_update=False)

        request = LlmRequest(
            tenant_id=tenant_id,
            prompt=_clarification_prompt(raw_reply, prior_blockers=prior_blockers),
            model=self._model,
            correlation_id=correlation_id,
            system=CLARIFICATION_EVALUATOR_SYSTEM_PROMPT,
            messages=llm_messages_from_turns(conversation_turns),
            metadata={
                "service": "status_parser",
                "purpose": "evaluate_checkin_clarification",
                "developer_id": developer_id,
            },
        )
        response = (
            await self._tool_agent.run(request, tools)
            if self._tool_agent is not None
            else await self._llm_provider.complete(request)
        )
        try:
            parsed = json.loads(response.text)
        except json.JSONDecodeError as exc:
            _logger.warning(
                "clarification_evaluator_json_decode_failed",
                tenant_id=tenant_id,
                developer_id=developer_id,
                correlation_id=correlation_id,
                purpose="evaluate_checkin_clarification",
                trace_id=response.trace_id,
                error=str(exc),
            )
            return ClarificationDecision(sufficient=True)
        return _clarification_decision_from_json(parsed, fallback_progress_note=raw_reply)


def _parser_prompt(raw_reply: str, *, prior_blockers: Iterable[str] = ()) -> str:
    return (
        "Extract structured check-in signals from the reply below. "
        "Return only a JSON object with keys: progress_note string, "
        "blockers array of strings, eta_change_days integer or null, blockers_answered boolean, "
        "eta_answered boolean, and requests array. "
        "Each requests item uses keys: name string, kind dependency/review/input, "
        "note string, email string or null. "
        "Do not invent blockers; use an empty blockers array when no blocker is stated. "
        "Set blockers_answered true only when the reply explicitly says there are no blockers "
        "or names one or more blockers. Set eta_answered true only when the reply explicitly "
        "gives an ETA, ETA change, or says there is no ETA change. "
        "Only include a request when the reply explicitly needs a deliverable, review, "
        "or input from a specific named person. Use an empty requests array otherwise. "
        "Previously open blockers are context only; mark them resolved only if the reply says "
        f"they are resolved.{_prior_blocker_prompt(prior_blockers)}\n\n"
        f"Reply:\n{raw_reply}"
    )


def _clarification_prompt(raw_reply: str, *, prior_blockers: Iterable[str] = ()) -> str:
    return (
        "Evaluate the latest check-in reply below. Return only a JSON object with keys: "
        "is_status_update boolean, sufficient boolean, question string or null, and "
        "signals object or null. Set is_status_update false for acknowledgements, thanks, "
        "reactions, or questions that do not provide status progress, blockers, or ETA. "
        "The signals object uses keys: progress_note string, blockers array of strings, "
        "eta_change_days integer or null, blockers_answered boolean, eta_answered boolean, "
        "and requests array. Each requests item uses keys: name string, "
        "kind dependency/review/input, note string, email string or null. "
        "Do not invent blockers. Previously open blockers are context only; mark them resolved "
        "only if the reply says they are resolved. Only include a request when the reply "
        "explicitly needs a deliverable, review, or input from a specific named person. "
        "Set blockers_answered true only when the reply explicitly says there are no blockers "
        "or names one or more blockers. Set eta_answered true only when the reply explicitly "
        "gives an ETA, ETA change, or says there is no ETA change. "
        "When sufficient is false, question must ask only for the missing status detail."
        f"{_prior_blocker_prompt(prior_blockers)}\n\n"
        f"Latest reply:\n{raw_reply}"
    )


def _clarification_decision_from_json(
    value: object,
    *,
    fallback_progress_note: str,
) -> ClarificationDecision:
    if not isinstance(value, Mapping):
        return ClarificationDecision(sufficient=True)

    is_status_update = value.get("is_status_update")
    if isinstance(is_status_update, bool) and not is_status_update:
        return ClarificationDecision(sufficient=False, is_status_update=False)

    sufficient = value.get("sufficient")
    if not isinstance(sufficient, bool):
        return ClarificationDecision(
            sufficient=True,
            signals=_signals_from_json(value, fallback_progress_note=fallback_progress_note),
        )

    raw_signals = value.get("signals")
    signals = (
        _signals_from_json(raw_signals, fallback_progress_note=fallback_progress_note)
        if isinstance(raw_signals, Mapping)
        else None
    )
    question = value.get("question")
    clean_question = question.strip() if isinstance(question, str) and question.strip() else None
    if sufficient:
        return ClarificationDecision(
            sufficient=True,
            signals=signals or CheckInSignals(progress_note=fallback_progress_note),
        )
    return ClarificationDecision(sufficient=False, question=clean_question, signals=signals)


def _prior_blocker_prompt(prior_blockers: Iterable[str]) -> str:
    blockers = tuple(blocker.strip() for blocker in prior_blockers if blocker.strip())
    if not blockers:
        return ""
    return "\n\nPreviously open blockers:\n" + "\n".join(f"- {blocker}" for blocker in blockers)


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


def _signals_from_json(value: object, *, fallback_progress_note: str) -> CheckInSignals:
    if not isinstance(value, Mapping):
        return CheckInSignals(progress_note=fallback_progress_note)

    progress_note = value.get("progress_note")
    blockers = _string_tuple(value.get("blockers"))
    eta_change_days = _optional_int(value.get("eta_change_days"))
    return CheckInSignals(
        progress_note=progress_note.strip()
        if isinstance(progress_note, str) and progress_note.strip()
        else fallback_progress_note,
        blockers=blockers,
        eta_change_days=eta_change_days,
        blockers_answered=bool(blockers) or _optional_bool(value.get("blockers_answered")),
        eta_answered=eta_change_days is not None or _optional_bool(value.get("eta_answered")),
        requests=_request_tuple(value.get("requests")),
    )


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
        requests.append(
            CrossPersonMention(
                raw_name=raw_name,
                kind=kind.value,
                note=note,
                email=_clean_string(item.get("email")),
            )
        )
    return tuple(requests)


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
