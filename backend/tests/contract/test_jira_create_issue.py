"""Creating one issue in Jira: Cloud's ADF and Data Center's text, unassigned, in fixed failures."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from core.domain.connections import ConnectionValues
from core.domain.errors import IssueCreateFailed
from core.domain.integrations import NewIssue
from infra.adapters.jira.jira_adapter import JiraIssueTrackerAdapter

ISSUE = NewIssue(
    project_key="CHK",
    issue_type="Task",
    summary="Security review for Release 1",
    description="Release 1 needs a security review.\n\nWhat counts as done: a review.\nSigned.",
    labels=("security-review", "release-readiness", "op-rr-1a2b3c4d"),
    reporter_account_id="acct-mina",
)


def _cloud() -> JiraIssueTrackerAdapter:
    return JiraIssueTrackerAdapter(
        base_url="https://jira.test", email="agent@example.com", api_token="token"
    )


class _DataCenter:
    async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
        del tenant_id
        return ConnectionValues(
            connector="jira",
            values={
                "deployment": "data_center",
                "base_url": "https://jira.corp.example",
                "auth_method": "personal_access_token",
                "personal_access_token": "pat-1",
            },
        )


@respx.mock
async def test_cloud_creates_an_unassigned_issue_with_an_adf_description_and_the_reporter() -> (
    None
):
    route = respx.post("https://jira.test/rest/api/3/issue").mock(
        return_value=httpx.Response(201, json={"id": "10100", "key": "CHK-34", "self": "x"})
    )

    key = await _cloud().create_issue("demo", ISSUE)

    assert key == "CHK-34"
    fields = json.loads(route.calls.last.request.content)["fields"]
    assert fields["project"] == {"key": "CHK"}
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["labels"] == ["security-review", "release-readiness", "op-rr-1a2b3c4d"]
    assert fields["reporter"] == {"accountId": "acct-mina"}
    assert "assignee" not in fields
    description = fields["description"]
    assert description["type"] == "doc"
    paragraphs = description["content"]
    assert [item["type"] for item in paragraphs] == ["paragraph", "paragraph"]
    assert paragraphs[1]["content"][1] == {"type": "hardBreak"}


@respx.mock
async def test_data_center_creates_with_plain_text_and_a_reporter_by_name() -> None:
    route = respx.post("https://jira.corp.example/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"id": "1", "key": "CHK-35"})
    )

    key = await JiraIssueTrackerAdapter(connections=_DataCenter()).create_issue("demo", ISSUE)

    assert key == "CHK-35"
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer pat-1"
    fields = json.loads(request.content)["fields"]
    assert fields["description"] == ISSUE.description
    assert fields["reporter"] == {"name": "acct-mina"}


@respx.mock
async def test_a_refused_reporter_is_dropped_once_and_the_integration_posts() -> None:
    route = respx.post("https://jira.test/rest/api/3/issue").mock(
        side_effect=[
            httpx.Response(400, json={"errors": {"reporter": "cannot be set"}}),
            httpx.Response(201, json={"id": "1", "key": "CHK-36"}),
        ]
    )

    key = await _cloud().create_issue("demo", ISSUE)

    assert key == "CHK-36"
    first, second = (json.loads(call.request.content)["fields"] for call in route.calls)
    assert "reporter" in first and "reporter" not in second


@respx.mock
@pytest.mark.parametrize(
    ("response", "category", "fields"),
    [
        (
            httpx.Response(
                400,
                json={
                    "errorMessages": ["Jira's own words, never shown"],
                    "errors": {"components": "Component is required."},
                },
            ),
            "refused",
            ("components",),
        ),
        (httpx.Response(401, json={}), "credentials", ()),
        (httpx.Response(403, json={}), "credentials", ()),
        (httpx.Response(503, text="down"), "unreachable", ()),
        (httpx.Response(201, json={"id": "1"}), "unreachable", ()),
    ],
)
async def test_failures_are_a_fixed_category_with_field_ids_only(
    response: httpx.Response, category: str, fields: tuple[str, ...]
) -> None:
    respx.post("https://jira.test/rest/api/3/issue").mock(return_value=response)

    with pytest.raises(IssueCreateFailed) as failed:
        await _cloud().create_issue("demo", ISSUE)

    assert (failed.value.category, failed.value.fields) == (category, fields)
    assert "Jira's own words" not in str(failed.value)


@respx.mock
async def test_a_network_failure_is_unreachable_and_missing_credentials_are_credentials() -> None:
    respx.post("https://jira.test/rest/api/3/issue").mock(side_effect=httpx.ConnectError("x"))

    with pytest.raises(IssueCreateFailed) as unreachable:
        await _cloud().create_issue("demo", ISSUE)
    with pytest.raises(IssueCreateFailed) as unconfigured:
        await JiraIssueTrackerAdapter().create_issue("demo", ISSUE)

    assert unreachable.value.category == "unreachable"
    assert unconfigured.value.category == "credentials"
