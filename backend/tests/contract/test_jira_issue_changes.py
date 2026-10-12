"""The Jira history read: kept fields only, no free text, Cloud paged and Data Center expanded."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from core.domain.connections import ConnectionValues
from core.domain.errors import ProviderUnavailable
from core.domain.integrations import IssueChange, IssueChangeField, UserRef
from infra.adapters.jira.jira_adapter import JiraIssueTrackerAdapter


class _Connection:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
        del tenant_id
        if connector != "jira":
            return None
        return ConnectionValues(connector="jira", values=self._values)


_CLOUD = {
    "deployment": "cloud",
    "base_url": "https://example.atlassian.net",
    "auth_method": "api_token",
    "email": "bot@example.com",
    "api_token": "test-token",
    "story_points_field": "customfield_10016",
    "severity_field": "customfield_10050",
}
_DATA_CENTER = {
    "deployment": "data_center",
    "base_url": "https://jira.example.test",
    "auth_method": "personal_access_token",
    "personal_access_token": "test-pat",
    "story_points_field": "customfield_10002",
    "severity_field": "customfield_10070",
}


def _item(
    field: str, *, field_id: str | None = None, custom: bool = False, **values: object
) -> dict[str, object]:
    item: dict[str, object] = {"field": field, "fieldtype": "custom" if custom else "jira"}
    if field_id is not None:
        item["fieldId"] = field_id
    item.update(values)
    return item


def _history(
    history_id: str, created: str, *items: dict[str, object], author: object = "default"
) -> dict[str, object]:
    history: dict[str, object] = {"id": history_id, "created": created, "items": list(items)}
    if author == "default":
        history["author"] = {"accountId": "acc-ana", "displayName": "Ana Example"}
    elif author is not None:
        history["author"] = author
    return history


@respx.mock
async def test_cloud_history_keeps_the_listed_fields_and_pages_until_the_last() -> None:
    adapter = JiraIssueTrackerAdapter(connections=_Connection(_CLOUD))
    first_page = [
        _history(
            "100",
            "2026-10-01T09:00:00.000+0000",
            _item(
                "status",
                field_id="status",
                **{"from": "1", "fromString": "To Do", "to": "3", "toString": "In Progress"},
            ),
            _item(
                "assignee",
                field_id="assignee",
                **{
                    "from": None,
                    "fromString": None,
                    "to": "acc-ana",
                    "toString": "Ana Example",
                },
            ),
            # Not a kept field: dropped.
            _item("Rank", field_id="customfield_10019", custom=True, toString="Ranked higher"),
        ),
        _history(
            "101",
            "2026-10-02T10:00:00.000+0000",
            _item(
                "summary",
                field_id="summary",
                fromString="Old words people typed",
                toString="New words people typed",
            ),
            _item("description", field_id="description", toString="A long description"),
            author=None,
        ),
    ]
    second_page = [
        _history(
            "102",
            "2026-10-03T11:00:00.000+0000",
            _item(
                "duedate",
                field_id="duedate",
                **{
                    "from": "2026-10-10",
                    "fromString": "2026-10-10 00:00:00.0",
                    "to": "2026-10-14",
                    "toString": "2026-10-14 00:00:00.0",
                },
            ),
            # One version left, one joined: one change, removed and added.
            _item("Fix Version", field_id="fixVersions", fromString="R1", toString=None),
            _item("Fix Version", field_id="fixVersions", fromString=None, toString="R2"),
            _item("labels", field_id="labels", fromString="checkout", toString="CR checkout"),
            _item(
                "Story point estimate",
                field_id="customfield_10016",
                custom=True,
                fromString="3",
                toString="5.0",
            ),
            _item(
                "Sprint",
                field_id="customfield_10020",
                custom=True,
                fromString="Sprint 1",
                toString="Sprint 1, Sprint 2",
            ),
            _item(
                "Severity",
                field_id="customfield_10050",
                custom=True,
                fromString=None,
                toString="S2 - Major",
            ),
            _item(
                "IssueParentAssociation",
                field_id="parent",
                **{"from": None, "to": "10001", "toString": "CHK-1"},
            ),
            _item("Component", field_id="components", toString="Payments"),
            _item("resolution", field_id="resolution", toString="Done"),
            _item("issuetype", field_id="issuetype", fromString="Task", toString="Bug"),
            _item("priority", field_id="priority", fromString="Medium", toString="High"),
        ),
    ]
    route = respx.get("https://example.atlassian.net/rest/api/3/issue/CHK-4/changelog").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "startAt": 0,
                    "maxResults": 2,
                    "total": 3,
                    "isLast": False,
                    "values": first_page,
                },
            ),
            httpx.Response(
                200,
                json={
                    "startAt": 2,
                    "maxResults": 2,
                    "total": 3,
                    "isLast": True,
                    "values": second_page,
                },
            ),
        ]
    )

    changes = await adapter.list_issue_changes("demo", "CHK-4", None)

    assert [call.request.url.params["startAt"] for call in route.calls] == ["0", "2"]
    ana = UserRef(tenant_id="demo", external_id="acc-ana", display_name="Ana Example")
    by_field = {change.field: change for change in changes}
    assert by_field[IssueChangeField.STATUS] == IssueChange(
        history_id="100",
        author=ana,
        at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
        field=IssueChangeField.STATUS,
        from_value="To Do",
        to_value="In Progress",
    )
    # An assignee is its account id, never its name.
    assert (
        by_field[IssueChangeField.ASSIGNEE].from_value,
        by_field[IssueChangeField.ASSIGNEE].to_value,
    ) == (None, "acc-ana")
    # Free text is never kept: only that the summary and description changed.
    for field in (IssueChangeField.SUMMARY, IssueChangeField.DESCRIPTION):
        assert by_field[field].from_value is None and by_field[field].to_value is None
        assert by_field[field].author is None
    values = {change.field: (change.from_value, change.to_value) for change in changes}
    assert values[IssueChangeField.DUE_DATE] == ("2026-10-10", "2026-10-14")
    assert values[IssueChangeField.FIX_VERSION] == ("R1", "R2")
    assert values[IssueChangeField.LABELS] == (None, "CR")
    assert values[IssueChangeField.STORY_POINTS] == ("3", "5")
    assert values[IssueChangeField.SPRINT] == (None, "Sprint 2")
    assert values[IssueChangeField.SEVERITY] == (None, "S2 - Major")
    assert values[IssueChangeField.PARENT] == (None, "CHK-1")
    assert values[IssueChangeField.COMPONENTS] == (None, "Payments")
    assert values[IssueChangeField.RESOLUTION] == (None, "Done")
    assert values[IssueChangeField.ISSUE_TYPE] == ("Task", "Bug")
    assert values[IssueChangeField.PRIORITY] == ("Medium", "High")
    assert len(changes) == len(values)
    assert all("Rank" not in str(change) for change in changes)
    assert [change.at for change in changes] == sorted(change.at for change in changes)


@respx.mock
async def test_cloud_history_before_since_is_left_out() -> None:
    adapter = JiraIssueTrackerAdapter(connections=_Connection(_CLOUD))
    respx.get("https://example.atlassian.net/rest/api/3/issue/CHK-4/changelog").mock(
        return_value=httpx.Response(
            200,
            json={
                "isLast": True,
                "values": [
                    _history(
                        "1",
                        "2026-10-01T09:00:00.000+0000",
                        _item("status", field_id="status", fromString="To Do", toString="Doing"),
                    ),
                    _history(
                        "2",
                        "2026-10-05T09:00:00.000+0000",
                        _item("status", field_id="status", fromString="Doing", toString="Done"),
                    ),
                ],
            },
        )
    )

    changes = await adapter.list_issue_changes(
        "demo", "CHK-4", datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    )

    assert [change.history_id for change in changes] == ["2"]


@respx.mock
async def test_a_set_field_that_ends_where_it_started_is_no_change() -> None:
    adapter = JiraIssueTrackerAdapter(connections=_Connection(_CLOUD))
    respx.get("https://example.atlassian.net/rest/api/3/issue/CHK-4/changelog").mock(
        return_value=httpx.Response(
            200,
            json={
                "isLast": True,
                "values": [
                    _history(
                        "1",
                        "2026-10-01T09:00:00.000+0000",
                        _item("labels", field_id="labels", fromString="b a", toString="a b"),
                        _item("status", field_id="status", fromString="Doing", toString="Doing"),
                    )
                ],
            },
        )
    )

    assert await adapter.list_issue_changes("demo", "CHK-4", None) == []


@respx.mock
async def test_data_center_reads_the_expanded_history_and_names_custom_fields_once() -> None:
    adapter = JiraIssueTrackerAdapter(connections=_Connection(_DATA_CENTER))
    issue = respx.get("https://jira.example.test/rest/api/2/issue/CHK-7").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "CHK-7",
                "fields": {"updated": "2026-10-03T11:00:00.000+0000"},
                "changelog": {
                    "startAt": 0,
                    "total": 1,
                    "histories": [
                        _history(
                            "9",
                            "2026-10-03T11:00:00.000+0000",
                            _item("status", fromString="Open", toString="In Review"),
                            _item(
                                "assignee",
                                **{
                                    "from": "kim",
                                    "fromString": "Kim",
                                    "to": "ana",
                                    "toString": "Ana",
                                },
                            ),
                            _item("Fix Version", fromString=None, toString="2.4"),
                            _item("Component", fromString="Web", toString=None),
                            _item("Story Points", custom=True, fromString="2", toString="3"),
                            _item("Bug severity", custom=True, fromString=None, toString="High"),
                            _item(
                                "Epic Link",
                                custom=True,
                                fromString=None,
                                toString="CHK-2",
                            ),
                            # A custom field nobody configured: dropped.
                            _item("Team", custom=True, toString="Blue"),
                            author={"name": "ana", "key": "JIRAUSER1", "displayName": "Ana"},
                        )
                    ],
                },
            },
        )
    )
    field_list = respx.get("https://jira.example.test/rest/api/2/field").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "customfield_10002", "name": "Story Points"},
                {"id": "customfield_10070", "name": "Bug severity"},
                {"id": "customfield_10080", "name": "Team"},
            ],
        )
    )

    changes = await adapter.list_issue_changes("demo", "CHK-7", None)
    await adapter.list_issue_changes("demo", "CHK-7", None)

    assert issue.calls.last.request.url.params["expand"] == "changelog"
    assert field_list.call_count == 1
    values = {change.field: (change.from_value, change.to_value) for change in changes}
    assert values == {
        IssueChangeField.STATUS: ("Open", "In Review"),
        IssueChangeField.ASSIGNEE: ("kim", "ana"),
        IssueChangeField.FIX_VERSION: (None, "2.4"),
        IssueChangeField.COMPONENTS: ("Web", None),
        IssueChangeField.STORY_POINTS: ("2", "3"),
        IssueChangeField.SEVERITY: (None, "High"),
        IssueChangeField.PARENT: (None, "CHK-2"),
    }
    assert {change.author for change in changes} == {
        UserRef(tenant_id="demo", external_id="ana", display_name="Ana")
    }


@respx.mock
async def test_a_failed_history_read_is_unavailable_without_its_text() -> None:
    adapter = JiraIssueTrackerAdapter(connections=_Connection(_CLOUD))
    respx.get("https://example.atlassian.net/rest/api/3/issue/CHK-4/changelog").mock(
        return_value=httpx.Response(500, text="stack trace with test-token")
    )

    with pytest.raises(ProviderUnavailable) as raised:
        await adapter.list_issue_changes("demo", "CHK-4", None)

    assert "test-token" not in str(raised.value)


@respx.mock
async def test_the_issue_read_adds_reporter_creator_components_and_severity() -> None:
    adapter = JiraIssueTrackerAdapter(connections=_Connection(_CLOUD))
    route = respx.get("https://example.atlassian.net/rest/api/3/issue/CHK-4").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "CHK-4",
                "fields": {
                    "summary": "Guest checkout",
                    "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                    "reporter": {"accountId": "acc-mina", "displayName": "Mina"},
                    "creator": {"accountId": "acc-ira", "displayName": "Ira"},
                    "components": [{"name": "Payments"}, {"name": "Web"}],
                    "customfield_10050": {"value": "S2 - Major", "id": "10301"},
                    "customfield_10016": 5,
                },
            },
        )
    )

    issue = await adapter.get_issue("demo", "CHK-4")

    fields = route.calls.last.request.url.params["fields"].split(",")
    assert {"reporter", "creator", "components", "customfield_10050"} <= set(fields)
    assert issue.metadata["reporter_id"] == "acc-mina"
    assert issue.metadata["creator_id"] == "acc-ira"
    assert issue.metadata["components"] == "Payments, Web"
    assert issue.metadata["severity"] == "S2 - Major"
    assert issue.metadata["story_points"] == 5.0
    # Names stay in Jira: only account ids are kept.
    assert "Mina" not in {value for value in issue.metadata.values() if isinstance(value, str)}
