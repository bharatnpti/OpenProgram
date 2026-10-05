"""Connection tests: fixed sentences, what the system reported, and never an error text."""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx
import pytest
import respx

from config.settings import Settings
from infra.adapters.connections.testers import HttpConnectionTester, _CheckFailed

JIRA_CLOUD = {
    "deployment": "cloud",
    "base_url": "https://acme.atlassian.net",
    "auth_method": "api_token",
    "email": "bot@acme.example",
    "api_token": "secret-token-123",
}
JIRA_DC = {
    "deployment": "data_center",
    "base_url": "https://jira.corp.example",
    "auth_method": "personal_access_token",
    "personal_access_token": "pat-secret",
}


def _tester(**kwargs: object) -> HttpConnectionTester:
    settings = Settings(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        slack_api_base_url="https://slack.test/api",
    )
    return HttpConnectionTester(settings, **kwargs)  # type: ignore[arg-type]


def _jira_fields() -> list[dict[str, object]]:
    return [
        {"id": "summary", "name": "Summary", "schema": {"type": "string"}},
        {"id": "customfield_10016", "name": "Story point estimate", "schema": {"type": "number"}},
        {"id": "customfield_10050", "name": "Budget", "schema": {"type": "number"}},
        {"id": "customfield_10060", "name": "Team", "schema": {"type": "option"}},
    ]


@respx.mock
async def test_jira_cloud_reports_the_server_the_account_and_number_fields() -> None:
    respx.get("https://acme.atlassian.net/rest/api/3/serverInfo").mock(
        return_value=httpx.Response(
            200, json={"deploymentType": "Cloud", "serverTitle": "Acme Jira", "version": "1001.0"}
        )
    )
    myself = respx.get("https://acme.atlassian.net/rest/api/3/myself").mock(
        return_value=httpx.Response(200, json={"displayName": "OpenProgram bot"})
    )
    respx.get("https://acme.atlassian.net/rest/api/3/field").mock(
        return_value=httpx.Response(200, json=_jira_fields())
    )

    check = await _tester().test("demo", "jira", JIRA_CLOUD)

    assert check.ok is True
    assert check.message == "Connected to Jira."
    assert dict(check.details) == {
        "Server": "Acme Jira",
        "Version": "1001.0",
        "Signed in as": "OpenProgram bot",
    }
    assert [option.value for option in check.suggestions["story_points_field"]] == [
        "customfield_10016",
        "customfield_10050",
    ]
    assert myself.calls.last.request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_jira_says_when_the_chosen_type_does_not_match_the_server() -> None:
    respx.get("https://acme.atlassian.net/rest/api/3/serverInfo").mock(
        return_value=httpx.Response(200, json={"deploymentType": "DataCenter"})
    )

    check = await _tester().test("demo", "jira", JIRA_CLOUD)

    assert check.ok is False
    assert check.message == "This is Jira Data Center or Server: choose that as the Jira type."


@respx.mock
async def test_jira_data_center_with_a_refused_token() -> None:
    respx.get("https://jira.corp.example/rest/api/2/serverInfo").mock(
        return_value=httpx.Response(200, json={"deploymentType": "DataCenter"})
    )
    respx.get("https://jira.corp.example/rest/api/2/myself").mock(
        return_value=httpx.Response(401, text="Unauthorized for token pat-secret")
    )

    check = await _tester().test("demo", "jira", JIRA_DC)

    assert check.ok is False
    assert check.message == "Jira refused the personal access token."
    assert "pat-secret" not in check.message


@respx.mock
async def test_a_redirect_to_a_sign_in_page_is_a_refused_sign_in() -> None:
    respx.get("https://jira.corp.example/rest/api/2/serverInfo").mock(
        return_value=httpx.Response(302, headers={"location": "https://sso.corp.example/login"})
    )

    check = await _tester().test("demo", "jira", JIRA_DC)

    assert check.ok is False
    assert "redirected the request" in check.message


@respx.mock
async def test_a_chosen_story_points_field_that_does_not_exist_fails() -> None:
    respx.get("https://acme.atlassian.net/rest/api/3/serverInfo").mock(
        return_value=httpx.Response(200, json={"deploymentType": "Cloud"})
    )
    respx.get("https://acme.atlassian.net/rest/api/3/myself").mock(
        return_value=httpx.Response(200, json={"displayName": "bot"})
    )
    respx.get("https://acme.atlassian.net/rest/api/3/field").mock(
        return_value=httpx.Response(200, json=_jira_fields())
    )

    check = await _tester().test(
        "demo", "jira", {**JIRA_CLOUD, "story_points_field": "customfield_10060"}
    )

    assert check.ok is False
    assert check.message == "Signed in, but Jira has no number field customfield_10060."


@respx.mock
async def test_an_unreachable_host_is_named_without_the_error_text() -> None:
    respx.get("https://jira.corp.example/rest/api/2/serverInfo").mock(
        side_effect=httpx.ConnectError("[Errno 8] nodename nor servname provided: pat-secret")
    )

    check = await _tester().test("demo", "jira", JIRA_DC)

    assert check.message == "Could not reach Jira at jira.corp.example."


@respx.mock
async def test_gitlab_checks_the_token_and_the_group() -> None:
    user = respx.get("https://gitlab.example.com/api/v4/user").mock(
        return_value=httpx.Response(200, json={"username": "openprogram-bot"})
    )
    respx.get("https://gitlab.example.com/api/v4/groups/acme%2Fdelivery").mock(
        return_value=httpx.Response(404)
    )

    check = await _tester().test(
        "demo",
        "gitlab",
        {
            "base_url": "https://gitlab.example.com",
            "token": "glpat",
            "namespace_id": "acme/delivery",
        },
    )

    assert user.calls.last.request.headers["private-token"] == "glpat"
    assert check.ok is False
    assert check.message == "GitLab has no group acme/delivery this token can read."


@respx.mock
async def test_github_reports_the_signed_in_account() -> None:
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(200, json={"login": "op-bot"})
    )

    check = await _tester().test(
        "demo", "github", {"base_url": "https://api.github.com", "token": "ghp"}
    )

    assert check.ok is True
    assert dict(check.details) == {"Signed in as": "op-bot"}


@respx.mock
async def test_slack_checks_the_bot_token_and_the_app_token() -> None:
    respx.post("https://slack.test/api/auth.test").mock(
        return_value=httpx.Response(200, json={"ok": True, "team": "Acme", "user": "openprogram"})
    )
    socket = respx.post("https://slack.test/api/apps.connections.open").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    )

    check = await _tester().test("demo", "slack", {"bot_token": "xoxb-1", "app_token": "xapp-bad"})

    assert socket.calls.last.request.headers["authorization"] == "Bearer xapp-bad"
    assert check.ok is False
    assert check.message == "Slack refused the app-level token."


@respx.mock
async def test_teams_posts_one_test_card() -> None:
    hook = respx.post("https://acme.webhook.office.com/workflows/abc").mock(
        return_value=httpx.Response(202)
    )

    check = await _tester().test(
        "demo", "teams", {"webhook_url": "https://acme.webhook.office.com/workflows/abc"}
    )

    assert check.ok is True
    body = json.loads(hook.calls.last.request.content)
    card = body["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["body"][0]["text"] == "OpenProgram connection test"


async def test_teams_refuses_a_plain_http_webhook() -> None:
    check = await _tester().test("demo", "teams", {"webhook_url": "http://hook.example"})

    assert check.ok is False
    assert check.message == "The webhook URL must be an https address."


async def test_email_signs_in_without_sending() -> None:
    seen: list[Mapping[str, str]] = []

    def _check(values: Mapping[str, str]) -> None:
        seen.append(values)

    values = {"host": "smtp.example.com", "port": "587", "from_address": "op@example.com"}
    check = await _tester(smtp_check=_check).test("demo", "email", values)

    assert seen == [values]
    assert check.ok is True
    assert check.message == "Connected to the mail server. No email was sent."


async def test_email_reports_a_refused_password() -> None:
    def _check(values: Mapping[str, str]) -> None:
        raise _CheckFailed("The mail server refused the user name or password.")

    check = await _tester(smtp_check=_check).test(
        "demo", "email", {"host": "smtp.example.com", "port": "587"}
    )

    assert check.ok is False
    assert check.message == "The mail server refused the user name or password."


@pytest.mark.parametrize("connector", ["jenkins", "confluence"])
async def test_an_unknown_connector_cannot_be_tested(connector: str) -> None:
    check = await _tester().test("demo", connector, {})

    assert check.ok is False
