from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from core.ports.repositories import TimeSeriesRepository

DEFAULT_GIT_LOOKBACK_DAYS = 7
DEFAULT_GIT_ACTIVITY_LIMIT = 8
MAX_GIT_ACTIVITY_LIMIT = 20
GIT_FACT_SOURCES = ("vcs_commit", "vcs_pull_request")


def _parameters_schema() -> Mapping[str, object]:
    return {
        "type": "object",
        "properties": {
            "issue_key": {
                "type": "string",
                "description": "Optional issue key to filter matching commit or PR metadata/text.",
            },
            "since_days": {
                "type": "integer",
                "minimum": 1,
                "description": "Number of recent days to inspect.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_GIT_ACTIVITY_LIMIT,
                "description": "Maximum commits or PRs to return.",
            },
        },
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class GitActivityTool:
    tenant_id: str
    developer_id: str
    repository: TimeSeriesRepository
    reference_at: datetime | None = None

    name: str = "fetch_git_activity"
    description: str = (
        "Fetch recent read-only Git commit and pull request facts for this developer, optionally "
        "filtered by issue key."
    )
    parameters: Mapping[str, object] = field(default_factory=_parameters_schema)

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        since_days = _bounded_positive_int(
            arguments.get("since_days"),
            default=DEFAULT_GIT_LOOKBACK_DAYS,
        )
        limit = _bounded_positive_int(arguments.get("limit"), default=DEFAULT_GIT_ACTIVITY_LIMIT)
        issue_key = _clean_string(arguments.get("issue_key"))
        reference_at = self.reference_at or datetime.now(tz=UTC)
        facts = await self.repository.list_facts(
            self.tenant_id,
            EntityRef(tenant_id=self.tenant_id, kind=NodeKind.DEVELOPER, id=self.developer_id),
            reference_at - timedelta(days=since_days),
        )
        matching = [
            fact
            for fact in facts
            if fact.source in GIT_FACT_SOURCES and _matches_issue_key(fact, issue_key)
        ]
        matching.sort(key=lambda fact: fact.observed_at, reverse=True)
        return _format_facts(matching[:limit])


def _format_facts(facts: list[FactEvent]) -> str:
    if not facts:
        return "No matching Git activity."
    return "\n".join(_format_fact(fact) for fact in facts)


def _format_fact(fact: FactEvent) -> str:
    payload = fact.payload
    if fact.source == "vcs_pull_request":
        title = _payload_string(payload, "title") or "untitled PR"
        repo = _payload_string(payload, "repo")
        merged = payload.get("merged")
        details = [f"title={title}", f"merged={merged}"]
        if repo is not None:
            details.insert(0, f"repo={repo}")
        return f"{fact.observed_at.isoformat()} pull_request: " + ", ".join(details)

    message = _payload_string(payload, "message") or "no commit message"
    sha = _payload_string(payload, "sha")
    repo = _payload_string(payload, "repo")
    details = [f"message={message}"]
    if sha is not None:
        details.insert(0, f"sha={sha}")
    if repo is not None:
        details.insert(0, f"repo={repo}")
    return f"{fact.observed_at.isoformat()} commit: " + ", ".join(details)


def _matches_issue_key(fact: FactEvent, issue_key: str | None) -> bool:
    if issue_key is None:
        return True
    if fact.entity_ref.id.casefold() == issue_key.casefold():
        return True
    pattern = _issue_key_pattern(issue_key)
    for value in fact.payload.values():
        if isinstance(value, str) and pattern.search(value):
            return True
    return False


def _issue_key_pattern(issue_key: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(issue_key)}(?![A-Za-z0-9])", re.IGNORECASE)


def _payload_string(payload: Mapping[str, JsonScalar], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _clean_string(value: JsonScalar) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _bounded_positive_int(
    value: JsonScalar,
    *,
    default: int,
    upper_bound: int = MAX_GIT_ACTIVITY_LIMIT,
) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(value, upper_bound)
    return min(default, upper_bound)
