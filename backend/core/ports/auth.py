from __future__ import annotations

from typing import Protocol

from core.domain.auth import Principal


class AuthProvider(Protocol):
    async def authenticate(self, token: str | None) -> Principal: ...


class CurrentPrincipal(Protocol):
    async def get(self) -> Principal: ...
