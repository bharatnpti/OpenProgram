from __future__ import annotations

from enum import StrEnum

from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied


class Capability(StrEnum):
    READ_OWN_WORK = "read_own_work"
    READ_TEAM_AGGREGATE = "read_team_aggregate"
    READ_EXEC_AGGREGATE = "read_exec_aggregate"
    READ_RAW_DM = "read_raw_dm"
    WRITE_CONNECTOR_SECRET = "write_connector_secret"


class SensitiveField(StrEnum):
    BUDGET = "budget"
    RAW_DM_CONTENT = "raw_dm_content"


class AuthorizationPolicy:
    def can(self, principal: Principal, capability: Capability) -> bool:
        if principal.has_role(Role.ADMIN):
            return True
        if capability is Capability.READ_RAW_DM:
            return False
        allowed = {
            Role.DEV: frozenset({Capability.READ_OWN_WORK}),
            Role.PO: frozenset({Capability.READ_TEAM_AGGREGATE}),
            Role.SM: frozenset({Capability.READ_TEAM_AGGREGATE}),
            Role.MGR: frozenset({Capability.READ_TEAM_AGGREGATE, Capability.READ_EXEC_AGGREGATE}),
            Role.EXEC: frozenset({Capability.READ_EXEC_AGGREGATE}),
        }
        return any(capability in allowed.get(role, frozenset()) for role in principal.roles)

    def ensure(self, principal: Principal, capability: Capability) -> None:
        if not self.can(principal, capability):
            message = f"{principal.subject} is not authorized for {capability.value}"
            raise AuthorizationDenied(message)

    def can_read_field(self, principal: Principal, field: SensitiveField) -> bool:
        if field is SensitiveField.RAW_DM_CONTENT:
            return principal.has_role(Role.ADMIN)
        if field is SensitiveField.BUDGET:
            return principal.has_role(Role.ADMIN) or principal.has_role(Role.EXEC)
        return False
