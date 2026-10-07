from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast
from urllib.parse import quote

import httpx
from opentelemetry import trace

from core.domain.errors import ProviderUnavailable
from core.domain.graph import JsonScalar
from core.domain.integrations import (
    Commit,
    PullRequest,
    PullRequestEvent,
    PullRequestEventKind,
    Repo,
    SyncCursor,
    UserRef,
)
from core.ports.connections import ConnectionResolver

_tracer = trace.get_tracer("openprogram.adapters.vcs.gitlab")

# A merge request's history: at most this many requests read at once, and this
# many pages of 100 notes or commits each.
_HISTORY_CONCURRENCY = 4
_HISTORY_MAX_PAGES = 5
_PAGE_SIZE = 100

# GitLab records approvals and draft changes as system notes; the body is
# matched without its Markdown bold. "Work in progress" is the pre-14.0 wording.
# "unapproved" is checked before "approved", which it contains.
_SYSTEM_NOTE_EVENTS: tuple[tuple[str, PullRequestEventKind], ...] = (
    ("unapproved this merge request", PullRequestEventKind.UNAPPROVAL),
    ("approved this merge request", PullRequestEventKind.APPROVAL),
    ("marked this merge request as ready", PullRequestEventKind.READY),
    ("unmarked as a work in progress", PullRequestEventKind.READY),
    ("marked this merge request as draft", PullRequestEventKind.DRAFT),
    ("marked as a work in progress", PullRequestEventKind.DRAFT),
)


@dataclass(frozen=True)
class GitLabCredentials:
    base_url: str
    token: str | None
    namespace_id: str | None


@dataclass(frozen=True)
class GitLabVcsAdapter:
    base_url: str = "https://gitlab.com/api/v4"
    token: str | None = None
    namespace_id: str | None = None
    connections: ConnectionResolver | None = None
    timeout_seconds: float = 10.0

    async def list_repos(self, tenant_id: str) -> list[Repo]:
        with _tracer.start_as_current_span("gitlab.list_repos"):
            credentials = await self._credentials(tenant_id)
            path = (
                f"/groups/{quote(credentials.namespace_id, safe='')}/projects"
                if credentials.namespace_id
                else "/projects"
            )
            params = {"simple": "true", "per_page": "100"}
            if credentials.namespace_id is None:
                params["membership"] = "true"
            payload = await self._get_json(credentials, path, params=params)
            return [_map_repo(tenant_id, item) for item in _list_payload(payload)]

    async def list_commits(self, tenant_id: str, repo: str, cursor: SyncCursor) -> list[Commit]:
        with _tracer.start_as_current_span("gitlab.list_commits"):
            credentials = await self._credentials(tenant_id)
            params = {"per_page": "100"}
            if cursor.updated_at is not None:
                params["since"] = cursor.updated_at.isoformat()
            payload = await self._get_json(
                credentials,
                f"/projects/{_project_id(repo)}/repository/commits",
                params=params,
            )
            return [_map_commit(tenant_id, repo, item) for item in _list_payload(payload)]

    async def list_pull_requests(
        self, tenant_id: str, repo: str, cursor: SyncCursor | None = None
    ) -> list[PullRequest]:
        with _tracer.start_as_current_span("gitlab.list_pull_requests"):
            credentials = await self._credentials(tenant_id)
            params = {
                "state": "all",
                "order_by": "updated_at",
                "sort": "desc",
                "per_page": "100",
            }
            if cursor is not None and cursor.updated_at is not None:
                params["updated_after"] = cursor.updated_at.isoformat()
            payload = await self._get_json(
                credentials,
                f"/projects/{_project_id(repo)}/merge_requests",
                params=params,
            )
            merge_requests = [
                _map_merge_request(tenant_id, repo, item) for item in _list_payload(payload)
            ]
            if cursor is not None:
                merge_requests = [
                    merge_request
                    for merge_request in merge_requests
                    if _after_cursor(merge_request.updated_at, cursor)
                ]
            return await self._with_histories(credentials, repo, merge_requests)

    async def _with_histories(
        self,
        credentials: GitLabCredentials,
        repo: str,
        merge_requests: list[PullRequest],
    ) -> list[PullRequest]:
        """Each request with its commits, draft marks, notes and approvals.

        A request whose history cannot be read keeps ``events=None`` rather than
        failing the sync: its state, title and branch still count, and the flow
        view says how many requests it could not time.
        """
        if not merge_requests:
            return merge_requests
        limit = asyncio.Semaphore(_HISTORY_CONCURRENCY)
        async with httpx.AsyncClient(
            base_url=credentials.base_url, timeout=self.timeout_seconds
        ) as client:

            async def read(merge_request: PullRequest) -> PullRequest:
                async with limit:
                    try:
                        events = await self._merge_request_events(
                            client, credentials, repo, merge_request.id
                        )
                    except ProviderUnavailable:
                        return merge_request
                return replace(merge_request, events=events)

            with _tracer.start_as_current_span("gitlab.merge_request_histories"):
                return list(await asyncio.gather(*(read(item) for item in merge_requests)))

    async def _merge_request_events(
        self,
        client: httpx.AsyncClient,
        credentials: GitLabCredentials,
        repo: str,
        iid: str,
    ) -> tuple[PullRequestEvent, ...]:
        base = f"/projects/{_project_id(repo)}/merge_requests/{quote(iid, safe='')}"
        notes = await self._get_pages(
            client,
            credentials,
            f"{base}/notes",
            params={"sort": "asc", "order_by": "created_at"},
        )
        commits = await self._get_pages(client, credentials, f"{base}/commits", params={})
        events = [event for note in notes if (event := _note_event(note)) is not None]
        events.extend(event for commit in commits if (event := _commit_event(commit)) is not None)
        return tuple(sorted(events, key=lambda event: event.at))

    async def _get_pages(
        self,
        client: httpx.AsyncClient,
        credentials: GitLabCredentials,
        path: str,
        *,
        params: Mapping[str, str],
    ) -> list[Mapping[str, object]]:
        items: list[Mapping[str, object]] = []
        for page in range(1, _HISTORY_MAX_PAGES + 1):
            try:
                response = await client.get(
                    path,
                    headers=_headers(credentials),
                    params={**params, "per_page": str(_PAGE_SIZE), "page": str(page)},
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderUnavailable("VCS request failed") from exc
            batch = _list_payload(payload)
            items.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
        return items

    async def list_pull_requests_for(self, author: UserRef) -> list[PullRequest]:
        with _tracer.start_as_current_span("gitlab.list_pull_requests_for"):
            credentials = await self._credentials(author.tenant_id)
            path = (
                f"/groups/{quote(credentials.namespace_id, safe='')}/merge_requests"
                if credentials.namespace_id
                else "/merge_requests"
            )
            payload = await self._get_json(
                credentials,
                path,
                params={
                    "scope": "all",
                    "state": "all",
                    "author_username": author.external_id,
                    "per_page": "100",
                },
            )
            return [
                _map_merge_request(author.tenant_id, _repo_from_merge_request(item), item)
                for item in _list_payload(payload)
            ]

    async def _get_json(
        self,
        credentials: GitLabCredentials,
        path: str,
        *,
        params: Mapping[str, str],
    ) -> object:
        try:
            async with httpx.AsyncClient(
                base_url=credentials.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.get(path, headers=_headers(credentials), params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("VCS request failed") from exc

    async def _credentials(self, tenant_id: str) -> GitLabCredentials:
        # The tenant's connection set up in admin wins over the server's settings.
        if self.connections is not None:
            values = await self.connections.resolve(tenant_id, "gitlab")
            if values is not None:
                return GitLabCredentials(
                    base_url=gitlab_api_url(values.get("base_url") or "https://gitlab.com"),
                    token=values.get("token"),
                    namespace_id=values.get("namespace_id"),
                )
        return GitLabCredentials(
            base_url=(self.base_url or "").rstrip("/"),
            token=self.token,
            namespace_id=self.namespace_id,
        )


def _headers(credentials: GitLabCredentials) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if credentials.token:
        headers["PRIVATE-TOKEN"] = credentials.token
    return headers


def _note_event(payload: Mapping[str, object]) -> PullRequestEvent | None:
    """A note as review activity: a person's note, or a system note that approves or marks."""
    at = _datetime_field(payload, "created_at")
    if at is None:
        return None
    actor = _optional_string(_optional_mapping(payload, "author") or {}, "username")
    if payload.get("system") is not True:
        return PullRequestEvent(kind=PullRequestEventKind.COMMENT, at=at, actor=actor)
    body = (_optional_string(payload, "body") or "").replace("*", "").strip().casefold()
    for phrase, kind in _SYSTEM_NOTE_EVENTS:
        if body.startswith(phrase):
            return PullRequestEvent(kind=kind, at=at, actor=actor)
    return None


def _commit_event(payload: Mapping[str, object]) -> PullRequestEvent | None:
    # When the author wrote it: a rebase or a squash changes the commit date.
    at = (
        _datetime_field(payload, "authored_date")
        or _datetime_field(payload, "created_at")
        or _datetime_field(payload, "committed_date")
    )
    return PullRequestEvent(kind=PullRequestEventKind.COMMIT, at=at) if at is not None else None


def _labels(payload: Mapping[str, object]) -> tuple[str, ...]:
    value = payload.get("labels")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    names: list[str] = []
    for item in value:
        # Plain names by default; objects when asked for with_labels_details.
        name = item if isinstance(item, str) else None
        if isinstance(item, Mapping):
            name = _optional_string(cast(Mapping[str, object], item), "name")
        if name:
            names.append(name)
    return tuple(names)


def _map_repo(tenant_id: str, payload: Mapping[str, object]) -> Repo:
    namespace = _optional_mapping(payload, "namespace")
    return Repo(
        tenant_id=tenant_id,
        id=_string_field(payload, "id"),
        name=_optional_string(payload, "path_with_namespace") or _string_field(payload, "name"),
        default_branch=_optional_string(payload, "default_branch"),
        metadata=_metadata(
            {
                "namespace": _optional_string(namespace or {}, "full_path"),
                "visibility": payload.get("visibility"),
                "archived": payload.get("archived"),
            }
        ),
    )


def _map_commit(tenant_id: str, repo: str, payload: Mapping[str, object]) -> Commit:
    author = UserRef(
        tenant_id=tenant_id,
        external_id=(
            _optional_string(payload, "author_email")
            or _optional_string(payload, "author_name")
            or "unknown"
        ),
        display_name=_optional_string(payload, "author_name"),
    )
    return Commit(
        tenant_id=tenant_id,
        repo=repo,
        sha=_string_field(payload, "id"),
        message=_optional_string(payload, "message") or _optional_string(payload, "title") or "",
        author=author,
        committed_at=_required_datetime(payload, "committed_date", "created_at"),
        metadata=_metadata({"short_id": payload.get("short_id")}),
    )


def _map_merge_request(
    tenant_id: str,
    repo: str | None,
    payload: Mapping[str, object],
) -> PullRequest:
    author_payload = _optional_mapping(payload, "author")
    author = UserRef(
        tenant_id=tenant_id,
        external_id=_optional_string(author_payload or {}, "username") or "unknown",
        display_name=_optional_string(author_payload or {}, "name"),
    )
    return PullRequest(
        tenant_id=tenant_id,
        id=_optional_string(payload, "iid") or _string_field(payload, "id"),
        title=_optional_string(payload, "title") or "",
        author=author,
        merged=_optional_string(payload, "merged_at") is not None
        or payload.get("state") == "merged",
        updated_at=_datetime_field(payload, "updated_at"),
        opened_at=_datetime_field(payload, "created_at"),
        merged_at=_datetime_field(payload, "merged_at"),
        closed_at=_datetime_field(payload, "closed_at"),
        labels=_labels(payload),
        metadata=_metadata(
            {
                "repo": repo,
                "state": payload.get("state"),
                "draft": payload.get("draft") or payload.get("work_in_progress"),
                "web_url": payload.get("web_url"),
                "source_branch": payload.get("source_branch"),
            }
        ),
    )


def gitlab_api_url(base_url: str) -> str:
    """The REST API root for a GitLab address, with or without /api/v4 already on it."""
    trimmed = base_url.rstrip("/")
    return trimmed if trimmed.endswith("/api/v4") else f"{trimmed}/api/v4"


def _project_id(repo: str) -> str:
    return quote(repo, safe="")


def _repo_from_merge_request(payload: Mapping[str, object]) -> str | None:
    references = _optional_mapping(payload, "references")
    full_ref = _optional_string(references or {}, "full")
    if full_ref and "!" in full_ref:
        return full_ref.split("!", maxsplit=1)[0]
    web_url = _optional_string(payload, "web_url")
    if not web_url:
        return None
    marker = "/-/merge_requests/"
    if marker not in web_url:
        return None
    project_url = web_url.split(marker, maxsplit=1)[0].rstrip("/")
    parts = project_url.split("/")
    if len(parts) < 2:
        return None
    return "/".join(parts[-2:])


def _list_payload(payload: object) -> list[Mapping[str, object]]:
    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
        raise ProviderUnavailable("VCS response was not a list")
    return [cast(Mapping[str, object], item) for item in payload if isinstance(item, Mapping)]


def _optional_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    value = payload.get(key)
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return None


def _string_field(
    payload: Mapping[str, object],
    key: str,
    default: str | None = None,
) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int):
        return str(value)
    if default is not None:
        return default
    raise ProviderUnavailable(f"VCS payload missing string field {key}")


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int):
        return str(value)
    return None


def _required_datetime(payload: Mapping[str, object], *keys: str) -> datetime:
    for key in keys:
        parsed = _datetime_field(payload, key)
        if parsed is not None:
            return parsed
    raise ProviderUnavailable("VCS commit payload missing timestamp")


def _datetime_field(payload: Mapping[str, object], key: str) -> datetime | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _after_cursor(updated_at: datetime | None, cursor: SyncCursor) -> bool:
    return cursor.updated_at is None or updated_at is None or updated_at > cursor.updated_at


def _metadata(values: Mapping[str, object]) -> Mapping[str, JsonScalar]:
    return {
        key: value
        for key, value in values.items()
        if value is None or isinstance(value, str | int | float | bool)
    }
