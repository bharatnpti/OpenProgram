"""Slack credentials from the tenant's connection, else the server's settings.

Each credential falls back on its own: an admin may set the bot token in the
console while Socket Mode keeps the app token from the environment. A client
built here reads its token on every call, so a token saved in the console is
used from the next request (or, for Socket Mode, the next reconnect) without a
restart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from core.domain.errors import ProviderConfigurationError
from core.ports.connections import ConnectionResolver
from infra.adapters.chat.slack import HttpSlackClient

SLACK_CONNECTOR = "slack"
BOT_TOKEN = "bot_token"
APP_TOKEN = "app_token"
SIGNING_SECRET = "signing_secret"

_LABELS = {
    BOT_TOKEN: "bot token",
    APP_TOKEN: "app-level token (xapp-, scope connections:write)",
    SIGNING_SECRET: "signing secret",
}
_SETTING_NAMES = {
    BOT_TOKEN: "slack_bot_token",
    APP_TOKEN: "slack_app_token",
    SIGNING_SECRET: "slack_signing_secret",
}


@dataclass(frozen=True)
class SlackCredentialSource:
    tenant_id: str
    connections: ConnectionResolver | None
    bot_token: str | None = None
    app_token: str | None = None
    signing_secret: str | None = None

    async def value(self, key: str) -> str | None:
        if self.connections is not None:
            values = await self.connections.resolve(self.tenant_id, SLACK_CONNECTOR)
            if values is not None and values.get(key):
                return values.get(key)
        fallback = {
            BOT_TOKEN: self.bot_token,
            APP_TOKEN: self.app_token,
            SIGNING_SECRET: self.signing_secret,
        }.get(key)
        return fallback.strip() if fallback and fallback.strip() else None


@dataclass(frozen=True)
class ConnectionSlackHttpClient:
    """A Slack Web API client that reads its token for every call."""

    credentials: SlackCredentialSource
    token_key: str = BOT_TOKEN
    base_url: str = "https://slack.com/api"
    retry_attempts: int = 3
    retry_backoff_seconds: float = 0.25

    async def open_conversation(self, user_id: str) -> str:
        return await (await self._client()).open_conversation(user_id)

    async def post_message(self, channel_id: str, text: str) -> str:
        return await (await self._client()).post_message(channel_id, text)

    async def latest_reply(self, thread_id: str) -> Mapping[str, object] | None:
        return await (await self._client()).latest_reply(thread_id)

    async def list_users(self, cursor: str | None = None) -> Mapping[str, object]:
        return await (await self._client()).list_users(cursor)

    async def open_socket_connection(self) -> str:
        return await (await self._client()).open_socket_connection()

    async def _client(self) -> HttpSlackClient:
        token = await self.credentials.value(self.token_key)
        if not token:
            label = _LABELS.get(self.token_key, self.token_key)
            setting = _SETTING_NAMES.get(self.token_key, self.token_key)
            raise ProviderConfigurationError(
                f"the Slack {label} is not set: add it under Admin, Integrations, "
                f"or set {setting} in the server settings"
            )
        return HttpSlackClient(
            bot_token=token,
            base_url=self.base_url,
            retry_attempts=self.retry_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )


@dataclass(frozen=True)
class SlackCredentialsReadinessProbe:
    """Ready when the bot token and the inbound credential are set, here or in settings."""

    credentials: SlackCredentialSource
    inbound_key: str

    async def check(self) -> bool:
        bot_token = await self.credentials.value(BOT_TOKEN)
        inbound = await self.credentials.value(self.inbound_key)
        return bool(bot_token and inbound)
