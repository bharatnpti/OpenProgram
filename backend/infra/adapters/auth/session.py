from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from redis.asyncio import Redis

from core.domain.auth import Role
from core.ports.auth import AuthenticatedUser, AuthSession


@dataclass(frozen=True, kw_only=True)
class AuthFlowState:
    state: str
    nonce: str
    code_verifier: str
    return_url: str
    prompt: str | None = None


@dataclass(frozen=True, kw_only=True)
class StoredAuthSession:
    session: AuthSession
    token_material: dict[str, object] | None = None


class AuthSessionStore(Protocol):
    async def save_flow(self, flow: AuthFlowState, ttl_seconds: int) -> None: ...

    async def pop_flow(self, state: str) -> AuthFlowState | None: ...

    async def save_session(
        self,
        *,
        session_id: str,
        user: AuthenticatedUser,
        csrf_token: str,
        expires_at: datetime,
        token_material: dict[str, object] | None,
        ttl_seconds: int,
    ) -> AuthSession: ...

    async def get_session(self, session_id: str) -> StoredAuthSession | None: ...

    async def delete_session(self, session_id: str) -> None: ...


class InMemoryAuthSessionStore:
    def __init__(self) -> None:
        self._flows: dict[str, tuple[AuthFlowState, datetime]] = {}
        self._sessions: dict[str, StoredAuthSession] = {}

    async def save_flow(self, flow: AuthFlowState, ttl_seconds: int) -> None:
        self._flows[flow.state] = (flow, _expires_at(ttl_seconds))

    async def pop_flow(self, state: str) -> AuthFlowState | None:
        record = self._flows.pop(state, None)
        if record is None:
            return None
        flow, expires_at = record
        return flow if expires_at > _now() else None

    async def save_session(
        self,
        *,
        session_id: str,
        user: AuthenticatedUser,
        csrf_token: str,
        expires_at: datetime,
        token_material: dict[str, object] | None,
        ttl_seconds: int,
    ) -> AuthSession:
        del ttl_seconds
        session = AuthSession(
            session_id=session_id,
            user=user,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )
        self._sessions[session_id] = StoredAuthSession(
            session=session,
            token_material=dict(token_material) if token_material is not None else None,
        )
        return session

    async def get_session(self, session_id: str) -> StoredAuthSession | None:
        record = self._sessions.get(session_id)
        if record is None:
            return None
        if record.session.expires_at <= _now():
            await self.delete_session(session_id)
            return None
        return record

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class RedisAuthSessionStore:
    def __init__(self, redis: Redis, fernet: Fernet) -> None:
        self._redis = redis
        self._fernet = fernet

    async def save_flow(self, flow: AuthFlowState, ttl_seconds: int) -> None:
        await self._redis.setex(_flow_key(flow.state), ttl_seconds, json.dumps(_flow_to_json(flow)))

    async def pop_flow(self, state: str) -> AuthFlowState | None:
        key = _flow_key(state)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        await self._redis.delete(key)
        return _flow_from_json(_loads(raw))

    async def save_session(
        self,
        *,
        session_id: str,
        user: AuthenticatedUser,
        csrf_token: str,
        expires_at: datetime,
        token_material: dict[str, object] | None,
        ttl_seconds: int,
    ) -> AuthSession:
        session = AuthSession(
            session_id=session_id,
            user=user,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )
        await self._redis.setex(
            _session_key(session_id),
            ttl_seconds,
            json.dumps(self._session_to_json(session, token_material)),
        )
        return session

    async def get_session(self, session_id: str) -> StoredAuthSession | None:
        raw = await self._redis.get(_session_key(session_id))
        if raw is None:
            return None
        record = self._session_from_json(session_id, _loads(raw))
        if record.session.expires_at <= _now():
            await self.delete_session(session_id)
            return None
        return record

    async def delete_session(self, session_id: str) -> None:
        await self._redis.delete(_session_key(session_id))

    def _session_to_json(
        self,
        session: AuthSession,
        token_material: dict[str, object] | None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "tenant_id": session.user.tenant_id,
            "subject": session.user.subject,
            "roles": sorted(role.value for role in session.user.roles),
            "scopes": sorted(session.user.scopes),
            "username": session.user.username,
            "email": session.user.email,
            "name": session.user.name,
            "token_expires_at": _datetime_to_iso(session.user.token_expires_at),
            "csrf_token": session.csrf_token,
            "expires_at": _datetime_to_iso(session.expires_at),
        }
        if token_material is not None:
            raw_token = json.dumps(token_material, default=str).encode("utf-8")
            payload["token_material"] = self._fernet.encrypt(raw_token).decode("utf-8")
        return payload

    def _session_from_json(self, session_id: str, payload: dict[str, object]) -> StoredAuthSession:
        token_material = _decrypt_token_material(self._fernet, payload.get("token_material"))
        user = AuthenticatedUser(
            tenant_id=str(payload["tenant_id"]),
            subject=str(payload["subject"]),
            roles=frozenset(Role(str(role)) for role in _string_list(payload.get("roles"))),
            scopes=frozenset(_string_list(payload.get("scopes"))),
            username=_optional_string(payload.get("username")),
            email=_optional_string(payload.get("email")),
            name=_optional_string(payload.get("name")),
            token_expires_at=_datetime_from_iso(payload.get("token_expires_at")),
        )
        session = AuthSession(
            session_id=session_id,
            user=user,
            csrf_token=str(payload["csrf_token"]),
            expires_at=_datetime_from_iso(payload["expires_at"]) or _now(),
        )
        return StoredAuthSession(session=session, token_material=token_material)


def _flow_key(state: str) -> str:
    return f"auth:flow:{state}"


def _session_key(session_id: str) -> str:
    return f"auth:session:{session_id}"


def _expires_at(ttl_seconds: int) -> datetime:
    return _now() + timedelta(seconds=ttl_seconds)


def _now() -> datetime:
    return datetime.now(UTC)


def _flow_to_json(flow: AuthFlowState) -> dict[str, object]:
    return {
        "state": flow.state,
        "nonce": flow.nonce,
        "code_verifier": flow.code_verifier,
        "return_url": flow.return_url,
        "prompt": flow.prompt,
    }


def _flow_from_json(payload: dict[str, object]) -> AuthFlowState:
    return AuthFlowState(
        state=str(payload["state"]),
        nonce=str(payload["nonce"]),
        code_verifier=str(payload["code_verifier"]),
        return_url=str(payload["return_url"]),
        prompt=_optional_string(payload.get("prompt")),
    )


def _loads(value: bytes | str | object) -> dict[str, object]:
    raw = value.decode("utf-8") if isinstance(value, bytes) else value
    loaded = json.loads(str(raw))
    if not isinstance(loaded, dict):
        raise ValueError("stored auth payload must be a JSON object")
    return loaded


def _decrypt_token_material(fernet: Fernet, value: object) -> dict[str, object] | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        raw = fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, dict) else None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def _datetime_to_iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _datetime_from_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
