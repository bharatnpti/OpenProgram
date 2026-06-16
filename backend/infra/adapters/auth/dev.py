from __future__ import annotations

from dataclasses import dataclass

from core.domain.auth import Principal, Role
from core.ports.auth import AuthProvider


@dataclass(frozen=True)
class DevAuthProvider:
    tenant_id: str
    subject: str
    roles: frozenset[Role]

    async def authenticate(self, token: str | None) -> Principal:
        scopes = frozenset({"dev-mode"}) if token is None else frozenset({"dev-mode", "token"})
        return Principal(
            tenant_id=self.tenant_id,
            subject=self.subject,
            roles=self.roles,
            scopes=scopes,
        )


@dataclass(frozen=True)
class DevCurrentPrincipal:
    auth_provider: AuthProvider
    token: str | None

    async def get(self) -> Principal:
        return await self.auth_provider.authenticate(self.token)
