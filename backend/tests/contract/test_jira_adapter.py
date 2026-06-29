from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from core.domain.errors import ProviderUnavailable
from core.domain.integrations import IssueState, SyncCursor, UserRef
from infra.adapters.jira.jira_adapter import JiraIssueTrackerAdapter


@respx.mock
async def test_jira_adapter_maps_read_payloads_and_rejects_writes() -> None:
    adapter = JiraIssueTrackerAdapter(
        base_url="https://jira.test",
        email="agent@example.com",
        api_token="token",
    )
    respx.get("https://jira.test/rest/api/3/project/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "id": "10001",
                        "key": "PO",
                        "name": "PulseOps",
                        "projectTypeKey": "software",
                    }
                ]
            },
        )
    )
    respx.get("https://jira.test/rest/api/3/search").mock(
        side_effect=[
            httpx.Response(200, json={"issues": [_issue_payload("PO-1")]}),
            httpx.Response(200, json={"issues": [_issue_payload("PO-3")]}),
            httpx.Response(200, json={"issues": [_issue_payload("PO-2")]}),
        ]
    )
    respx.get("https://jira.test/rest/agile/1.0/board/board-1/sprint").mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "id": 7,
                        "name": "Phase 1",
                        "state": "active",
                        "startDate": "2026-01-05T09:00:00.000Z",
                        "endDate": "2026-01-16T17:00:00.000Z",
                    }
                ]
            },
        )
    )
    respx.get("https://jira.test/rest/api/3/issue/PO-1").mock(
        return_value=httpx.Response(200, json=_issue_payload("PO-1"))
    )

    projects = await adapter.list_projects("demo")
    issues = await adapter.list_issues_updated_since(
        "demo",
        "PO",
        SyncCursor(updated_at=datetime(2026, 1, 1, tzinfo=UTC)),
    )
    query_issues = await adapter.list_issues_for_query(
        "demo",
        'project = "PO" AND component = API',
        SyncCursor(updated_at=datetime(2026, 1, 2, tzinfo=UTC)),
    )
    sprints = await adapter.list_sprints("demo", "board-1")
    issue = await adapter.get_issue("demo", "PO-1")
    active = await adapter.list_active_for(UserRef(tenant_id="demo", external_id="account-1"))

    assert projects[0].key == "PO"
    assert issues[0].key == "PO-1"
    assert query_issues[0].key == "PO-3"
    assert issues[0].state is IssueState.IN_PROGRESS
    assert issues[0].assignee is not None
    assert issues[0].assignee.external_id == "account-1"
    assert issues[0].metadata["project_key"] == "PO"
    assert sprints[0].id == "7"
    assert issue.title == "Wire read-only adapters"
    assert active[0].key == "PO-2"

    with pytest.raises(ProviderUnavailable):
        await adapter.transition("demo", "PO-1", IssueState.DONE.value)
    with pytest.raises(ProviderUnavailable):
        await adapter.add_comment("demo", "PO-1", "done")
    assert {call.request.method for call in respx.calls} == {"GET"}
    search_jqls = [
        str(call.request.url.params["jql"])
        for call in respx.calls
        if call.request.url.path == "/rest/api/3/search"
    ]
    assert (
        '(project = "PO" AND component = API) AND updated > "2026-01-02T00:00:00+00:00" '
        "ORDER BY updated ASC"
    ) in search_jqls


def _issue_payload(key: str) -> dict[str, object]:
    return {
        "id": "10010",
        "key": key,
        "fields": {
            "summary": "Wire read-only adapters",
            "updated": "2026-01-10T08:30:00.000+0000",
            "status": {
                "name": "In Progress",
                "statusCategory": {"key": "indeterminate", "name": "In Progress"},
            },
            "project": {"id": "10001", "key": "PO"},
            "issuetype": {"name": "Story"},
            "parent": {"key": "EPIC-1"},
            "assignee": {
                "accountId": "account-1",
                "displayName": "Asha",
            },
        },
    }
