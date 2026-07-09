from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from core.domain.graph import JsonScalar
from core.domain.integrations import Issue, UserRef
from core.ports.issue_tracker import IssueTracker

DEFAULT_ACTIVE_ISSUE_LIMIT = 8
MAX_ACTIVE_ISSUE_LIMIT = 20


def _parameters_schema() -> Mapping[str, object]:
    return {
        "type": "object",
        "properties": {
            "issue_key": {
                "type": "string",
                "description": "Optional exact issue key to fetch.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_ACTIVE_ISSUE_LIMIT,
                "description": "Maximum active issues to return when issue_key is omitted.",
            },
        },
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class IssueTrackerTool:
    tenant_id: str
    developer_id: str
    issue_tracker: IssueTracker

    name: str = "fetch_issue_tracker_context"
    description: str = (
        "Fetch read-only Jira issue context for this developer. Use issue_key for an exact issue "
        "or omit it to list active assigned issues."
    )
    parameters: Mapping[str, object] = field(default_factory=_parameters_schema)

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        issue_key = _clean_string(arguments.get("issue_key"))
        if issue_key is not None:
            try:
                return _format_issues(
                    [await self.issue_tracker.get_issue(self.tenant_id, issue_key)]
                )
            except KeyError:
                return f"Issue {issue_key} could not be fetched."

        limit = _bounded_positive_int(arguments.get("limit"), default=DEFAULT_ACTIVE_ISSUE_LIMIT)
        issues = await self.issue_tracker.list_active_for(
            UserRef(tenant_id=self.tenant_id, external_id=self.developer_id)
        )
        return _format_issues(issues[:limit])


def _format_issues(issues: list[Issue]) -> str:
    if not issues:
        return "No matching issues."
    return "\n".join(_format_issue(issue) for issue in issues)


def _format_issue(issue: Issue) -> str:
    updated = issue.updated_at.isoformat() if issue.updated_at is not None else "unknown"
    return f"{issue.key}: {issue.title} | state={issue.state.value} | updated_at={updated}"


def _clean_string(value: JsonScalar) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _bounded_positive_int(
    value: JsonScalar,
    *,
    default: int,
    upper_bound: int = MAX_ACTIVE_ISSUE_LIMIT,
) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(value, upper_bound)
    return min(default, upper_bound)
