from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class SecretRef:
    tenant_id: str
    connector: str
    key: str


class SecretStore(Protocol):
    async def put(self, ref: SecretRef, value: str) -> None: ...

    async def get(self, ref: SecretRef) -> str: ...

    async def delete(self, ref: SecretRef) -> None:
        """Remove the secret. Removing one that is not stored is not an error."""
        ...
