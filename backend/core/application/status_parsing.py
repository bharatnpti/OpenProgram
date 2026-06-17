from __future__ import annotations

import json
from collections.abc import Mapping

from core.domain.llm import LlmRequest
from core.domain.status import CheckInSignals, Mood
from core.ports.llm import LlmProvider


class StatusParser:
    def __init__(self, llm_provider: LlmProvider, model: str) -> None:
        self._llm_provider = llm_provider
        self._model = model

    async def parse_reply(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        raw_reply: str,
        correlation_id: str,
    ) -> CheckInSignals:
        response = await self._llm_provider.complete(
            LlmRequest(
                tenant_id=tenant_id,
                prompt=_parser_prompt(raw_reply),
                model=self._model,
                correlation_id=correlation_id,
                metadata={
                    "service": "status_parser",
                    "purpose": "parse_checkin_signals",
                    "developer_id": developer_id,
                    "redact_input": True,
                    "redact_output": True,
                },
            )
        )
        try:
            parsed = json.loads(response.text)
        except json.JSONDecodeError:
            return CheckInSignals(progress_note=raw_reply)
        return _signals_from_json(parsed, fallback_progress_note=raw_reply)


def _parser_prompt(raw_reply: str) -> str:
    return (
        "Extract structured check-in signals from the reply below. "
        "Return only a JSON object with keys: progress_note string, "
        "blockers array of strings, eta_change_days integer or null, "
        "mood one of positive, neutral, negative, or null.\n\n"
        f"Reply:\n{raw_reply}"
    )


def _signals_from_json(value: object, *, fallback_progress_note: str) -> CheckInSignals:
    if not isinstance(value, Mapping):
        return CheckInSignals(progress_note=fallback_progress_note)

    progress_note = value.get("progress_note")
    return CheckInSignals(
        progress_note=progress_note.strip()
        if isinstance(progress_note, str) and progress_note.strip()
        else fallback_progress_note,
        blockers=_string_tuple(value.get("blockers")),
        eta_change_days=_optional_int(value.get("eta_change_days")),
        mood=_optional_mood(value.get("mood")),
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _optional_mood(value: object) -> Mood | None:
    if not isinstance(value, str):
        return None
    try:
        return Mood(value.lower())
    except ValueError:
        return None
