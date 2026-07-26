from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

import httpx
from opentelemetry import trace

from core.domain.errors import ProviderUnavailable, SecretNotFound
from core.domain.graph import JsonScalar
from core.domain.integrations import Issue, IssueState, Project, Sprint, SyncCursor, UserRef
from core.ports.secrets import SecretRef, SecretStore

_tracer = trace.get_tracer("openprogram.adapters.issue_tracker.jira")
_ISSUE_FIELDS = "summary,status,assignee,updated,project,issuetype,parent"


@dataclass(frozen=True)
class JiraCredentials:
    base_url: str
    email: str | None
    api_token: str


@dataclass(frozen=True)
class JiraIssueTrackerAdapter:
    base_url: str | None = None
    email: str | None = None
    api_token: str | None = None
    secret_store: SecretStore | None = None
    timeout_seconds: float = 10.0

    async def list_projects(self, tenant_id: str) -> list[Project]:
        with _tracer.start_as_current_span("jira.list_projects"):
            payload = await self._get(tenant_id, "/rest/api/3/project/search")
            return [_map_project(tenant_id, item) for item in _items(payload, "values")]

    async def list_issues_updated_since(
        self, tenant_id: str, project_key: str, cursor: SyncCursor
    ) -> list[Issue]:
        with _tracer.start_as_current_span("jira.list_issues_updated_since"):
            jql = f"project = {_jql_string(project_key)}"
            return await self._search_issues(tenant_id, jql, cursor)

    async def list_issues_for_query(
        self, tenant_id: str, jql: str, cursor: SyncCursor
    ) -> list[Issue]:
        with _tracer.start_as_current_span("jira.list_issues_for_query"):
            return await self._search_issues(tenant_id, jql, cursor)

    async def list_sprints(self, tenant_id: str, board_id: str) -> list[Sprint]:
        with _tracer.start_as_current_span("jira.list_sprints"):
            payload = await self._get(
                tenant_id,
                f"/rest/agile/1.0/board/{board_id}/sprint",
                params={"maxResults": "100"},
            )
            return [_map_sprint(tenant_id, board_id, item) for item in _items(payload, "values")]

    async def get_issue(self, tenant_id: str, key: str) -> Issue:
        with _tracer.start_as_current_span("jira.get_issue"):
            payload = await self._get(
                tenant_id,
                f"/rest/api/3/issue/{key}",
                params={"fields": _ISSUE_FIELDS},
            )
            return _map_issue(tenant_id, payload)

    async def list_active_for(self, assignee: UserRef) -> list[Issue]:
        with _tracer.start_as_current_span("jira.list_active_for"):
            return await self._search_issues_for_jql(
                assignee.tenant_id,
                (
                    f"assignee = {_jql_string(assignee.external_id)} "
                    "AND statusCategory != Done ORDER BY updated DESC"
                ),
            )

    async def transition(self, tenant_id: str, key: str, to_state: str) -> None:
        with _tracer.start_as_current_span("jira.transition"):
            payload = await self._get(tenant_id, f"/rest/api/3/issue/{key}/transitions")
            transition_id = _resolve_transition_id(payload, to_state)
            if transition_id is None:
                raise ProviderUnavailable(f"issue tracker has no transition to state {to_state!r}")
            await self._post(
                tenant_id,
                f"/rest/api/3/issue/{key}/transitions",
                json={"transition": {"id": transition_id}},
            )

    async def add_comment(self, tenant_id: str, key: str, body: str) -> None:
        with _tracer.start_as_current_span("jira.add_comment"):
            await self._post(
                tenant_id,
                f"/rest/api/3/issue/{key}/comment",
                json={"body": _adf_document(body)},
            )

    async def _get(
        self,
        tenant_id: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        credentials = await self._credentials(tenant_id)
        headers = {"Accept": "application/json"}
        auth: httpx.Auth | None = None
        if credentials.email:
            auth = httpx.BasicAuth(credentials.email, credentials.api_token)
        else:
            headers["Authorization"] = f"Bearer {credentials.api_token}"

        try:
            async with httpx.AsyncClient(
                base_url=credentials.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.get(path, headers=headers, auth=auth, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("issue tracker request failed") from exc

        if not isinstance(payload, Mapping):
            raise ProviderUnavailable("issue tracker response was not an object")
        return cast(Mapping[str, object], payload)

    async def _post(
        self,
        tenant_id: str,
        path: str,
        *,
        json: Mapping[str, object],
    ) -> None:
        credentials = await self._credentials(tenant_id)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        auth: httpx.Auth | None = None
        if credentials.email:
            auth = httpx.BasicAuth(credentials.email, credentials.api_token)
        else:
            headers["Authorization"] = f"Bearer {credentials.api_token}"

        try:
            async with httpx.AsyncClient(
                base_url=credentials.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                if auth is None:
                    response = await client.post(path, headers=headers, json=json)
                else:
                    response = await client.post(path, headers=headers, auth=auth, json=json)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("issue tracker write failed") from exc

    async def _search_issues(
        self,
        tenant_id: str,
        jql: str,
        cursor: SyncCursor,
    ) -> list[Issue]:
        effective_jql = jql
        if cursor.updated_at is not None:
            effective_jql = (
                f"({effective_jql}) AND updated > {_jql_string(cursor.updated_at.isoformat())}"
            )
        return await self._search_issues_for_jql(
            tenant_id,
            f"{effective_jql} ORDER BY updated ASC",
        )

    async def _search_issues_for_jql(self, tenant_id: str, jql: str) -> list[Issue]:
        issues: list[Issue] = []
        next_page_token: str | None = None
        while True:
            params = {
                "jql": jql,
                "fields": _ISSUE_FIELDS,
                "maxResults": "100",
            }
            if next_page_token is not None:
                params["nextPageToken"] = next_page_token
            payload = await self._get(
                tenant_id,
                "/rest/api/3/search/jql",
                params=params,
            )
            issues.extend(_map_issue(tenant_id, item) for item in _items(payload, "issues"))
            next_page_token = _optional_string(payload, "nextPageToken")
            if next_page_token is None:
                return issues

    async def _credentials(self, tenant_id: str) -> JiraCredentials:
        base_url = self.base_url or await self._secret(tenant_id, "base_url")
        api_token = self.api_token or await self._secret(tenant_id, "api_token")
        email = self.email or await self._secret(tenant_id, "email")
        if not base_url or not api_token:
            raise ProviderUnavailable("issue tracker credentials are not configured")
        return JiraCredentials(
            base_url=base_url.rstrip("/"),
            email=email,
            api_token=api_token,
        )

    async def _secret(self, tenant_id: str, key: str) -> str | None:
        if self.secret_store is None:
            return None
        try:
            return await self.secret_store.get(
                SecretRef(tenant_id=tenant_id, connector="jira", key=key)
            )
        except SecretNotFound:
            return None


def _map_project(tenant_id: str, payload: Mapping[str, object]) -> Project:
    return Project(
        tenant_id=tenant_id,
        id=_string_field(payload, "id"),
        key=_string_field(payload, "key"),
        name=_string_field(payload, "name"),
        metadata=_metadata(
            {
                "project_type": payload.get("projectTypeKey"),
                "style": payload.get("style"),
            }
        ),
    )


def _map_sprint(tenant_id: str, board_id: str, payload: Mapping[str, object]) -> Sprint:
    return Sprint(
        tenant_id=tenant_id,
        id=_string_field(payload, "id"),
        board_id=board_id,
        name=_string_field(payload, "name"),
        state=_optional_string(payload, "state") or "unknown",
        starts_at=_datetime_field(payload, "startDate"),
        ends_at=_datetime_field(payload, "endDate"),
        metadata=_metadata({"goal": payload.get("goal")}),
    )


def _map_issue(tenant_id: str, payload: Mapping[str, object]) -> Issue:
    fields = _mapping_field(payload, "fields")
    status = _mapping_field(fields, "status")
    assignee_payload = _optional_mapping(fields, "assignee")
    project = _optional_mapping(fields, "project")
    issue_type = _optional_mapping(fields, "issuetype")
    parent = _optional_mapping(fields, "parent")
    return Issue(
        tenant_id=tenant_id,
        key=_string_field(payload, "key"),
        title=_optional_string(fields, "summary") or _string_field(payload, "key"),
        state=_issue_state(status),
        assignee=_map_user(tenant_id, assignee_payload) if assignee_payload else None,
        updated_at=_datetime_field(fields, "updated"),
        metadata=_metadata(
            {
                "project_key": project.get("key") if project else None,
                "project_id": project.get("id") if project else None,
                "status": status.get("name"),
                "status_category": _status_category(status),
                "issue_type": issue_type.get("name") if issue_type else None,
                "parent_key": parent.get("key") if parent else None,
                "container_id": parent.get("key") if parent else None,
            }
        ),
    )


def _map_user(tenant_id: str, payload: Mapping[str, object]) -> UserRef:
    external_id = (
        _optional_string(payload, "accountId")
        or _optional_string(payload, "name")
        or _optional_string(payload, "emailAddress")
    )
    if external_id is None:
        raise ProviderUnavailable("issue tracker user payload missing identifier")
    return UserRef(
        tenant_id=tenant_id,
        external_id=external_id,
        display_name=_optional_string(payload, "displayName"),
    )


def _issue_state(status: Mapping[str, object]) -> IssueState:
    name = (_optional_string(status, "name") or "").strip().lower()
    category = (_status_category(status) or "").strip().lower()
    if category == "done" or name in {"done", "closed", "resolved"}:
        return IssueState.DONE
    if any(token in name for token in ("block", "impediment", "on hold")):
        return IssueState.BLOCKED
    if category == "indeterminate" or any(token in name for token in ("progress", "review")):
        return IssueState.IN_PROGRESS
    return IssueState.TODO


def _status_category(status: Mapping[str, object]) -> str | None:
    category = _optional_mapping(status, "statusCategory")
    if category is None:
        return None
    return _optional_string(category, "key") or _optional_string(category, "name")


def _items(payload: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    value = payload.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)]


def _mapping_field(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    raise ProviderUnavailable(f"issue tracker payload missing object field {key}")


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
    raise ProviderUnavailable(f"issue tracker payload missing string field {key}")


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int):
        return str(value)
    return None


def _datetime_field(payload: Mapping[str, object], key: str) -> datetime | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    if len(normalized) >= 5 and normalized[-5] in {"+", "-"} and normalized[-3] != ":":
        normalized = f"{normalized[:-2]}:{normalized[-2:]}"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _metadata(values: Mapping[str, object]) -> Mapping[str, JsonScalar]:
    return {
        key: value
        for key, value in values.items()
        if value is None or isinstance(value, str | int | float | bool)
    }


def _jql_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _resolve_transition_id(payload: Mapping[str, object], to_state: str) -> str | None:
    """Resolve the transition id whose destination matches ``to_state``.

    Matches (case-insensitively) against the transition name, the destination
    status name, and the destination status category key, so a caller can pass
    either a capability-neutral state ("done") or a concrete workflow label.
    """
    wanted = to_state.strip().lower()
    if not wanted:
        return None
    for transition in _items(payload, "transitions"):
        candidates = [_optional_string(transition, "name")]
        destination = _optional_mapping(transition, "to")
        if destination is not None:
            candidates.append(_optional_string(destination, "name"))
            candidates.append(_status_category(destination))
        if any(candidate and candidate.strip().lower() == wanted for candidate in candidates):
            return _optional_string(transition, "id")
    return None


def _adf_document(body: str) -> Mapping[str, object]:
    """Wrap plain text in a minimal Atlassian Document Format doc for the v3 API."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": body}],
            }
        ],
    }
