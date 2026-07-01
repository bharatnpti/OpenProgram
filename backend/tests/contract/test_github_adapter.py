from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from core.domain.integrations import SyncCursor, UserRef
from infra.adapters.github.github_adapter import GitHubVcsAdapter


@respx.mock
async def test_github_adapter_maps_recorded_rest_payloads() -> None:
    adapter = GitHubVcsAdapter(
        base_url="https://github.test",
        token="ghp-test",
        owner="acme",
    )
    respx.get("https://github.test/orgs/acme/repos").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 100,
                    "name": "repo",
                    "full_name": "acme/repo",
                    "default_branch": "main",
                    "private": True,
                    "archived": False,
                    "owner": {"login": "acme"},
                }
            ],
        )
    )
    respx.get("https://github.test/repos/acme/repo/commits").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "sha": "abc123",
                    "author": {"login": "asha", "id": 42},
                    "commit": {
                        "message": "Add read adapter",
                        "author": {
                            "name": "Asha",
                            "email": "asha@example.com",
                            "date": "2026-01-10T08:30:00Z",
                        },
                        "tree": {"sha": "tree123"},
                    },
                }
            ],
        )
    )
    respx.get("https://github.test/repos/acme/repo/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 9001,
                    "number": 7,
                    "title": "Read sync",
                    "user": {"login": "asha"},
                    "state": "closed",
                    "created_at": "2026-01-10T08:45:00Z",
                    "merged_at": "2026-01-10T09:00:00Z",
                    "updated_at": "2026-01-10T09:30:00Z",
                    "draft": False,
                }
            ],
        )
    )
    respx.get("https://github.test/search/issues").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": 9002,
                        "number": 8,
                        "title": "Follow-up",
                        "user": {"login": "asha"},
                        "state": "open",
                        "repository_url": "https://api.github.com/repos/acme/repo",
                        "updated_at": "2026-01-10T10:00:00Z",
                        "pull_request": {},
                    }
                ]
            },
        )
    )

    repos = await adapter.list_repos("demo")
    commits = await adapter.list_commits(
        "demo",
        "repo",
        SyncCursor(updated_at=datetime(2026, 1, 1, tzinfo=UTC)),
    )
    pull_requests = await adapter.list_pull_requests("demo", "repo", SyncCursor())
    authored = await adapter.list_pull_requests_for(UserRef(tenant_id="demo", external_id="asha"))

    assert repos[0].name == "acme/repo"
    assert repos[0].default_branch == "main"
    assert commits[0].sha == "abc123"
    assert commits[0].author is not None
    assert commits[0].author.external_id == "asha"
    assert pull_requests[0].id == "7"
    assert pull_requests[0].merged is True
    assert pull_requests[0].opened_at == datetime(2026, 1, 10, 8, 45, tzinfo=UTC)
    assert pull_requests[0].metadata["repo"] == "repo"
    assert authored[0].metadata["repo"] == "acme/repo"
    assert {call.request.method for call in respx.calls} == {"GET"}
