"""The connectors an admin can set up, described by the adapters that use them.

Field keys are what the adapters read from a resolved connection, so a key
changes only together with the adapter that reads it.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.settings import Settings
from core.domain.connections import (
    ConnectorField,
    ConnectorPurpose,
    ConnectorSpec,
    FieldCondition,
    FieldKind,
    FieldOption,
)

JIRA = "jira"
GITLAB = "gitlab"
GITHUB = "github"
SLACK = "slack"
EMAIL = "email"
TEAMS = "teams"
GOOGLE_CALENDAR = "google_calendar"

JIRA_CLOUD = "cloud"
JIRA_DATA_CENTER = "data_center"
JIRA_AUTH_API_TOKEN = "api_token"
JIRA_AUTH_PAT = "personal_access_token"
JIRA_AUTH_BASIC = "basic"

SMTP_STARTTLS = "starttls"
SMTP_SSL = "ssl"
SMTP_PLAIN = "none"


def _when(field: str, *values: str) -> FieldCondition:
    return FieldCondition(field=field, values=values)


JIRA_SPEC = ConnectorSpec(
    id=JIRA,
    name="Jira",
    description="Issues, statuses, sprints and assignees for every project and pod.",
    purposes=(ConnectorPurpose.ISSUE_TRACKER,),
    fields=(
        ConnectorField(
            key="deployment",
            label="Jira type",
            kind=FieldKind.SELECT,
            required=True,
            default=JIRA_CLOUD,
            options=(
                FieldOption(value=JIRA_CLOUD, label="Jira Cloud (atlassian.net)"),
                FieldOption(value=JIRA_DATA_CENTER, label="Jira Data Center or Server"),
            ),
        ),
        ConnectorField(
            key="base_url",
            label="Jira address",
            kind=FieldKind.URL,
            required=True,
            placeholder="https://your-company.atlassian.net",
            help="The address you open Jira at, without a path.",
            routes_secrets=True,
        ),
        ConnectorField(
            key="auth_method",
            label="Sign-in method",
            kind=FieldKind.SELECT,
            required=True,
            default=JIRA_AUTH_API_TOKEN,
            routes_secrets=True,
            options=(
                FieldOption(
                    value=JIRA_AUTH_API_TOKEN,
                    label="Email and API token",
                    shown_when=_when("deployment", JIRA_CLOUD),
                ),
                FieldOption(
                    value=JIRA_AUTH_PAT,
                    label="Personal access token",
                    shown_when=_when("deployment", JIRA_DATA_CENTER),
                ),
                FieldOption(
                    value=JIRA_AUTH_BASIC,
                    label="User name and password",
                    shown_when=_when("deployment", JIRA_DATA_CENTER),
                ),
            ),
        ),
        ConnectorField(
            key="email",
            label="Account email",
            kind=FieldKind.EMAIL,
            required=True,
            help="The Atlassian account the API token belongs to.",
            shown_when=_when("auth_method", JIRA_AUTH_API_TOKEN),
            routes_secrets=True,
        ),
        ConnectorField(
            key="api_token",
            label="API token",
            kind=FieldKind.SECRET,
            required=True,
            help="Created at id.atlassian.com under Security, API tokens.",
            shown_when=_when("auth_method", JIRA_AUTH_API_TOKEN),
        ),
        ConnectorField(
            key="personal_access_token",
            label="Personal access token",
            kind=FieldKind.SECRET,
            required=True,
            help="Created in Jira under Profile, Personal Access Tokens. Read access is enough.",
            shown_when=_when("auth_method", JIRA_AUTH_PAT),
        ),
        ConnectorField(
            key="username",
            label="User name",
            kind=FieldKind.TEXT,
            required=True,
            shown_when=_when("auth_method", JIRA_AUTH_BASIC),
            routes_secrets=True,
        ),
        ConnectorField(
            key="password",
            label="Password",
            kind=FieldKind.SECRET,
            required=True,
            shown_when=_when("auth_method", JIRA_AUTH_BASIC),
        ),
        ConnectorField(
            key="story_points_field",
            label="Story points field",
            kind=FieldKind.TEXT,
            placeholder="customfield_10016",
            help=(
                "The custom field that holds story points. Leave it empty to count "
                "requirements instead. Test the connection to pick from Jira's fields."
            ),
        ),
    ),
)

GITLAB_SPEC = ConnectorSpec(
    id=GITLAB,
    name="GitLab",
    description="Repositories, commits and merge requests.",
    purposes=(ConnectorPurpose.CODE,),
    exclusive_group="code",
    fields=(
        ConnectorField(
            key="base_url",
            label="GitLab address",
            kind=FieldKind.URL,
            required=True,
            default="https://gitlab.com",
            help="The address you open GitLab at. The API path is added for you.",
            routes_secrets=True,
        ),
        ConnectorField(
            key="token",
            label="Access token",
            kind=FieldKind.SECRET,
            required=True,
            help="A personal, group or project access token with the read_api scope.",
        ),
        ConnectorField(
            key="namespace_id",
            label="Group",
            kind=FieldKind.TEXT,
            placeholder="acme or 1234",
            help="The group to read repositories from, by path or id. Empty reads every "
            "repository the token can see.",
        ),
    ),
)

GITHUB_SPEC = ConnectorSpec(
    id=GITHUB,
    name="GitHub",
    description="Repositories, commits and pull requests.",
    purposes=(ConnectorPurpose.CODE,),
    exclusive_group="code",
    fields=(
        ConnectorField(
            key="base_url",
            label="API address",
            kind=FieldKind.URL,
            required=True,
            default="https://api.github.com",
            help="Leave as is for github.com. GitHub Enterprise uses https://<host>/api/v3.",
            routes_secrets=True,
        ),
        ConnectorField(
            key="token",
            label="Access token",
            kind=FieldKind.SECRET,
            required=True,
            help="A fine-grained token with read access to contents and pull requests.",
        ),
        ConnectorField(
            key="owner",
            label="Organisation or user",
            kind=FieldKind.TEXT,
            help="Whose repositories to read. Empty reads the token owner's.",
        ),
    ),
)

SLACK_SPEC = ConnectorSpec(
    id=SLACK,
    name="Slack",
    description="Check-in messages, the member directory, and day reports to channels.",
    purposes=(ConnectorPurpose.CHAT, ConnectorPurpose.REPORT_DELIVERY),
    fields=(
        ConnectorField(
            key="bot_token",
            label="Bot token",
            kind=FieldKind.SECRET,
            required=True,
            placeholder="xoxb-…",
            help="From the Slack app's OAuth & Permissions page.",
        ),
        ConnectorField(
            key="app_token",
            label="App-level token",
            kind=FieldKind.SECRET,
            placeholder="xapp-…",
            help="Needed when replies arrive over Socket Mode (scope connections:write). "
            "A new token is used from the next reconnect.",
        ),
        ConnectorField(
            key="signing_secret",
            label="Signing secret",
            kind=FieldKind.SECRET,
            help="Needed when Slack sends replies to OpenProgram's web address.",
        ),
    ),
)

EMAIL_SPEC = ConnectorSpec(
    id=EMAIL,
    name="Email (SMTP)",
    description="Sends day reports to mailing lists and people by email.",
    purposes=(ConnectorPurpose.REPORT_DELIVERY,),
    fields=(
        ConnectorField(
            key="host",
            label="SMTP server",
            kind=FieldKind.TEXT,
            required=True,
            placeholder="smtp.example.com",
            routes_secrets=True,
        ),
        ConnectorField(
            key="port",
            label="Port",
            kind=FieldKind.NUMBER,
            required=True,
            default="587",
            routes_secrets=True,
        ),
        ConnectorField(
            key="security",
            label="Encryption",
            kind=FieldKind.SELECT,
            required=True,
            default=SMTP_STARTTLS,
            # Turning encryption off would send the stored password in clear.
            routes_secrets=True,
            options=(
                FieldOption(value=SMTP_STARTTLS, label="STARTTLS (usually port 587)"),
                FieldOption(value=SMTP_SSL, label="TLS from the start (usually port 465)"),
                FieldOption(value=SMTP_PLAIN, label="None (internal relays only)"),
            ),
        ),
        ConnectorField(
            key="username",
            label="User name",
            kind=FieldKind.TEXT,
            help="Leave empty for a relay that needs no sign-in.",
            routes_secrets=True,
        ),
        ConnectorField(
            key="password",
            label="Password",
            kind=FieldKind.SECRET,
        ),
        ConnectorField(
            key="from_address",
            label="From address",
            kind=FieldKind.EMAIL,
            required=True,
            placeholder="openprogram@example.com",
        ),
        ConnectorField(
            key="from_name",
            label="From name",
            kind=FieldKind.TEXT,
            default="OpenProgram",
        ),
    ),
)

TEAMS_SPEC = ConnectorSpec(
    id=TEAMS,
    name="Microsoft Teams",
    description="Posts day reports to a Teams channel through a webhook.",
    purposes=(ConnectorPurpose.REPORT_DELIVERY,),
    fields=(
        ConnectorField(
            key="webhook_url",
            label="Channel webhook URL",
            kind=FieldKind.SECRET,
            required=True,
            help="From the channel's Workflows: 'Post to a channel when a webhook request "
            "is received'. Anyone with the URL can post, so it is kept as a secret.",
        ),
        ConnectorField(
            key="channel_name",
            label="Channel name",
            kind=FieldKind.TEXT,
            placeholder="Delivery – daily",
            help="Shown when you pick where a report goes.",
        ),
    ),
)

GOOGLE_CALENDAR_SPEC = ConnectorSpec(
    id=GOOGLE_CALENDAR,
    name="Google Calendar",
    description="Out-of-office days, so a missing check-in on leave is not read as silence.",
    purposes=(ConnectorPurpose.CALENDAR,),
    fields=(
        ConnectorField(
            key="base_url",
            label="API address",
            kind=FieldKind.URL,
            required=True,
            default="https://www.googleapis.com/calendar/v3",
            routes_secrets=True,
        ),
        ConnectorField(
            key="token",
            label="Access token",
            kind=FieldKind.SECRET,
            required=True,
        ),
        ConnectorField(
            key="calendar_id",
            label="Calendar",
            kind=FieldKind.TEXT,
            help="A shared leave calendar's id. Empty reads each person's primary calendar.",
        ),
    ),
)

ALL_SPECS: tuple[ConnectorSpec, ...] = (
    JIRA_SPEC,
    GITLAB_SPEC,
    GITHUB_SPEC,
    SLACK_SPEC,
    EMAIL_SPEC,
    TEAMS_SPEC,
    GOOGLE_CALENDAR_SPEC,
)


@dataclass(frozen=True)
class SettingsConnectorCatalog:
    """Every connector, and which of them the server's environment already configures."""

    settings: Settings

    def specs(self) -> tuple[ConnectorSpec, ...]:
        return ALL_SPECS

    def environment_configured(self, connector: str) -> bool:
        settings = self.settings
        if connector == JIRA:
            return (
                settings.issue_tracker_provider == "jira"
                and bool(settings.jira_base_url)
                and bool(settings.jira_api_token)
            )
        if connector == GITLAB:
            return settings.vcs_provider == "gitlab" and bool(settings.gitlab_token)
        if connector == GITHUB:
            return settings.vcs_provider == "github" and bool(settings.github_token)
        if connector == SLACK:
            return settings.chat_provider == "slack" and bool(settings.slack_bot_token)
        if connector == GOOGLE_CALENDAR:
            return settings.calendar_provider == "google" and bool(settings.google_calendar_token)
        return False
