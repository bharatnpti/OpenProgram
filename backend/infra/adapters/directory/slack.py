from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from core.domain.directory import DirectoryUser
from core.domain.errors import ProviderUnavailable
from infra.adapters.chat.slack import SlackHttpClient


@dataclass
class SlackDirectoryProvider:
    http_client: SlackHttpClient

    async def fetch_users(self, tenant_id: str) -> list[DirectoryUser]:
        synced_at = datetime.now(tz=UTC)
        users: list[DirectoryUser] = []
        cursor: str | None = None
        while True:
            payload = await self.http_client.list_users(cursor)
            members = payload.get("members")
            if isinstance(members, list):
                for item in members:
                    user = _map_user(tenant_id, item, synced_at)
                    if user is not None:
                        users.append(user)
            cursor = _next_cursor(payload)
            if not cursor:
                return users


def _map_user(tenant_id: str, payload: object, synced_at: datetime) -> DirectoryUser | None:
    if not isinstance(payload, Mapping):
        return None
    if payload.get("deleted") is True or payload.get("is_bot") is True:
        return None
    external_id = _string_field(payload, "id")
    raw_profile = payload.get("profile")
    profile = cast(Mapping[str, object], raw_profile) if isinstance(raw_profile, Mapping) else {}
    display_name = _first_string(
        _profile_string(profile, "display_name"),
        _profile_string(profile, "real_name"),
        _string_field(payload, "name", default=external_id),
        external_id,
    )
    email = _profile_optional_string(profile, "email")
    handle = _string_field(payload, "name", default=None)
    avatar_url = _first_optional_string(
        _profile_string(profile, "image_192"),
        _profile_string(profile, "image_72"),
        _profile_string(profile, "image_48"),
        _profile_string(profile, "image_32"),
        _profile_string(profile, "image_24"),
    )
    title = _profile_optional_string(profile, "title")
    return DirectoryUser(
        tenant_id=tenant_id,
        external_id=external_id,
        display_name=display_name,
        email=email,
        handle=handle,
        avatar_url=avatar_url,
        title=title,
        source="slack",
        synced_at=synced_at,
        metadata={"slack_id": external_id},
    )


def _next_cursor(payload: Mapping[str, object]) -> str | None:
    metadata = payload.get("response_metadata")
    if isinstance(metadata, Mapping):
        cursor = metadata.get("next_cursor")
        if isinstance(cursor, str) and cursor.strip():
            return cursor.strip()
    return None


def _profile_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _profile_optional_string(payload: Mapping[str, object], key: str) -> str | None:
    return _profile_string(payload, key)


def _string_field(payload: Mapping[str, object], key: str, default: str | None = None) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    if default is not None:
        return default
    raise ProviderUnavailable(f"payload missing required string field {key}")


def _first_string(*values: str | None) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    raise ProviderUnavailable("slack directory payload missing display name")


def _first_optional_string(*values: str | None) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None
