from __future__ import annotations

from core.application.proof_links import jira_issue_evidence, pull_request_evidence


def test_jira_issue_evidence_constructs_url_from_base() -> None:
    evidence = jira_issue_evidence(key="PROJ-412", jira_base_url="https://acme.atlassian.net")

    assert evidence.identifier == "PROJ-412"
    assert evidence.url == "https://acme.atlassian.net/browse/PROJ-412"
    assert evidence.url_is_user_supplied is False


def test_jira_issue_evidence_prefers_user_supplied_link() -> None:
    evidence = jira_issue_evidence(
        key="PROJ-412",
        jira_base_url="https://acme.atlassian.net",
        metadata={"jira_url": "https://acme.atlassian.net/browse/PROJ-412-custom"},
    )

    assert evidence.url == "https://acme.atlassian.net/browse/PROJ-412-custom"
    assert evidence.url_is_user_supplied is True


def test_jira_issue_evidence_falls_back_to_raw_identifier_without_base_url() -> None:
    evidence = jira_issue_evidence(key="PROJ-412", jira_base_url=None)

    assert evidence.identifier == "PROJ-412"
    assert evidence.url is None
    assert evidence.url_is_user_supplied is False


def test_jira_issue_evidence_ignores_malformed_user_supplied_link() -> None:
    evidence = jira_issue_evidence(
        key="PROJ-412",
        jira_base_url="https://acme.atlassian.net",
        metadata={"jira_url": "not-a-url"},
    )

    assert evidence.url == "https://acme.atlassian.net/browse/PROJ-412"
    assert evidence.url_is_user_supplied is False


def test_pull_request_evidence_maps_github_cloud_api_base_to_web_host() -> None:
    evidence = pull_request_evidence(
        repo="acme/api",
        pr_id="42",
        github_base_url="https://api.github.com",
    )

    assert evidence.identifier == "acme/api#42"
    assert evidence.url == "https://github.com/acme/api/pull/42"
    assert evidence.url_is_user_supplied is False


def test_pull_request_evidence_maps_github_enterprise_api_base_to_web_host() -> None:
    evidence = pull_request_evidence(
        repo="acme/api",
        pr_id="42",
        github_base_url="https://ghe.acme.internal/api/v3",
    )

    assert evidence.url == "https://ghe.acme.internal/acme/api/pull/42"


def test_pull_request_evidence_prefers_user_supplied_link() -> None:
    evidence = pull_request_evidence(
        repo="acme/api",
        pr_id="42",
        github_base_url="https://api.github.com",
        metadata={"pr_url": "https://github.com/acme/api/pull/42?tab=files"},
    )

    assert evidence.url == "https://github.com/acme/api/pull/42?tab=files"
    assert evidence.url_is_user_supplied is True
