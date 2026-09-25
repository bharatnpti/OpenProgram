from __future__ import annotations

from dataclasses import dataclass

from core.domain.auth import Principal, Role
from core.ports.auth import AuthCredentials, AuthProvider


@dataclass(frozen=True)
class DevAuthProvider:
    tenant_id: str
    subject: str
    roles: frozenset[Role]

    async def authenticate(self, credentials: AuthCredentials) -> Principal:
        scopes = {"dev-mode"}
        if credentials.authorization is not None:
            scopes.add("token")
        # Persona switching for the local demo: the caller may name the person it
        # is acting as. The subject IS the developer_id every /me view resolves,
        # so this is what makes one console show any team member's own screens.
        subject = credentials.impersonate_subject or self.subject
        roles = self.roles
        if credentials.impersonate_subject is not None:
            scopes.add("impersonated")
            if credentials.impersonate_roles:
                roles = credentials.impersonate_roles
        return Principal(
            tenant_id=self.tenant_id,
            subject=subject,
            roles=roles,
            scopes=frozenset(scopes),
        )


@dataclass(frozen=True)
class AuthProviderCurrentPrincipal:
    auth_provider: AuthProvider
    credentials: AuthCredentials

    async def get(self) -> Principal:
        return await self.auth_provider.authenticate(self.credentials)


DevCurrentPrincipal = AuthProviderCurrentPrincipal
