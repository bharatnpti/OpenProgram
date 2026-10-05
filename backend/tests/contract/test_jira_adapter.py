from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from core.domain.connections import ConnectionValues
from core.domain.errors import ProviderUnavailable
from core.domain.integrations import IssueState, SyncCursor, UserRef
from infra.adapters.jira.jira_adapter import JiraIssueTrackerAdapter, _resolve_transition_id


def _transition(transition_id: str, name: str, to_name: str, category: str) -> dict[str, object]:
    return {
        "id": transition_id,
        "name": name,
        "to": {"name": to_name, "statusCategory": {"key": category}},
    }


# The QA Jira workflow: global transitions To Do / In Progress / Done, each also
# offered from the status the issue is already in (a self-transition).
_QA_WORKFLOW = {
    "transitions": [
        _transition("11", "To Do", "To Do", "new"),
        _transition("21", "In Progress", "In Progress", "indeterminate"),
        _transition("31", "Done", "Done", "done"),
    ]
}


@pytest.mark.parametrize(
    ("canonical", "transition_id"),
    [
        ("todo", "11"),
        ("in_progress", "21"),
        # No review status in this workflow: review reads as in progress.
        ("in_review", "21"),
        ("done", "31"),
        # Free-text labels still match by name, as before.
        ("In Progress", "21"),
    ],
)
def test_canonical_states_resolve_to_the_qa_workflow_transitions(
    canonical: str, transition_id: str
) -> None:
    assert _resolve_transition_id(_QA_WORKFLOW, canonical) == transition_id


def test_canonical_states_without_a_matching_status_resolve_to_nothing() -> None:
    # No Blocked status in the QA workflow, and free text matches no transition.
    assert _resolve_transition_id(_QA_WORKFLOW, "blocked") is None
    assert _resolve_transition_id(_QA_WORKFLOW, "merged and ready to close") is None
    assert _resolve_transition_id(_QA_WORKFLOW, "on track") is None


def test_canonical_states_prefer_the_workflow_status_they_name() -> None:
    workflow = {
        "transitions": [
            _transition("5", "Won't Do", "Won't Do", "done"),
            _transition("6", "Backlog", "Backlog", "new"),
            _transition("7", "Start", "In Progress", "indeterminate"),
            _transition("8", "Ready for review", "In Review", "indeterminate"),
            _transition("9", "Close", "Done", "done"),
            _transition("10", "Hold", "Blocked", "indeterminate"),
        ]
    }
    assert _resolve_transition_id(workflow, "done") == "9"
    assert _resolve_transition_id(workflow, "in_review") == "8"
    assert _resolve_transition_id(workflow, "in_progress") == "7"
    assert _resolve_transition_id(workflow, "todo") == "6"
    assert _resolve_transition_id(workflow, "blocked") == "10"


@respx.mock
async def test_jira_adapter_maps_read_payloads() -> None:
    adapter = JiraIssueTrackerAdapter(
        base_url="https://jira.test",
        email="agent@example.com",
        api_token="token",
        clock=lambda: datetime(2026, 1, 2, 1, 0, tzinfo=UTC),
    )
    respx.get("https://jira.test/rest/api/3/project/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {
                        "id": "10001",
                        "key": "PO",
                        "name": "OpenProgram",
                        "projectTypeKey": "software",
                    }
                ]
            },
        )
    )
    respx.get("https://jira.test/rest/api/3/search/jql").mock(
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
    assert {call.request.method for call in respx.calls} == {"GET"}
    search_jqls = [
        str(call.request.url.params["jql"])
        for call in respx.calls
        if call.request.url.path == "/rest/api/3/search/jql"
    ]
    # 60 minutes since the cursor plus the 2-minute overlap, as a relative date.
    assert (
        '(project = "PO" AND component = API) AND updated >= -62m ORDER BY updated ASC'
    ) in search_jqls
    assert not any("2026-01-02T" in jql for jql in search_jqls)


@respx.mock
async def test_jira_incremental_search_drops_issues_the_cursor_already_covers() -> None:
    """The relative window overlaps the cursor; anything not newer than it is dropped."""
    adapter = JiraIssueTrackerAdapter(
        base_url="https://jira.test",
        email="agent@example.com",
        api_token="token",
        clock=lambda: datetime(2026, 1, 10, 9, 0, 30, tzinfo=UTC),
    )
    already_seen = _issue_payload("PO-1")  # updated 08:30:00.000, the cursor itself
    newer = _issue_payload("PO-2")
    cast_fields = newer["fields"]
    assert isinstance(cast_fields, dict)
    cast_fields["updated"] = "2026-01-10T14:00:01.000+0530"  # 08:30:01 UTC
    route = respx.get("https://jira.test/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": [already_seen, newer]})
    )

    issues = await adapter.list_issues_for_query(
        "demo",
        'project = "PO"',
        SyncCursor(updated_at=datetime(2026, 1, 10, 8, 30, tzinfo=UTC)),
    )

    assert [issue.key for issue in issues] == ["PO-2"]
    # 30.5 minutes + 2 overlap rounds up to 33.
    assert route.calls.last.request.url.params["jql"] == (
        '(project = "PO") AND updated >= -33m ORDER BY updated ASC'
    )


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


@respx.mock
async def test_jira_adapter_applies_transition_and_comment() -> None:
    adapter = JiraIssueTrackerAdapter(
        base_url="https://jira.test",
        email="agent@example.com",
        api_token="token",
    )
    transitions_route = respx.get("https://jira.test/rest/api/3/issue/PO-1/transitions").mock(
        return_value=httpx.Response(
            200,
            json={
                "transitions": [
                    {
                        "id": "11",
                        "name": "Start Progress",
                        "to": {
                            "name": "In Progress",
                            "statusCategory": {"key": "indeterminate"},
                        },
                    },
                    {
                        "id": "31",
                        "name": "Finish",
                        "to": {"name": "Done", "statusCategory": {"key": "done"}},
                    },
                ]
            },
        )
    )
    post_transition = respx.post("https://jira.test/rest/api/3/issue/PO-1/transitions").mock(
        return_value=httpx.Response(204)
    )
    post_comment = respx.post("https://jira.test/rest/api/3/issue/PO-1/comment").mock(
        return_value=httpx.Response(201, json={"id": "10000"})
    )

    await adapter.transition("demo", "PO-1", IssueState.DONE.value)
    await adapter.add_comment("demo", "PO-1", "shipped it")

    assert transitions_route.called
    assert post_transition.called
    assert json.loads(post_transition.calls.last.request.content) == {"transition": {"id": "31"}}
    comment_body = json.loads(post_comment.calls.last.request.content)["body"]
    assert comment_body["type"] == "doc"
    assert comment_body["content"][0]["content"][0]["text"] == "shipped it"


@respx.mock
async def test_jira_adapter_transition_raises_when_state_unavailable() -> None:
    adapter = JiraIssueTrackerAdapter(
        base_url="https://jira.test",
        email="agent@example.com",
        api_token="token",
    )
    respx.get("https://jira.test/rest/api/3/issue/PO-1/transitions").mock(
        return_value=httpx.Response(
            200,
            json={
                "transitions": [
                    {
                        "id": "11",
                        "name": "Start Progress",
                        "to": {
                            "name": "In Progress",
                            "statusCategory": {"key": "indeterminate"},
                        },
                    }
                ]
            },
        )
    )
    with pytest.raises(ProviderUnavailable):
        await adapter.transition("demo", "PO-1", IssueState.DONE.value)


@respx.mock
async def test_jira_adapter_finds_user_by_email_when_address_is_hidden() -> None:
    # Jira blanks emailAddress unless the owner made it visible, which is the
    # common case: a single active human result is the match.
    adapter = JiraIssueTrackerAdapter(
        base_url="https://jira.test", email="agent@example.com", api_token="token"
    )
    route = respx.get("https://jira.test/rest/api/3/user/search").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "accountId": "712020:liam",
                    "accountType": "atlassian",
                    "displayName": "Liam Chen",
                    "emailAddress": "",
                    "active": True,
                }
            ],
        )
    )

    found = await adapter.find_user_by_email("demo", "liam@example.com")

    assert found == UserRef(tenant_id="demo", external_id="712020:liam", display_name="Liam Chen")
    assert route.calls.last.request.url.params["query"] == "liam@example.com"


@respx.mock
async def test_jira_adapter_find_user_by_email_prefers_visible_exact_match() -> None:
    adapter = JiraIssueTrackerAdapter(
        base_url="https://jira.test", email="agent@example.com", api_token="token"
    )
    respx.get("https://jira.test/rest/api/3/user/search").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"accountId": "a-long", "emailAddress": "bob@example.com.au", "active": True},
                {"accountId": "a-bob", "emailAddress": "Bob@Example.com", "active": True},
                {"accountId": "app-1", "accountType": "app", "emailAddress": ""},
            ],
        )
    )

    found = await adapter.find_user_by_email("demo", "bob@example.com")

    assert found is not None
    assert found.external_id == "a-bob"


@respx.mock
async def test_jira_adapter_find_user_by_email_refuses_to_guess() -> None:
    adapter = JiraIssueTrackerAdapter(
        base_url="https://jira.test", email="agent@example.com", api_token="token"
    )
    respx.get("https://jira.test/rest/api/3/user/search").mock(
        side_effect=[
            # two hidden-email accounts: ambiguous
            httpx.Response(200, json=[{"accountId": "a-1"}, {"accountId": "a-2"}]),
            # one visible address that is not the one asked for
            httpx.Response(200, json=[{"accountId": "a-3", "emailAddress": "other@example.com"}]),
            # only a deactivated account
            httpx.Response(200, json=[{"accountId": "a-4", "active": False}]),
        ]
    )

    assert await adapter.find_user_by_email("demo", "x@example.com") is None
    assert await adapter.find_user_by_email("demo", "x@example.com") is None
    assert await adapter.find_user_by_email("demo", "x@example.com") is None


# --- Jira Data Center and connections set up in admin --------------------------


class _Connection:
    def __init__(self, values: dict[str, str] | None) -> None:
        self._values = values

    async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
        del tenant_id
        if self._values is None or connector != "jira":
            return None
        return ConnectionValues(connector="jira", values=self._values)


_DATA_CENTER = {
    "deployment": "data_center",
    "base_url": "https://jira.corp.example",
    "auth_method": "personal_access_token",
    "personal_access_token": "pat-1",
    "story_points_field": "customfield_10002",
}


def _data_center_issue(key: str) -> dict[str, object]:
    payload = _issue_payload(key)
    fields = dict(payload["fields"])  # type: ignore[call-overload]
    fields.update(
        {
            "assignee": {"name": "asha", "key": "JIRAUSER1", "displayName": "Asha"},
            "duedate": "2026-11-14",
            "fixVersions": [
                {"name": "R1", "releaseDate": "2026-11-01"},
                {"name": "R2", "releaseDate": "2026-11-20"},
            ],
            "labels": ["checkout", "payments"],
            "priority": {"name": "High"},
            "created": "2026-01-02T08:00:00.000+0000",
            "resolutiondate": None,
            "customfield_10002": 5,
        }
    )
    return {**payload, "fields": fields}


@respx.mock
async def test_data_center_searches_by_offset_on_the_v2_api_with_a_bearer_token() -> None:
    adapter = JiraIssueTrackerAdapter(connections=_Connection(_DATA_CENTER))
    search = respx.get("https://jira.corp.example/rest/api/2/search").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "startAt": 0,
                    "total": 3,
                    "issues": [_data_center_issue("PO-1"), _data_center_issue("PO-2")],
                },
            ),
            httpx.Response(
                200, json={"startAt": 2, "total": 3, "issues": [_data_center_issue("PO-3")]}
            ),
        ]
    )

    issues = await adapter.list_issues_for_query("demo", 'project = "PO"', SyncCursor())

    assert [issue.key for issue in issues] == ["PO-1", "PO-2", "PO-3"]
    first, second = search.calls
    assert first.request.headers["authorization"] == "Bearer pat-1"
    assert first.request.url.params["startAt"] == "0"
    assert second.request.url.params["startAt"] == "2"
    assert first.request.url.params["fields"].endswith(",customfield_10002")
    assert issues[0].assignee == UserRef(tenant_id="demo", external_id="asha", display_name="Asha")


@respx.mock
async def test_the_extra_fields_land_in_the_issue_metadata() -> None:
    adapter = JiraIssueTrackerAdapter(connections=_Connection(_DATA_CENTER))
    respx.get("https://jira.corp.example/rest/api/2/issue/PO-1").mock(
        return_value=httpx.Response(200, json=_data_center_issue("PO-1"))
    )

    issue = await adapter.get_issue("demo", "PO-1")

    assert issue.metadata["due_date"] == "2026-11-14"
    assert issue.metadata["fix_versions"] == "R1, R2"
    assert issue.metadata["fix_version_release_date"] == "2026-11-20"
    assert issue.metadata["labels"] == "checkout, payments"
    assert issue.metadata["priority"] == "High"
    assert issue.metadata["story_points"] == 5.0
    assert issue.metadata["resolved_at"] is None
    assert str(issue.metadata["created_at"]).startswith("2026-01-02T08:00:00")


@respx.mock
async def test_data_center_lists_projects_comments_in_plain_text_and_finds_users_by_username() -> (
    None
):
    adapter = JiraIssueTrackerAdapter(
        connections=_Connection(
            {**_DATA_CENTER, "auth_method": "basic", "username": "svc", "password": "pw"}
        )
    )
    projects = respx.get("https://jira.corp.example/rest/api/2/project").mock(
        return_value=httpx.Response(200, json=[{"id": "1", "key": "PO", "name": "OpenProgram"}])
    )
    comment = respx.post("https://jira.corp.example/rest/api/2/issue/PO-1/comment").mock(
        return_value=httpx.Response(201, json={"id": "9"})
    )
    users = respx.get("https://jira.corp.example/rest/api/2/user/search").mock(
        return_value=httpx.Response(
            200, json=[{"name": "asha", "displayName": "Asha", "emailAddress": "asha@corp.example"}]
        )
    )

    listed = await adapter.list_projects("demo")
    await adapter.add_comment("demo", "PO-1", "shipped it")
    user = await adapter.find_user_by_email("demo", "asha@corp.example")

    assert [project.key for project in listed] == ["PO"]
    assert projects.calls.last.request.headers["authorization"].startswith("Basic ")
    assert json.loads(comment.calls.last.request.content) == {"body": "shipped it"}
    assert users.calls.last.request.url.params["username"] == "asha@corp.example"
    assert user is not None and user.external_id == "asha"


@respx.mock
async def test_an_admin_connection_wins_over_the_server_settings() -> None:
    adapter = JiraIssueTrackerAdapter(
        base_url="https://env-jira.test",
        email="env@example.com",
        api_token="env-token",
        connections=_Connection(
            {
                "deployment": "cloud",
                "base_url": "https://admin.atlassian.net",
                "auth_method": "api_token",
                "email": "admin@example.com",
                "api_token": "admin-token",
            }
        ),
    )
    route = respx.get("https://admin.atlassian.net/rest/api/3/issue/PO-1").mock(
        return_value=httpx.Response(200, json=_issue_payload("PO-1"))
    )

    await adapter.get_issue("demo", "PO-1")

    assert route.called


@respx.mock
async def test_without_an_admin_connection_the_server_settings_are_used() -> None:
    adapter = JiraIssueTrackerAdapter(
        base_url="https://env-jira.test",
        email="env@example.com",
        api_token="env-token",
        connections=_Connection(None),
    )
    route = respx.get("https://env-jira.test/rest/api/3/issue/PO-1").mock(
        return_value=httpx.Response(200, json=_issue_payload("PO-1"))
    )

    await adapter.get_issue("demo", "PO-1")

    assert route.called


async def test_no_connection_and_no_server_settings_is_unavailable() -> None:
    adapter = JiraIssueTrackerAdapter(connections=_Connection(None))

    with pytest.raises(ProviderUnavailable, match="not configured"):
        await adapter.get_issue("demo", "PO-1")


# --- Issue text for gates and questions ------------------------------------------------


def _adf(*content: dict[str, object]) -> dict[str, object]:
    return {"type": "doc", "version": 1, "content": list(content)}


def _text(value: str) -> dict[str, object]:
    return {"type": "text", "text": value}


@respx.mock
async def test_cloud_issue_text_flattens_adf_with_headings_lists_tasks_and_mentions() -> None:
    adapter = JiraIssueTrackerAdapter(
        base_url="https://jira.test", email="agent@example.com", api_token="token"
    )
    respx.get("https://jira.test/rest/api/3/issue/PO-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "PO-1",
                "fields": {
                    "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                    "updated": "2026-10-05T08:00:00.000+0000",
                    "description": _adf(
                        {
                            "type": "heading",
                            "attrs": {"level": 2},
                            "content": [_text("Acceptance criteria")],
                        },
                        {
                            "type": "bulletList",
                            "content": [
                                {
                                    "type": "listItem",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [_text("Page loads under 1s")],
                                        }
                                    ],
                                },
                            ],
                        },
                        {
                            "type": "taskList",
                            "content": [
                                {
                                    "type": "taskItem",
                                    "attrs": {"state": "DONE"},
                                    "content": [_text("Receipt sent once")],
                                },
                            ],
                        },
                    ),
                    "comment": {
                        "comments": [
                            {
                                "id": "10001",
                                "author": {"accountId": "acc-qa", "displayName": "Kim"},
                                "created": "2026-10-05T09:00:00.000+0000",
                                "body": _adf(
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {
                                                "type": "mention",
                                                "attrs": {"id": "acc-po", "text": "@Maria"},
                                            },
                                            _text(" which coupons count as test orders?"),
                                        ],
                                    }
                                ),
                            }
                        ]
                    },
                },
            },
        )
    )

    text = await adapter.get_issue_text("demo", "PO-1")

    assert (
        text.description == "## Acceptance criteria\n- Page loads under 1s\n[x] Receipt sent once"
    )
    (comment,) = text.comments
    assert comment.body == "@Maria which coupons count as test orders?"
    assert comment.mentions == (
        UserRef(tenant_id="demo", external_id="acc-po", display_name="Maria"),
    )
    assert comment.author is not None and comment.author.external_id == "acc-qa"
    assert text.state is IssueState.IN_PROGRESS


@respx.mock
async def test_data_center_issue_text_reads_wiki_markup_and_its_mentions() -> None:
    adapter = JiraIssueTrackerAdapter(connections=_Connection(_DATA_CENTER))
    respx.get("https://jira.corp.example/rest/api/2/issue/PO-2").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "PO-2",
                "fields": {
                    "status": {"name": "Done", "statusCategory": {"key": "done"}},
                    "description": (
                        "h3. Test cases\n# Pay with a saved card\n## With 3-D Secure\n* *Bold* note"
                    ),
                    "comment": {
                        "comments": [
                            {
                                "id": "7",
                                "author": {"name": "kim", "displayName": "Kim"},
                                "created": "2026-10-05T09:00:00.000+0000",
                                "body": "[~maria] is this in scope?",
                            }
                        ]
                    },
                },
            },
        )
    )

    text = await adapter.get_issue_text("demo", "PO-2")

    assert text.description == (
        "### Test cases\n1. Pay with a saved card\n  1. With 3-D Secure\n- *Bold* note"
    )
    assert text.comments[0].body == "@maria is this in scope?"
    assert text.comments[0].mentions[0].external_id == "maria"
    assert text.state is IssueState.DONE
