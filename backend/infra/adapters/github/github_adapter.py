from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from urllib.parse import quote

import httpx
from opentelemetry import trace

from core.domain.errors import ProviderUnavailable, SecretNotFound
from core.domain.graph import JsonScalar
from core.domain.integrations import Commit, PullRequest, Repo, SyncCursor, UserRef
from core.ports.secrets import SecretRef, SecretStore

_tracer = trace.get_tracer("pulseops.adapters.vcs.github")


@dataclass(frozen=True)
class GitHubCredentials:
    base_url: str
    token: str | None
    owner: str | None


@dataclass(frozen=True)
class GitHubVcsAdapter:
    base_url: str = "https://api.github.com"
    token: str | None = None
    owner: str | None = None
    secret_store: SecretStore | None = None
    timeout_seconds: float = 10.0

    async def list_repos(self, tenant_id: str) -> list[Repo]:
        with _tracer.start_as_current_span("github.list_repos"):
            credentials = await self._credentials(tenant_id)
            path = (
                f"/orgs/{quote(credentials.owner, safe='')}/repos"
                if credentials.owner
                else "/user/repos"
            )
            payload = await self._get_json(
                credentials,
                path,
                params={"per_page": "100"},
            )
            return [_map_repo(tenant_id, item) for item in _list_payload(payload)]

    async def list_commits(self, tenant_id: str, repo: str, cursor: SyncCursor) -> list[Commit]:
        with _tracer.start_as_current_span("github.list_commits"):
            credentials = await self._credentials(tenant_id)
            owner, name = _repo_parts(repo, credentials.owner)
            params = {"per_page": "100"}
            if cursor.updated_at is not None:
                params["since"] = cursor.updated_at.isoformat()
            payload = await self._get_json(
                credentials,
                f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/commits",
                params=params,
            )
            return [_map_commit(tenant_id, repo, item) for item in _list_payload(payload)]

    async def list_pull_requests(
        self, tenant_id: str, repo: str, cursor: SyncCursor | None = None
    ) -> list[PullRequest]:
        with _tracer.start_as_current_span("github.list_pull_requests"):
            credentials = await self._credentials(tenant_id)
            owner, name = _repo_parts(repo, credentials.owner)
            payload = await self._get_json(
                credentials,
                f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/pulls",
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": "100",
                },
            )
            pull_requests = [
                _map_pull_request(tenant_id, repo, item) for item in _list_payload(payload)
            ]
            if cursor is None:
                return pull_requests
            return [
                pull_request
                for pull_request in pull_requests
                if _after_cursor(pull_request.updated_at, cursor)
            ]

    async def list_pull_requests_for(self, author: UserRef) -> list[PullRequest]:
        with _tracer.start_as_current_span("github.list_pull_requests_for"):
            credentials = await self._credentials(author.tenant_id)
            query = f"type:pr author:{author.external_id}"
            if credentials.owner:
                query = f"{query} org:{credentials.owner}"
            payload = await self._get_json(
                credentials,
                "/search/issues",
                params={"q": query, "per_page": "100"},
            )
            items = _items_object(payload)
            return [_map_search_pull_request(author.tenant_id, item) for item in items]

    async def _get_json(
        self,
        credentials: GitHubCredentials,
        path: str,
        *,
        params: Mapping[str, str],
    ) -> object:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if credentials.token:
            headers["Authorization"] = f"Bearer {credentials.token}"
        try:
            async with httpx.AsyncClient(
                base_url=credentials.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.get(path, headers=headers, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("VCS request failed") from exc

    async def _credentials(self, tenant_id: str) -> GitHubCredentials:
        return GitHubCredentials(
            base_url=(self.base_url or await self._secret(tenant_id, "base_url") or "").rstrip("/"),
            token=self.token or await self._secret(tenant_id, "token"),
            owner=self.owner or await self._secret(tenant_id, "owner"),
        )

    async def _secret(self, tenant_id: str, key: str) -> str | None:
        if self.secret_store is None:
            return None
        try:
            return await self.secret_store.get(
                SecretRef(tenant_id=tenant_id, connector="github", key=key)
            )
        except SecretNotFound:
            return None


def _map_repo(tenant_id: str, payload: Mapping[str, object]) -> Repo:
    owner = _optional_mapping(payload, "owner")
    return Repo(
        tenant_id=tenant_id,
        id=_string_field(payload, "id"),
        name=_optional_string(payload, "full_name") or _string_field(payload, "name"),
        default_branch=_optional_string(payload, "default_branch"),
        metadata=_metadata(
            {
                "owner": owner.get("login") if owner else None,
                "private": payload.get("private"),
                "archived": payload.get("archived"),
            }
        ),
    )


def _map_commit(tenant_id: str, repo: str, payload: Mapping[str, object]) -> Commit:
    commit = _mapping_field(payload, "commit")
    commit_author = _optional_mapping(commit, "author")
    account = _optional_mapping(payload, "author")
    return Commit(
        tenant_id=tenant_id,
        repo=repo,
        sha=_string_field(payload, "sha"),
        message=_optional_string(commit, "message") or "",
        author=_map_user(tenant_id, account, commit_author),
        committed_at=_datetime_from_mapping(commit_author, "date"),
        metadata=_metadata({"tree_sha": _tree_sha(commit)}),
    )


def _map_pull_request(tenant_id: str, repo: str, payload: Mapping[str, object]) -> PullRequest:
    author = _map_user(tenant_id, _optional_mapping(payload, "user"), None)
    return PullRequest(
        tenant_id=tenant_id,
        id=_number_or_id(payload),
        title=_optional_string(payload, "title") or "",
        author=author or UserRef(tenant_id=tenant_id, external_id="unknown"),
        merged=_optional_string(payload, "merged_at") is not None,
        updated_at=_datetime_field(payload, "updated_at"),
        metadata=_metadata(
            {
                "repo": repo,
                "state": payload.get("state"),
                "draft": payload.get("draft"),
            }
        ),
    )


def _map_search_pull_request(tenant_id: str, payload: Mapping[str, object]) -> PullRequest:
    repo = _repo_from_url(_optional_string(payload, "repository_url"))
    author = _map_user(tenant_id, _optional_mapping(payload, "user"), None)
    pull_request = _optional_mapping(payload, "pull_request")
    return PullRequest(
        tenant_id=tenant_id,
        id=_number_or_id(payload),
        title=_optional_string(payload, "title") or "",
        author=author or UserRef(tenant_id=tenant_id, external_id="unknown"),
        merged=_optional_string(pull_request or {}, "merged_at") is not None,
        updated_at=_datetime_field(payload, "updated_at"),
        metadata=_metadata(
            {
                "repo": repo,
                "state": payload.get("state"),
            }
        ),
    )


def _map_user(
    tenant_id: str,
    account: Mapping[str, object] | None,
    commit_author: Mapping[str, object] | None,
) -> UserRef | None:
    if account is not None:
        external_id = _optional_string(account, "login") or _optional_string(account, "id")
        if external_id is not None:
            return UserRef(
                tenant_id=tenant_id,
                external_id=external_id,
                display_name=_optional_string(account, "login"),
            )
    if commit_author is None:
        return None
    external_id = (
        _optional_string(commit_author, "email")
        or _optional_string(commit_author, "name")
        or "unknown"
    )
    return UserRef(
        tenant_id=tenant_id,
        external_id=external_id,
        display_name=_optional_string(commit_author, "name"),
    )


def _repo_parts(repo: str, configured_owner: str | None) -> tuple[str, str]:
    if "/" in repo:
        owner, name = repo.split("/", maxsplit=1)
        return owner, name
    if configured_owner:
        return configured_owner, repo
    raise ProviderUnavailable("VCS repository must include owner or configured owner")


def _list_payload(payload: object) -> list[Mapping[str, object]]:
    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
        raise ProviderUnavailable("VCS response was not a list")
    return [cast(Mapping[str, object], item) for item in payload if isinstance(item, Mapping)]


def _items_object(payload: object) -> list[Mapping[str, object]]:
    if not isinstance(payload, Mapping):
        raise ProviderUnavailable("VCS search response was not an object")
    items = payload.get("items")
    if not isinstance(items, Sequence) or isinstance(items, str | bytes):
        return []
    return [cast(Mapping[str, object], item) for item in items if isinstance(item, Mapping)]


def _mapping_field(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    raise ProviderUnavailable(f"VCS payload missing object field {key}")


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


def _number_or_id(payload: Mapping[str, object]) -> str:
    return _optional_string(payload, "number") or _string_field(payload, "id")


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int):
        return str(value)
    return None


def _datetime_from_mapping(payload: Mapping[str, object] | None, key: str) -> datetime:
    if payload is None:
        raise ProviderUnavailable("VCS commit payload missing author timestamp")
    parsed = _datetime_field(payload, key)
    if parsed is None:
        raise ProviderUnavailable("VCS commit payload missing author timestamp")
    return parsed


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


def _tree_sha(commit: Mapping[str, object]) -> str | None:
    tree = _optional_mapping(commit, "tree")
    return _optional_string(tree, "sha") if tree else None


def _repo_from_url(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.rstrip("/").split("/")
    if len(parts) < 2:
        return None
    return "/".join(parts[-2:])


def _metadata(values: Mapping[str, object]) -> Mapping[str, JsonScalar]:
    return {
        key: value
        for key, value in values.items()
        if value is None or isinstance(value, str | int | float | bool)
    }
