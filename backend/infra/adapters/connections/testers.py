"""Try a connection's values against the real system before it is used.

Every result is a fixed sentence written here, plus a few labelled facts the
system reported (server name, signed-in account). An exception's own text and
any response body stay out, so a test result never shows a token, a URL with a
key in it, or an internal host's error page. Nothing is written to the system,
except that a Teams webhook can only be checked by posting to it, so its test
posts one short message the admin asked for.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote, urlsplit

import httpx

from config.settings import Settings
from core.domain.connections import ConnectionCheck, ConnectionValues, FieldOption
from core.domain.errors import ProviderUnavailable
from infra.adapters.connections.specs import (
    EMAIL,
    GITHUB,
    GITLAB,
    GOOGLE_CALENDAR,
    JIRA,
    JIRA_AUTH_API_TOKEN,
    JIRA_AUTH_BASIC,
    JIRA_CLOUD,
    SLACK,
    SMTP_PLAIN,
    SMTP_SSL,
    TEAMS,
)
from infra.adapters.gitlab.gitlab_adapter import gitlab_api_url
from infra.adapters.jira.jira_adapter import credentials_from_connection
from infra.adapters.reports.teams import teams_card_payload

_TIMEOUT_SECONDS = 10.0


class _CheckFailed(Exception):
    """A failed check, carrying the sentence to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class HttpConnectionTester:
    settings: Settings
    transport: httpx.AsyncBaseTransport | None = None
    smtp_check: Callable[[Mapping[str, str]], None] | None = None

    async def test(
        self, tenant_id: str, connector: str, values: Mapping[str, str]
    ) -> ConnectionCheck:
        del tenant_id
        checks: dict[str, Callable[[Mapping[str, str]], Awaitable[ConnectionCheck]]] = {
            JIRA: self._jira,
            GITLAB: self._gitlab,
            GITHUB: self._github,
            SLACK: self._slack,
            EMAIL: self._email,
            TEAMS: self._teams,
            GOOGLE_CALENDAR: self._google_calendar,
        }
        check = checks.get(connector)
        if check is None:
            return ConnectionCheck(ok=False, message="This connector cannot be tested.")
        try:
            return await check(values)
        except _CheckFailed as failed:
            return ConnectionCheck(ok=False, message=failed.message)
        except ProviderUnavailable:
            return ConnectionCheck(ok=False, message="The connection is missing a required field.")

    async def _jira(self, values: Mapping[str, str]) -> ConnectionCheck:
        credentials = credentials_from_connection(ConnectionValues(connector=JIRA, values=values))
        headers, auth = credentials.request_auth()
        cloud = (values.get("deployment") or JIRA_CLOUD) == JIRA_CLOUD
        async with self._client(credentials.base_url, headers, auth) as client:
            info = await _json(
                client, "GET", f"{credentials.api}/serverInfo", "Jira", wrong_kind=not cloud
            )
            server_kind = str(info.get("deploymentType") or "")
            if cloud and server_kind and server_kind != "Cloud":
                raise _CheckFailed(
                    "This is Jira Data Center or Server: choose that as the Jira type."
                )
            if not cloud and server_kind == "Cloud":
                raise _CheckFailed("This is Jira Cloud: choose Jira Cloud as the Jira type.")
            me = await _json(
                client, "GET", f"{credentials.api}/myself", "Jira", sign_in=_jira_sign_in(values)
            )
            fields = await _json_list(client, f"{credentials.api}/field", "Jira")
        number_fields = _number_fields(fields)
        details = [
            ("Server", str(info.get("serverTitle") or credentials.base_url)),
            ("Version", str(info.get("version") or "unknown")),
            ("Signed in as", str(me.get("displayName") or me.get("name") or "unknown")),
        ]
        chosen = values.get("story_points_field")
        if chosen and chosen not in {option.value for option in number_fields}:
            return ConnectionCheck(
                ok=False,
                message=f"Signed in, but Jira has no number field {chosen}.",
                details=tuple(details),
                suggestions={"story_points_field": number_fields},
            )
        return ConnectionCheck(
            ok=True,
            message="Connected to Jira.",
            details=tuple(details),
            suggestions={"story_points_field": number_fields},
        )

    async def _gitlab(self, values: Mapping[str, str]) -> ConnectionCheck:
        base_url = gitlab_api_url(values.get("base_url") or "https://gitlab.com")
        headers = {"PRIVATE-TOKEN": values.get("token", "")}
        async with self._client(base_url, headers) as client:
            user = await _json(client, "GET", "/user", "GitLab", sign_in="the access token")
            details = [("Signed in as", str(user.get("username") or "unknown"))]
            namespace = values.get("namespace_id")
            if namespace:
                group = await _json(
                    client,
                    "GET",
                    f"/groups/{quote(namespace, safe='')}",
                    "GitLab",
                    missing=f"GitLab has no group {namespace} this token can read.",
                )
                details.append(("Group", str(group.get("full_path") or namespace)))
        return ConnectionCheck(ok=True, message="Connected to GitLab.", details=tuple(details))

    async def _github(self, values: Mapping[str, str]) -> ConnectionCheck:
        base_url = (values.get("base_url") or "https://api.github.com").rstrip("/")
        headers = {
            "Authorization": f"Bearer {values.get('token', '')}",
            "Accept": "application/vnd.github+json",
        }
        async with self._client(base_url, headers) as client:
            user = await _json(client, "GET", "/user", "GitHub", sign_in="the access token")
            details = [("Signed in as", str(user.get("login") or "unknown"))]
            owner = values.get("owner")
            if owner:
                found = await _json(
                    client,
                    "GET",
                    f"/users/{quote(owner, safe='')}",
                    "GitHub",
                    missing=f"GitHub has no organisation or user {owner}.",
                )
                details.append(("Owner", str(found.get("login") or owner)))
        return ConnectionCheck(ok=True, message="Connected to GitHub.", details=tuple(details))

    async def _slack(self, values: Mapping[str, str]) -> ConnectionCheck:
        base_url = self.settings.slack_api_base_url
        bot = {"Authorization": f"Bearer {values.get('bot_token', '')}"}
        async with self._client(base_url, bot) as client:
            identity = await _slack_call(client, "/auth.test", "bot token")
            details = [
                ("Workspace", str(identity.get("team") or "unknown")),
                ("Bot user", str(identity.get("user") or "unknown")),
            ]
            app_token = values.get("app_token")
            if app_token:
                # Asks for a Socket Mode URL; an unused URL expires on its own.
                await _slack_call(
                    client,
                    "/apps.connections.open",
                    "app-level token",
                    headers={"Authorization": f"Bearer {app_token}"},
                )
                details.append(("Socket Mode", "app-level token accepted"))
        return ConnectionCheck(ok=True, message="Connected to Slack.", details=tuple(details))

    async def _email(self, values: Mapping[str, str]) -> ConnectionCheck:
        check = self.smtp_check or _smtp_sign_in
        try:
            await asyncio.wait_for(asyncio.to_thread(check, values), _TIMEOUT_SECONDS + 5)
        except TimeoutError as exc:
            raise _CheckFailed("The mail server did not answer in time.") from exc
        host = values.get("host", "")
        return ConnectionCheck(
            ok=True,
            message="Connected to the mail server. No email was sent.",
            details=(("Server", f"{host}:{values.get('port', '')}"),),
        )

    async def _teams(self, values: Mapping[str, str]) -> ConnectionCheck:
        url = values.get("webhook_url", "")
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise _CheckFailed("The webhook URL must be an https address.")
        payload = teams_card_payload(
            "OpenProgram connection test",
            ["This channel will receive the day reports you send here."],
        )
        async with self._client(f"{parts.scheme}://{parts.netloc}", {}) as client:
            try:
                response = await client.post(url, json=payload)
            except httpx.HTTPError as exc:
                raise _CheckFailed("Could not reach the Teams webhook.") from exc
        if response.status_code >= 400:
            raise _CheckFailed("Teams refused the webhook URL. Copy it again from the channel.")
        return ConnectionCheck(ok=True, message="Posted a test message to the Teams channel.")

    async def _google_calendar(self, values: Mapping[str, str]) -> ConnectionCheck:
        base_url = (values.get("base_url") or "https://www.googleapis.com/calendar/v3").rstrip("/")
        calendar_id = values.get("calendar_id") or "primary"
        headers = {"Authorization": f"Bearer {values.get('token', '')}"}
        async with self._client(base_url, headers) as client:
            calendar = await _json(
                client,
                "GET",
                f"/calendars/{quote(calendar_id, safe='')}",
                "Google Calendar",
                sign_in="the access token",
                missing=f"Google Calendar has no calendar {calendar_id} this token can read.",
            )
        return ConnectionCheck(
            ok=True,
            message="Connected to Google Calendar.",
            details=(("Calendar", str(calendar.get("summary") or calendar_id)),),
        )

    def _client(
        self,
        base_url: str,
        headers: Mapping[str, str],
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        # No redirects: a sign-in page answering with a redirect is a refused token.
        return httpx.AsyncClient(
            base_url=base_url,
            headers={"Accept": "application/json", **headers},
            auth=auth,
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=self.transport,
        )


async def _response(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    system: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    host = urlsplit(str(client.base_url)).hostname or "the server"
    try:
        return await client.request(method, path, headers=headers)
    except httpx.TimeoutException as exc:
        raise _CheckFailed(f"{system} at {host} did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise _CheckFailed(f"Could not reach {system} at {host}.") from exc


async def _json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    system: str,
    *,
    sign_in: str = "the credentials",
    missing: str | None = None,
    wrong_kind: bool = False,
) -> Mapping[str, object]:
    payload = await _payload(
        client, method, path, system, sign_in=sign_in, missing=missing, wrong_kind=wrong_kind
    )
    if not isinstance(payload, Mapping):
        raise _CheckFailed(f"This address did not answer like {system}.")
    return cast(Mapping[str, object], payload)


async def _json_list(client: httpx.AsyncClient, path: str, system: str) -> list[object]:
    payload = await _payload(client, "GET", path, system)
    return list(payload) if isinstance(payload, list) else []


async def _payload(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    system: str,
    *,
    sign_in: str = "the credentials",
    missing: str | None = None,
    wrong_kind: bool = False,
) -> object:
    response = await _response(client, method, path, system)
    status = response.status_code
    if status in {401, 403}:
        raise _CheckFailed(f"{system} refused {sign_in}.")
    if 300 <= status < 400:
        raise _CheckFailed(
            f"{system} redirected the request, usually to a sign-in page: "
            f"it did not accept {sign_in}."
        )
    if status == 404:
        if wrong_kind:
            raise _CheckFailed(f"This address did not answer like {system} Data Center.")
        raise _CheckFailed(missing or f"This address did not answer like {system}.")
    if status >= 400:
        raise _CheckFailed(f"{system} answered with an error (HTTP {status}).")
    try:
        return response.json()
    except ValueError as exc:
        raise _CheckFailed(f"This address did not answer like {system}.") from exc


async def _slack_call(
    client: httpx.AsyncClient,
    path: str,
    token_label: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> Mapping[str, object]:
    response = await _response(client, "POST", path, "Slack", headers=headers)
    try:
        payload = response.json()
    except ValueError as exc:
        raise _CheckFailed("Slack did not answer as expected.") from exc
    if not isinstance(payload, Mapping):
        raise _CheckFailed("Slack did not answer as expected.")
    if payload.get("ok") is not True:
        error = payload.get("error")
        if error in {"invalid_auth", "not_authed", "account_inactive", "token_revoked"}:
            raise _CheckFailed(f"Slack refused the {token_label}.")
        if error == "not_allowed_token_type":
            raise _CheckFailed(f"That is not a {token_label}.")
        raise _CheckFailed(f"Slack did not accept the {token_label}.")
    return cast(Mapping[str, object], payload)


def _jira_sign_in(values: Mapping[str, str]) -> str:
    method = values.get("auth_method") or JIRA_AUTH_API_TOKEN
    if method == JIRA_AUTH_API_TOKEN:
        return "the email and API token"
    if method == JIRA_AUTH_BASIC:
        return "the user name and password"
    return "the personal access token"


def _number_fields(fields: list[object]) -> tuple[FieldOption, ...]:
    """Jira's custom number fields, story-point-looking ones first."""
    options: list[FieldOption] = []
    for item in fields:
        if not isinstance(item, Mapping):
            continue
        field_id = item.get("id")
        name = item.get("name")
        schema = item.get("schema")
        if not isinstance(field_id, str) or not isinstance(name, str):
            continue
        if not field_id.startswith("customfield_") or not isinstance(schema, Mapping):
            continue
        if schema.get("type") != "number":
            continue
        options.append(FieldOption(value=field_id, label=f"{name} ({field_id})"))

    def rank(option: FieldOption) -> tuple[int, str]:
        label = option.label.lower()
        return (0 if "point" in label or "estimate" in label else 1, label)

    return tuple(sorted(options, key=rank))


def _smtp_sign_in(values: Mapping[str, str]) -> None:
    host = values.get("host", "")
    port = int(values.get("port") or 587)
    security = values.get("security") or "starttls"
    context = ssl.create_default_context()
    try:
        server: smtplib.SMTP
        if security == SMTP_SSL:
            server = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT_SECONDS, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=_TIMEOUT_SECONDS)
        with server:
            server.ehlo()
            if security not in {SMTP_SSL, SMTP_PLAIN}:
                server.starttls(context=context)
                server.ehlo()
            if values.get("username"):
                server.login(values.get("username", ""), values.get("password", ""))
    except smtplib.SMTPAuthenticationError as exc:
        raise _CheckFailed("The mail server refused the user name or password.") from exc
    except smtplib.SMTPNotSupportedError as exc:
        raise _CheckFailed("The mail server does not offer STARTTLS on this port.") from exc
    except (ssl.SSLError, smtplib.SMTPServerDisconnected) as exc:
        raise _CheckFailed(
            "The mail server closed the connection: check the port and encryption."
        ) from exc
    except smtplib.SMTPException as exc:
        raise _CheckFailed("The mail server refused the connection.") from exc
    except OSError as exc:
        raise _CheckFailed(f"Could not reach the mail server {host}:{port}.") from exc
