from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
from opentelemetry import trace

from core.domain.connections import ConnectionValues
from core.domain.errors import ProviderUnavailable
from core.domain.graph import JsonScalar
from core.domain.integrations import (
    Issue,
    IssueComment,
    IssueState,
    IssueText,
    Project,
    Sprint,
    SyncCursor,
    UserRef,
)
from core.ports.connections import ConnectionResolver

_tracer = trace.get_tracer("openprogram.adapters.issue_tracker.jira")
_ISSUE_FIELDS = (
    "summary,status,assignee,updated,project,issuetype,parent,"
    "duedate,fixVersions,labels,priority,created,resolutiondate"
)
# JQL dates have minute precision and are read in the API user's profile timezone,
# so the incremental filter is a relative window, widened by this much to absorb
# rounding and clock skew; issues the cursor already covers are dropped afterwards.
_CURSOR_OVERLAP = timedelta(minutes=2)
_PAGE_SIZE = 100

#: Jira Cloud. Searches use the enhanced /rest/api/3/search/jql endpoint.
DEPLOYMENT_CLOUD = "cloud"
#: Jira Data Center or Server: the v2 REST API, paged by startAt, plain-text comments.
DEPLOYMENT_DATA_CENTER = "data_center"
AUTH_API_TOKEN = "api_token"
AUTH_PERSONAL_ACCESS_TOKEN = "personal_access_token"
AUTH_BASIC = "basic"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class JiraCredentials:
    base_url: str
    #: The user for HTTP basic auth; None sends ``token`` as a bearer token.
    user: str | None
    token: str
    deployment: str = DEPLOYMENT_CLOUD
    story_points_field: str | None = None

    @property
    def api(self) -> str:
        return "/rest/api/3" if self.deployment == DEPLOYMENT_CLOUD else "/rest/api/2"

    @property
    def data_center(self) -> bool:
        return self.deployment == DEPLOYMENT_DATA_CENTER

    def request_auth(self) -> tuple[dict[str, str], httpx.Auth | None]:
        if self.user:
            return {}, httpx.BasicAuth(self.user, self.token)
        return {"Authorization": f"Bearer {self.token}"}, None


def credentials_from_connection(values: ConnectionValues) -> JiraCredentials:
    """Jira credentials from an admin-set connection (field keys in the connector spec)."""
    base_url = values.get("base_url")
    deployment = values.get("deployment") or DEPLOYMENT_CLOUD
    method = values.get("auth_method") or AUTH_API_TOKEN
    user: str | None
    if method == AUTH_BASIC:
        user, token = values.get("username"), values.get("password")
    elif method == AUTH_PERSONAL_ACCESS_TOKEN:
        user, token = None, values.get("personal_access_token")
    else:
        user, token = values.get("email"), values.get("api_token")
    if not base_url or not token or (method != AUTH_PERSONAL_ACCESS_TOKEN and not user):
        raise ProviderUnavailable("issue tracker connection is incomplete")
    return JiraCredentials(
        base_url=base_url.rstrip("/"),
        user=user,
        token=token,
        deployment=deployment,
        story_points_field=values.get("story_points_field"),
    )


@dataclass(frozen=True)
class JiraIssueTrackerAdapter:
    """Jira Cloud or Data Center, read through its REST API.

    The tenant's connection set up in admin wins; without an enabled one the
    adapter uses the server's own settings (``base_url``, ``email``,
    ``api_token``, ``deployment``), so a deployment configured by environment
    keeps working.
    """

    base_url: str | None = None
    email: str | None = None
    api_token: str | None = None
    deployment: str = DEPLOYMENT_CLOUD
    story_points_field: str | None = None
    connections: ConnectionResolver | None = None
    timeout_seconds: float = 10.0
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def list_projects(self, tenant_id: str) -> list[Project]:
        with _tracer.start_as_current_span("jira.list_projects"):
            credentials = await self._credentials(tenant_id)
            if credentials.data_center:
                payload = await self._get_json(tenant_id, f"{credentials.api}/project")
                items = _list_items(payload)
            else:
                payload = await self._get(tenant_id, f"{credentials.api}/project/search")
                items = _items(payload, "values")
            return [_map_project(tenant_id, item) for item in items]

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
            credentials = await self._credentials(tenant_id)
            payload = await self._get(
                tenant_id,
                f"{credentials.api}/issue/{key}",
                params={"fields": _fields(credentials)},
            )
            return _map_issue(tenant_id, payload, credentials.story_points_field)

    async def get_issue_text(self, tenant_id: str, key: str) -> IssueText:
        """The description and comments as plain text, from Cloud's ADF or DC's wiki markup."""
        with _tracer.start_as_current_span("jira.get_issue_text"):
            credentials = await self._credentials(tenant_id)
            payload = await self._get(
                tenant_id,
                f"{credentials.api}/issue/{key}",
                params={"fields": "description,comment,status,updated"},
            )
            fields = _mapping_field(payload, "fields")
            status = _optional_mapping(fields, "status") or {}
            comment_block = _optional_mapping(fields, "comment") or {}
            comments: list[IssueComment] = []
            for item in _items(comment_block, "comments"):
                text, mentions = _rich_text(tenant_id, item.get("body"))
                author = _optional_mapping(item, "author")
                comments.append(
                    IssueComment(
                        id=_string_field(item, "id", default=""),
                        author=_map_user(tenant_id, author) if author else None,
                        created_at=_datetime_field(item, "created"),
                        body=text,
                        mentions=mentions,
                    )
                )
            description, _mentions = _rich_text(tenant_id, fields.get("description"))
            return IssueText(
                tenant_id=tenant_id,
                key=_string_field(payload, "key", default=key),
                state=_issue_state(status),
                description=description,
                comments=tuple(comments),
                updated_at=_datetime_field(fields, "updated"),
            )

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
            credentials = await self._credentials(tenant_id)
            payload = await self._get(tenant_id, f"{credentials.api}/issue/{key}/transitions")
            transition_id = _resolve_transition_id(payload, to_state)
            if transition_id is None:
                raise ProviderUnavailable(f"issue tracker has no transition to state {to_state!r}")
            await self._post(
                tenant_id,
                f"{credentials.api}/issue/{key}/transitions",
                json={"transition": {"id": transition_id}},
            )

    async def add_comment(self, tenant_id: str, key: str, body: str) -> None:
        with _tracer.start_as_current_span("jira.add_comment"):
            credentials = await self._credentials(tenant_id)
            # The v2 API takes plain text; v3 takes an Atlassian Document Format doc.
            comment: object = body if credentials.data_center else _adf_document(body)
            await self._post(
                tenant_id,
                f"{credentials.api}/issue/{key}/comment",
                json={"body": comment},
            )

    async def find_user_by_email(self, tenant_id: str, email: str) -> UserRef | None:
        with _tracer.start_as_current_span("jira.find_user_by_email"):
            credentials = await self._credentials(tenant_id)
            # Data Center searches user name, display name and email by "username".
            parameter = "username" if credentials.data_center else "query"
            payload = await self._get_json(
                tenant_id, f"{credentials.api}/user/search", params={parameter: email}
            )
            return _single_user_for_email(tenant_id, payload, email)

    async def _get(
        self,
        tenant_id: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        payload = await self._get_json(tenant_id, path, params=params)
        if not isinstance(payload, Mapping):
            raise ProviderUnavailable("issue tracker response was not an object")
        return cast(Mapping[str, object], payload)

    async def _get_json(
        self,
        tenant_id: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> object:
        credentials = await self._credentials(tenant_id)
        auth_headers, auth = credentials.request_auth()
        headers = {"Accept": "application/json", **auth_headers}

        try:
            async with httpx.AsyncClient(
                base_url=credentials.base_url,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.get(path, headers=headers, auth=auth, params=params)
                response.raise_for_status()
                payload: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable("issue tracker request failed") from exc
        return payload

    async def _post(
        self,
        tenant_id: str,
        path: str,
        *,
        json: Mapping[str, object],
    ) -> None:
        credentials = await self._credentials(tenant_id)
        auth_headers, auth = credentials.request_auth()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **auth_headers,
        }

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
        since = cursor.updated_at
        if since is None:
            return await self._search_issues_for_jql(tenant_id, f"{jql} ORDER BY updated ASC")
        # Not an absolute date: Jira answers an ISO-8601 literal with an empty
        # result rather than an error, and reads "yyyy-MM-dd HH:mm" in the API
        # user's profile timezone, which the cursor does not know.
        window = _relative_window(since, self.clock())
        issues = await self._search_issues_for_jql(
            tenant_id,
            f"({jql}) AND updated >= {window} ORDER BY updated ASC",
        )
        return [issue for issue in issues if issue.updated_at is None or issue.updated_at > since]

    async def _search_issues_for_jql(self, tenant_id: str, jql: str) -> list[Issue]:
        credentials = await self._credentials(tenant_id)
        if credentials.data_center:
            return await self._search_issues_by_offset(tenant_id, jql, credentials)
        issues: list[Issue] = []
        next_page_token: str | None = None
        while True:
            params = {
                "jql": jql,
                "fields": _fields(credentials),
                "maxResults": str(_PAGE_SIZE),
            }
            if next_page_token is not None:
                params["nextPageToken"] = next_page_token
            payload = await self._get(
                tenant_id,
                "/rest/api/3/search/jql",
                params=params,
            )
            issues.extend(
                _map_issue(tenant_id, item, credentials.story_points_field)
                for item in _items(payload, "issues")
            )
            next_page_token = _optional_string(payload, "nextPageToken")
            if next_page_token is None:
                return issues

    async def _search_issues_by_offset(
        self, tenant_id: str, jql: str, credentials: JiraCredentials
    ) -> list[Issue]:
        """Data Center search: /rest/api/2/search, paged by startAt until total is reached."""
        issues: list[Issue] = []
        start_at = 0
        while True:
            payload = await self._get(
                tenant_id,
                f"{credentials.api}/search",
                params={
                    "jql": jql,
                    "fields": _fields(credentials),
                    "startAt": str(start_at),
                    "maxResults": str(_PAGE_SIZE),
                },
            )
            page = _items(payload, "issues")
            issues.extend(
                _map_issue(tenant_id, item, credentials.story_points_field) for item in page
            )
            start_at += len(page)
            total = payload.get("total")
            if not page or not isinstance(total, int) or start_at >= total:
                return issues

    async def _credentials(self, tenant_id: str) -> JiraCredentials:
        if self.connections is not None:
            values = await self.connections.resolve(tenant_id, "jira")
            if values is not None:
                return credentials_from_connection(values)
        if not self.base_url or not self.api_token:
            raise ProviderUnavailable("issue tracker credentials are not configured")
        return JiraCredentials(
            base_url=self.base_url.rstrip("/"),
            user=self.email,
            token=self.api_token,
            deployment=self.deployment,
            story_points_field=self.story_points_field,
        )


def _fields(credentials: JiraCredentials) -> str:
    if credentials.story_points_field:
        return f"{_ISSUE_FIELDS},{credentials.story_points_field}"
    return _ISSUE_FIELDS


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


def _map_issue(
    tenant_id: str,
    payload: Mapping[str, object],
    story_points_field: str | None = None,
) -> Issue:
    fields = _mapping_field(payload, "fields")
    status = _mapping_field(fields, "status")
    assignee_payload = _optional_mapping(fields, "assignee")
    project = _optional_mapping(fields, "project")
    issue_type = _optional_mapping(fields, "issuetype")
    parent = _optional_mapping(fields, "parent")
    priority = _optional_mapping(fields, "priority")
    versions = _items(fields, "fixVersions")
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
                "due_date": _optional_string(fields, "duedate"),
                "fix_versions": _joined(_optional_string(item, "name") for item in versions),
                "fix_version_release_date": _latest(
                    _optional_string(item, "releaseDate") for item in versions
                ),
                "fix_version_dates": _version_dates(versions),
                "labels": _joined(_strings(fields.get("labels"))),
                "priority": priority.get("name") if priority else None,
                "created_at": _iso(_datetime_field(fields, "created")),
                "resolved_at": _iso(_datetime_field(fields, "resolutiondate")),
                "story_points": (
                    _number(fields.get(story_points_field)) if story_points_field else None
                ),
            }
        ),
    )


def _version_dates(versions: Sequence[Mapping[str, object]]) -> str | None:
    """Each fix version's release date as JSON, for releases defined by fix version."""
    dates = {
        name: released
        for item in versions
        if (name := _optional_string(item, "name")) is not None
        and (released := _optional_string(item, "releaseDate")) is not None
    }
    return json.dumps(dates, sort_keys=True) if dates else None


def _joined(values: Iterable[str | None]) -> str | None:
    names = [value for value in values if value]
    return ", ".join(names) if names else None


def _latest(values: Iterable[str | None]) -> str | None:
    dates = sorted(value for value in values if value)
    return dates[-1] if dates else None


def _strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


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


def _single_user_for_email(tenant_id: str, payload: object, email: str) -> UserRef | None:
    """Pick the one human account ``user/search`` returned for an email.

    The search is a prefix match over email and display name, and Jira blanks
    ``emailAddress`` unless the owner made it visible, so the address usually
    cannot be compared. Prefer a visible exact match; otherwise accept the
    result only when it is the single active human account, and refuse to
    guess between several.
    """
    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
        raise ProviderUnavailable("issue tracker user search response was not a list")
    people = [
        cast(Mapping[str, object], item)
        for item in payload
        if isinstance(item, Mapping)
        and item.get("accountType", "atlassian") == "atlassian"
        and item.get("active", True) is not False
    ]
    wanted = email.strip().lower()
    exact = [p for p in people if (_optional_string(p, "emailAddress") or "").lower() == wanted]
    if len(exact) == 1:
        return _map_user(tenant_id, exact[0])
    if len(people) == 1 and not _optional_string(people[0], "emailAddress"):
        return _map_user(tenant_id, people[0])
    return None


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
    return _list_items(payload.get(key))


def _list_items(value: object) -> list[Mapping[str, object]]:
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


def _relative_window(since: datetime, now: datetime) -> str:
    """A JQL relative date ("-95m") reaching back past ``since``, timezone-free."""
    elapsed = max(now - since, timedelta(0)) + _CURSOR_OVERLAP
    return f"-{math.ceil(elapsed.total_seconds() / 60)}m"


def _jql_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _resolve_transition_id(payload: Mapping[str, object], to_state: str) -> str | None:
    """Resolve the id of a transition this issue really offers to ``to_state``.

    A canonical state (``todo``, ``in_progress``, ``in_review``, ``blocked``,
    ``done``) is matched by where each transition leads, read exactly as
    ``_issue_state`` reads a status, so a write lands on the state the next sync
    reads back. Anything else is matched case-insensitively against the
    transition name, the destination status name, and the destination status
    category key (a concrete workflow label).
    """
    wanted = to_state.strip().lower()
    if not wanted:
        return None
    transitions = _items(payload, "transitions")
    if wanted in _CANONICAL_STATES:
        return _canonical_transition_id(transitions, wanted)
    for transition in transitions:
        candidates = [_optional_string(transition, "name")]
        destination = _optional_mapping(transition, "to")
        if destination is not None:
            candidates.append(_optional_string(destination, "name"))
            candidates.append(_status_category(destination))
        if any(candidate and candidate.strip().lower() == wanted for candidate in candidates):
            return _optional_string(transition, "id")
    return None


_CANONICAL_STATES: Mapping[str, IssueState] = {
    "todo": IssueState.TODO,
    "in_progress": IssueState.IN_PROGRESS,
    # Jira has no review category: a review status sits in "In Progress", and
    # _issue_state reads it as in progress.
    "in_review": IssueState.IN_PROGRESS,
    "blocked": IssueState.BLOCKED,
    "done": IssueState.DONE,
}
# Done-category resolutions that are not "the work is finished".
_NOT_DONE_TOKENS = ("won't", "wont", "cancel", "reject", "duplicate", "declin", "invalid")


def _canonical_transition_id(transitions: list[Mapping[str, object]], wanted: str) -> str | None:
    """Pick the transition leading to the canonical state ``wanted``, or ``None``.

    ``in_review`` prefers a destination whose name says review; a workflow
    without one (To Do / In Progress / Done) gets its in-progress transition,
    since OpenProgram reads a review status as in progress anyway.
    """
    leads_to = [
        (transition, destination)
        for transition in transitions
        if (destination := _optional_mapping(transition, "to")) is not None
        and _issue_state(destination) is _CANONICAL_STATES[wanted]
    ]

    def name(destination: Mapping[str, object]) -> str:
        return (_optional_string(destination, "name") or "").strip().lower()

    if wanted == "in_review":
        review = [pair for pair in leads_to if "review" in name(pair[1])]
        if review:
            leads_to = review
        else:
            wanted = "in_progress"
    if wanted == "in_progress":
        not_review = [pair for pair in leads_to if "review" not in name(pair[1])]
        progress = [pair for pair in not_review if "progress" in name(pair[1])]
        leads_to = progress or not_review
    elif wanted == "done":
        finished = [
            pair
            for pair in leads_to
            if not any(token in name(pair[1]) for token in _NOT_DONE_TOKENS)
        ]
        exact = [pair for pair in finished if name(pair[1]) == "done"]
        leads_to = exact or finished
    elif wanted == "todo":
        exact = [pair for pair in leads_to if name(pair[1]) in {"to do", "todo"}]
        leads_to = exact or leads_to
    for transition, _destination in leads_to:
        transition_id = _optional_string(transition, "id")
        if transition_id is not None:
            return transition_id
    return None


_DC_MENTION = re.compile(r"\[~(?:accountid:)?([^\]]+)\]")
_WIKI_HEADING = re.compile(r"^(\s*)h([1-6])\.\s+", re.MULTILINE)
_WIKI_NUMBERED = re.compile(r"^(\s*)(#+)\s+", re.MULTILINE)
_WIKI_BULLET = re.compile(r"^(\s*)(\*+)\s+", re.MULTILINE)


def _wiki_text(value: str) -> str:
    """Wiki markup written the way ADF is flattened, where '#' starts a heading, not a list."""
    text = _WIKI_NUMBERED.sub(lambda match: "  " * (len(match.group(2)) - 1) + "1. ", value)
    text = _WIKI_BULLET.sub(lambda match: "  " * (len(match.group(2)) - 1) + "- ", text)
    return _WIKI_HEADING.sub(lambda match: "#" * int(match.group(2)) + " ", text)


def _rich_text(tenant_id: str, value: object) -> tuple[str, tuple[UserRef, ...]]:
    """Plain text and the people it mentions, from ADF (Cloud) or wiki markup (DC)."""
    if isinstance(value, str):
        mentions = tuple(
            UserRef(tenant_id=tenant_id, external_id=match.group(1))
            for match in _DC_MENTION.finditer(value)
        )
        text = _DC_MENTION.sub(lambda match: f"@{match.group(1)}", _wiki_text(value))
        return text, mentions
    if not isinstance(value, Mapping):
        return "", ()
    lines: list[str] = []
    found: list[UserRef] = []
    _adf_blocks(cast(Mapping[str, object], value), lines, found, tenant_id, prefix="")
    text = "\n".join(line.rstrip() for line in lines)
    return text.strip(), tuple(found)


def _adf_blocks(
    node: Mapping[str, object],
    lines: list[str],
    mentions: list[UserRef],
    tenant_id: str,
    *,
    prefix: str,
) -> None:
    kind = node.get("type")
    children = _list_items(node.get("content"))
    if kind in {"paragraph", "heading", "codeBlock", "blockquote"} and all(
        child.get("type") not in _ADF_BLOCKS for child in children
    ):
        text = "".join(_adf_inline(child, mentions, tenant_id) for child in children)
        if kind == "heading":
            attrs = _optional_mapping(node, "attrs") or {}
            level = attrs.get("level") if isinstance(attrs.get("level"), int) else 2
            text = f"{'#' * int(level)} {text}"  # type: ignore[call-overload]
        lines.append(f"{prefix}{text}")
        return
    if kind in {"bulletList", "orderedList", "taskList"}:
        for index, child in enumerate(children, start=1):
            if kind == "orderedList":
                marker = f"{index}. "
            elif kind == "taskList":
                attrs = _optional_mapping(child, "attrs") or {}
                marker = "[x] " if attrs.get("state") == "DONE" else "[ ] "
            else:
                marker = "- "
            _adf_list_item(child, lines, mentions, tenant_id, prefix=prefix, marker=marker)
        return
    if kind == "table":
        for row in children:
            cells = [
                " ".join(_adf_text(cell, mentions, tenant_id).split())
                for cell in _list_items(row.get("content"))
            ]
            lines.append(f"{prefix}{' | '.join(cells)}")
        return
    for child in children:
        _adf_blocks(child, lines, mentions, tenant_id, prefix=prefix)


def _adf_list_item(
    node: Mapping[str, object],
    lines: list[str],
    mentions: list[UserRef],
    tenant_id: str,
    *,
    prefix: str,
    marker: str,
) -> None:
    children = _list_items(node.get("content"))
    if node.get("type") == "taskItem":
        text = "".join(_adf_inline(child, mentions, tenant_id) for child in children)
        lines.append(f"{prefix}{marker}{text}")
        return
    first = True
    for child in children:
        if first and child.get("type") == "paragraph":
            text = "".join(
                _adf_inline(part, mentions, tenant_id) for part in _list_items(child.get("content"))
            )
            lines.append(f"{prefix}{marker}{text}")
            first = False
            continue
        _adf_blocks(child, lines, mentions, tenant_id, prefix=prefix + "  ")


def _adf_text(node: Mapping[str, object], mentions: list[UserRef], tenant_id: str) -> str:
    lines: list[str] = []
    _adf_blocks(node, lines, mentions, tenant_id, prefix="")
    return " ".join(lines)


def _adf_inline(node: Mapping[str, object], mentions: list[UserRef], tenant_id: str) -> str:
    kind = node.get("type")
    if kind == "text":
        return _optional_string(node, "text") or ""
    if kind == "hardBreak":
        return " "
    if kind == "mention":
        attrs = _optional_mapping(node, "attrs") or {}
        account = _optional_string(attrs, "id")
        name = (_optional_string(attrs, "text") or "").lstrip("@")
        if account:
            mentions.append(
                UserRef(tenant_id=tenant_id, external_id=account, display_name=name or None)
            )
        return f"@{name or account or 'someone'}"
    if kind in {"emoji", "inlineCard", "status"}:
        attrs = _optional_mapping(node, "attrs") or {}
        return _optional_string(attrs, "text") or _optional_string(attrs, "url") or ""
    return "".join(
        _adf_inline(child, mentions, tenant_id) for child in _list_items(node.get("content"))
    )


_ADF_BLOCKS = frozenset(
    {"paragraph", "heading", "bulletList", "orderedList", "taskList", "table", "codeBlock"}
)


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
