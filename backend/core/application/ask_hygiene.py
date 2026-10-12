"""Ora's ``jira_hygiene`` tool: what is missing or wrong in Jira, as the hygiene board reads it.

It reads the same board ``GET /projects/{id}/hygiene`` and ``GET /pods/{id}/hygiene``
serve, behind the readiness board's own read rule (readiness_access), so Ask never
reads more than the asker could fetch. A refusal comes back in the route's words,
as ``{"error"}``. An executive gets each rule's counts and never who; a developer
gets no Ask tools at all (``may_ask``). It writes nothing, here or in Jira.

Findings are about issues: keys, titles, field names and dates, never Jira text,
and no count per person.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from core.application.forecast_service import ForecastService
from core.application.hygiene_service import BoardView, HygieneService
from core.application.readiness_access import pod_viewer, project_viewer
from core.application.release_readiness_service import ReleaseReadinessService
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.graph import JsonScalar, NodeKind
from core.domain.hygiene import HygieneState
from core.ports.repositories import GraphRepository

MAX_FINDINGS = 20

HYGIENE_TOOL_GUIDANCE = (
    "What is missing or wrong in Jira -- issues without an estimate, a due date, an epic, "
    "acceptance criteria or a severity, work with no movement, scope added after planning "
    "without a change label -- is answered from jira_hygiene: each rule with its open count, "
    "and the issues that break it with what is wrong, who is asked and the suggested fix. "
    "Name issues by key and title; never count or rank people from it. "
)


@dataclass(frozen=True, kw_only=True)
class JiraHygieneTool:
    principal: Principal
    service: HygieneService
    readiness: ReleaseReadinessService
    forecasts: ForecastService
    repository: GraphRepository
    #: The day asked about. Hygiene keeps no history of its own: a board read later says so.
    as_of: date

    name: str = "jira_hygiene"
    description: str = (
        "What is missing or wrong in a project's or pod's Jira issues, as the tenant's Jira "
        "hygiene rules find it: each rule (its name, whether it lists or asks) with how many "
        "issues break it now and how many are new today, and up to 20 of those issues with "
        "what is wrong, the owner asked, their profile, the suggested fix, how long it has "
        "been open and the stage. Use it for 'is Jira clean for X', 'which stories have no "
        "estimate', 'what was added after planning without a change label', 'scope creep'. "
        "Give project_id or pod_id (search_graph_nodes turns a name into an id); release_id "
        "keeps one release of the project, rule_id one rule, and state all also lists what "
        "people waived or set aside."
    )
    parameters: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "pod_id": {"type": "string"},
                "release_id": {"type": "string"},
                "rule_id": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "all"]},
            },
            "additionalProperties": False,
        }
    )

    async def run(self, arguments: Mapping[str, JsonScalar]) -> str:
        project_id = _text(arguments.get("project_id"))
        pod_id = _text(arguments.get("pod_id"))
        release_id = _text(arguments.get("release_id"))
        rule_id = _text(arguments.get("rule_id"))
        every = _text(arguments.get("state")) == "all"
        tenant_id = self.principal.tenant_id
        node_id = project_id or pod_id
        if node_id is None:
            return _error("give project_id or pod_id; search_graph_nodes finds either by name")
        node = await self.repository.get_node(tenant_id, node_id)
        if node is None:
            return _error(f"{node_id} not found; search_graph_nodes finds a project or pod by name")
        try:
            if node.kind is NodeKind.PROJECT:
                viewer = await project_viewer(
                    self.principal, self.forecasts, self.readiness, node.id, reading=True
                )
                release = None
                if release_id:
                    release = await self.forecasts.release(tenant_id, release_id)
                    if release.project_id != node.id:
                        return _error(f"{release_id} is no release of {node.id}")
                board = await self.service.project_board(
                    tenant_id,
                    node.id,
                    viewer,
                    release=release,
                    pod_id=pod_id if project_id and pod_id else None,
                )
            elif node.kind is NodeKind.POD:
                viewer = await pod_viewer(self.principal, self.forecasts, node.id)
                board = await self.service.pod_board(tenant_id, node.id, viewer)
            else:
                return _error(
                    f"{node_id} is a {node.kind.value}; jira_hygiene covers a project or pod"
                )
        except AuthorizationDenied as exc:
            return _error(str(exc))
        except GraphNotFound as exc:
            return _error(str(exc))
        return json.dumps(
            hygiene_payload(board, self.as_of, rule_id=rule_id, every=every), ensure_ascii=False
        )


def hygiene_payload(
    board: BoardView, as_of: date, *, rule_id: str | None = None, every: bool = False
) -> dict[str, object]:
    """A hygiene board for the model: each rule's counts, then the issues, by key and title."""
    rows = [row for row in board.rules if rule_id is None or row.rule.rule_id == rule_id]
    checked = board.last_run.finished_at if board.last_run else None
    payload: dict[str, object] = {
        "scope_name": board.scope_name,
        "scan_on": board.settings.enabled,
        "last_checked": checked.isoformat() if checked else None,
        "jira_data_as_of": board.jira.as_of.isoformat() if board.jira.as_of else None,
        "jira_data_stale": board.jira.stale,
        "rules": [
            {
                "rule_id": row.rule.rule_id,
                "name": row.rule.name,
                "rule": row.stated,
                "asks": row.rule.asks,
                "open": row.open,
                "new_today": row.new_today,
                "oldest_open_days": row.oldest_days,
            }
            for row in rows
        ],
        "held_for_stale_data": sum(row.held for row in rows),
    }
    if board.counts_only:
        payload["counts_only"] = "Executives read each rule's counts, not the issues or owners."
    else:
        findings = [
            view
            for view in board.findings
            if (rule_id is None or view.rule.rule_id == rule_id)
            and (every or view.finding.state is HygieneState.OPEN)
        ]
        payload["findings"] = [
            {
                "key": view.finding.issue_key,
                "title": view.title,
                "rule": view.rule.name,
                "what": view.finding.what,
                "state": view.finding.state.value,
                "owner": view.owner_name,
                "owner_profile": view.owner_profile_label,
                "suggested_fix": view.suggested_fix,
                "open_days": view.open_days,
                "stage": view.finding.stage.value if view.finding.stage else None,
                "blocking": view.rule.blocking,
                "scope": view.scope_name,
            }
            for view in findings[:MAX_FINDINGS]
        ]
        if len(findings) > MAX_FINDINGS:
            payload["more_findings"] = len(findings) - MAX_FINDINGS
    if not board.rules:
        payload["no_rules"] = (
            "No Jira hygiene rule is on, so nothing is checked: this says nothing about "
            "whether Jira is clean."
            if board.settings.enabled
            else "The Jira hygiene scan is off for this tenant, so nothing is checked."
        )
    if checked is not None and checked.date() > as_of:
        payload["history"] = (
            f"Jira hygiene keeps no history: this is the scan of {checked.date()}, which may "
            f"not be how it stood on {as_of.isoformat()}."
        )
    return payload


def _text(value: JsonScalar) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)
