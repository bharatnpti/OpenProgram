from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from core.domain.auth import Principal, Role


@dataclass(frozen=True, kw_only=True)
class AuthCredentials:
    authorization: str | None = None
    session_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class AuthenticatedUser:
    tenant_id: str
    subject: str
    roles: frozenset[Role]
    scopes: frozenset[str]
    username: str | None = None
    email: str | None = None
    name: str | None = None
    token_expires_at: datetime | None = None

    def principal(self) -> Principal:
        return Principal(
            tenant_id=self.tenant_id,
            subject=self.subject,
            roles=self.roles,
            scopes=self.scopes,
        )


@dataclass(frozen=True, kw_only=True)
class AuthSession:
    session_id: str
    user: AuthenticatedUser
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True, kw_only=True)
class AuthCallbackResult:
    session: AuthSession
    redirect_url: str


@dataclass(frozen=True, kw_only=True)
class AuthLogoutResult:
    success: bool
    message: str
    redirect_url: str


class AuthProvider(Protocol):
    async def authenticate(self, credentials: AuthCredentials) -> Principal: ...


class CurrentPrincipal(Protocol):
    async def get(self) -> Principal: ...
