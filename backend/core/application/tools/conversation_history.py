from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from core.domain.conversation import ConversationTurn
from core.domain.graph import JsonScalar
from core.ports.repositories import ConversationRepository

DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 50


def _parameters_schema() -> Mapping[str, object]:
    return {
        "type": "object",
        "properties": {
            "since_days": {
                "type": "integer",
                "minimum": 1,
                "description": "Number of days of retained history to inspect.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_HISTORY_LIMIT,
                "description": "Maximum number of chronological turns to return.",
            },
            "on": {
                "type": "string",
                "format": "date",
                "description": "Optional ISO date to fetch one conversation day.",
            },
        },
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class ConversationHistoryTool:
    tenant_id: str
    developer_id: str
    repository: ConversationRepository
    retention_days: int
    reference_at: datetime | None = None

    name: str = "fetch_conversation_history"
    description: str = (
        "Fetch older chronological conversation turns for this tenant/developer. "
        "Use this when the recent context is insufficient."
    )
    parameters: Mapping[str, object] = field(default_factory=_parameters_schema)

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        on = _optional_date(arguments.get("on"))
        limit = _bounded_positive_int(arguments.get("limit"), default=DEFAULT_HISTORY_LIMIT)
        if on is not None:
            turns = await self.repository.list_turns_for_day(
                self.tenant_id,
                self.developer_id,
                on,
            )
            return _format_turns(turns[-limit:])

        since_days = _bounded_positive_int(
            arguments.get("since_days"),
            default=self.retention_days,
            upper_bound=self.retention_days,
        )
        reference_at = self.reference_at or datetime.now(tz=UTC)
        turns = await self.repository.list_recent_turns(
            self.tenant_id,
            self.developer_id,
            limit=limit,
            since=reference_at - timedelta(days=since_days),
        )
        return _format_turns(turns)


def _format_turns(turns: list[ConversationTurn]) -> str:
    if not turns:
        return "No matching conversation turns."
    return "\n".join(
        f"{turn.observed_at.isoformat()} {turn.role.value}: {turn.content}" for turn in turns
    )


def _optional_date(value: JsonScalar) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _bounded_positive_int(
    value: JsonScalar,
    *,
    default: int,
    upper_bound: int = MAX_HISTORY_LIMIT,
) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(value, upper_bound)
    return min(default, upper_bound)
