from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from core.domain.integrations import SyncCursor, UserRef
from infra.adapters.gitlab.gitlab_adapter import GitLabVcsAdapter


@respx.mock
async def test_gitlab_adapter_maps_recorded_rest_payloads() -> None:
    adapter = GitLabVcsAdapter(
        base_url="https://gitlab.test/api/v4",
        token="glpat-test",
        namespace_id="136978033",
    )
    respx.get("https://gitlab.test/api/v4/groups/136978033/projects").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 100,
                    "name": "repo",
                    "path_with_namespace": "openprogram/repo",
                    "default_branch": "main",
                    "visibility": "private",
                    "archived": False,
                    "namespace": {"full_path": "openprogram"},
                }
            ],
        )
    )
    respx.get("https://gitlab.test/api/v4/projects/openprogram%2Frepo/repository/commits").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "abc123",
                    "short_id": "abc123",
                    "title": "Add read adapter",
                    "message": "Add read adapter",
                    "author_name": "Asha",
                    "author_email": "asha@example.com",
                    "committed_date": "2026-01-10T08:30:00Z",
                }
            ],
        )
    )
    respx.get("https://gitlab.test/api/v4/projects/openprogram%2Frepo/merge_requests").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 9001,
                    "iid": 7,
                    "title": "Read sync",
                    "author": {"username": "asha", "name": "Asha"},
                    "state": "merged",
                    "created_at": "2026-01-10T08:45:00Z",
                    "merged_at": "2026-01-10T09:00:00Z",
                    "updated_at": "2026-01-10T09:30:00Z",
                    "draft": False,
                    "web_url": "https://gitlab.test/openprogram/repo/-/merge_requests/7",
                    "source_branch": "PO-7-read-sync",
                    "references": {"full": "openprogram/repo!7"},
                    "labels": ["type::feature", "backend"],
                }
            ],
        )
    )
    notes = respx.get(
        "https://gitlab.test/api/v4/projects/openprogram%2Frepo/merge_requests/7/notes"
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                # As GitLab 19 writes them: system notes for marks and approvals.
                {
                    "system": True,
                    "body": "marked this merge request as **ready**",
                    "author": {"username": "asha"},
                    "created_at": "2026-01-10T08:46:00Z",
                },
                {
                    "system": False,
                    "body": "One question on the cursor",
                    "author": {"username": "noah"},
                    "created_at": "2026-01-10T08:50:00Z",
                },
                {
                    "system": True,
                    "body": "added 1 commit\n\n<ul><li>abc123 - Fix</li></ul>",
                    "author": {"username": "asha"},
                    "created_at": "2026-01-10T08:51:00Z",
                },
                {
                    "system": True,
                    "body": "approved this merge request",
                    "author": {"username": "noah"},
                    "created_at": "2026-01-10T08:55:00Z",
                },
                {
                    "system": True,
                    "body": "unapproved this merge request",
                    "author": {"username": "liam"},
                    "created_at": "2026-01-10T08:56:00Z",
                },
            ],
        )
    )
    respx.get(
        "https://gitlab.test/api/v4/projects/openprogram%2Frepo/merge_requests/7/commits"
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "abc123",
                    "authored_date": "2026-01-10T08:20:00Z",
                    "committed_date": "2026-01-10T08:40:00Z",
                }
            ],
        )
    )
    respx.get("https://gitlab.test/api/v4/groups/136978033/merge_requests").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 9002,
                    "iid": 8,
                    "title": "Follow-up",
                    "author": {"username": "asha", "name": "Asha"},
                    "state": "opened",
                    "created_at": "2026-01-10T09:45:00Z",
                    "updated_at": "2026-01-10T10:00:00Z",
                    "web_url": "https://gitlab.test/openprogram/repo/-/merge_requests/8",
                    "references": {"full": "openprogram/repo!8"},
                }
            ],
        )
    )

    repos = await adapter.list_repos("demo")
    commits = await adapter.list_commits(
        "demo",
        "openprogram/repo",
        SyncCursor(updated_at=datetime(2026, 1, 1, tzinfo=UTC)),
    )
    pull_requests = await adapter.list_pull_requests("demo", "openprogram/repo", SyncCursor())
    authored = await adapter.list_pull_requests_for(UserRef(tenant_id="demo", external_id="asha"))

    assert repos[0].name == "openprogram/repo"
    assert repos[0].default_branch == "main"
    assert commits[0].sha == "abc123"
    assert commits[0].author is not None
    assert commits[0].author.external_id == "asha@example.com"
    assert pull_requests[0].id == "7"
    assert pull_requests[0].merged is True
    assert pull_requests[0].opened_at == datetime(2026, 1, 10, 8, 45, tzinfo=UTC)
    assert pull_requests[0].metadata["repo"] == "openprogram/repo"
    assert pull_requests[0].metadata["source_branch"] == "PO-7-read-sync"
    assert pull_requests[0].metadata["web_url"] == (
        "https://gitlab.test/openprogram/repo/-/merge_requests/7"
    )
    assert pull_requests[0].merged_at == datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    assert pull_requests[0].labels == ("type::feature", "backend")
    # A commit is dated by its author, not by a rebase; "added 1 commit" is not review.
    assert [(e.kind.value, e.actor, e.at.minute) for e in pull_requests[0].events or ()] == [
        ("commit", None, 20),
        ("ready", "asha", 46),
        ("comment", "noah", 50),
        ("approval", "noah", 55),
        ("unapproval", "liam", 56),
    ]
    assert notes.calls[0].request.url.params["sort"] == "asc"
    assert authored[0].metadata["repo"] == "openprogram/repo"
    assert authored[0].events is None
    assert {call.request.method for call in respx.calls} == {"GET"}


@respx.mock
async def test_gitlab_adapter_keeps_a_request_whose_history_fails() -> None:
    adapter = GitLabVcsAdapter(base_url="https://gitlab.test/api/v4", token="glpat-test")
    respx.get("https://gitlab.test/api/v4/projects/openprogram%2Frepo/merge_requests").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "iid": 7,
                    "title": "Read sync",
                    "author": {"username": "asha"},
                    "state": "opened",
                    "created_at": "2026-01-10T08:45:00Z",
                    "updated_at": "2026-01-10T09:30:00Z",
                }
            ],
        )
    )
    respx.get("https://gitlab.test/api/v4/projects/openprogram%2Frepo/merge_requests/7/notes").mock(
        return_value=httpx.Response(403)
    )

    pull_requests = await adapter.list_pull_requests("demo", "openprogram/repo")

    assert [(pr.id, pr.events) for pr in pull_requests] == [("7", None)]


@respx.mock
async def test_gitlab_adapter_reads_every_page_of_a_long_history() -> None:
    adapter = GitLabVcsAdapter(base_url="https://gitlab.test/api/v4", token="glpat-test")
    respx.get("https://gitlab.test/api/v4/projects/openprogram%2Frepo/merge_requests").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "iid": 7,
                    "title": "Busy request",
                    "author": {"username": "asha"},
                    "state": "opened",
                    "created_at": "2026-01-10T08:45:00Z",
                    "updated_at": "2026-01-10T09:30:00Z",
                }
            ],
        )
    )
    note = {
        "system": False,
        "body": "nit",
        "author": {"username": "noah"},
        "created_at": "2026-01-10T08:50:00Z",
    }
    route = respx.get(
        "https://gitlab.test/api/v4/projects/openprogram%2Frepo/merge_requests/7/notes"
    )
    route.side_effect = [
        httpx.Response(200, json=[note] * 100),
        httpx.Response(200, json=[note] * 3),
    ]
    respx.get(
        "https://gitlab.test/api/v4/projects/openprogram%2Frepo/merge_requests/7/commits"
    ).mock(return_value=httpx.Response(200, json=[]))

    pull_requests = await adapter.list_pull_requests("demo", "openprogram/repo")

    assert len(pull_requests[0].events or ()) == 103
    assert [call.request.url.params["page"] for call in route.calls] == ["1", "2"]
