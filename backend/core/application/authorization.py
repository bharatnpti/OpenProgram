from __future__ import annotations

from enum import StrEnum

from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied


class Capability(StrEnum):
    READ_OWN_WORK = "read_own_work"
    READ_TEAM_AGGREGATE = "read_team_aggregate"
    READ_EXEC_AGGREGATE = "read_exec_aggregate"
    READ_POD_BLOCKERS = "read_pod_blockers"
    READ_POD_CHECKINS = "read_pod_checkins"
    READ_PROJECT_PROGRESS = "read_project_progress"
    READ_PROGRAM_ROLLUP = "read_program_rollup"
    READ_PORTFOLIO_HEATMAP = "read_portfolio_heatmap"
    READ_RAW_DM = "read_raw_dm"
    WRITE_CONNECTOR_SECRET = "write_connector_secret"
    DISPATCH_WORKFLOWS = "dispatch_workflows"


class SensitiveField(StrEnum):
    BUDGET = "budget"
    RAW_DM_CONTENT = "raw_dm_content"


class AuthorizationPolicy:
    def can(self, principal: Principal, capability: Capability) -> bool:
        allowed = {
            Role.ADMIN: frozenset({Capability.DISPATCH_WORKFLOWS}),
            Role.DEV: frozenset({Capability.READ_OWN_WORK}),
            Role.PO: frozenset(
                {
                    Capability.READ_TEAM_AGGREGATE,
                    Capability.READ_PROJECT_PROGRESS,
                }
            ),
            Role.SM: frozenset(
                {
                    Capability.READ_TEAM_AGGREGATE,
                    Capability.READ_POD_BLOCKERS,
                    Capability.READ_POD_CHECKINS,
                }
            ),
            Role.MGR: frozenset(
                {
                    Capability.READ_TEAM_AGGREGATE,
                    Capability.READ_EXEC_AGGREGATE,
                    Capability.READ_PROJECT_PROGRESS,
                    Capability.READ_PROGRAM_ROLLUP,
                    Capability.READ_PORTFOLIO_HEATMAP,
                }
            ),
            Role.EXEC: frozenset(
                {
                    Capability.READ_EXEC_AGGREGATE,
                    Capability.READ_PROGRAM_ROLLUP,
                    Capability.READ_PORTFOLIO_HEATMAP,
                }
            ),
        }
        if principal.has_role(Role.ADMIN):
            return True
        if capability is Capability.READ_RAW_DM:
            return False
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
