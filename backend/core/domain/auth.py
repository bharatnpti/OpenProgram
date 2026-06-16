from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    DEV = "dev"
    PO = "po"
    SM = "sm"
    MGR = "mgr"
    EXEC = "exec"
    ADMIN = "admin"


@dataclass(frozen=True, kw_only=True)
class Principal:
    tenant_id: str
    subject: str
    roles: frozenset[Role]
    scopes: frozenset[str] = field(default_factory=frozenset)

    def has_role(self, role: Role) -> bool:
        return role in self.roles

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes
