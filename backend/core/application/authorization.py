from __future__ import annotations

from enum import StrEnum

from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied


class Capability(StrEnum):
    READ_OWN_WORK = "read_own_work"
    READ_DIRECTORY = "read_directory"
    READ_TEAM_AGGREGATE = "read_team_aggregate"
    READ_EXEC_AGGREGATE = "read_exec_aggregate"
    READ_POD_BLOCKERS = "read_pod_blockers"
    READ_POD_CHECKINS = "read_pod_checkins"
    READ_PROJECT_PROGRESS = "read_project_progress"
    READ_PROGRAM_ROLLUP = "read_program_rollup"
    READ_PORTFOLIO_HEATMAP = "read_portfolio_heatmap"
    WRITE_CONNECTOR_SECRET = "write_connector_secret"
    WRITE_ISSUE_TRACKER = "write_issue_tracker"
    DISPATCH_WORKFLOWS = "dispatch_workflows"
    MANAGE_CONFIG = "manage_config"


class SensitiveField(StrEnum):
    BUDGET = "budget"


class AuthorizationPolicy:
    # Everyone is a person with their own work: every role is asked for a
    # check-in and must be able to read and answer it. The capability is
    # self-scoped by definition -- the endpoints behind it resolve the caller's
    # own subject -- so access to anyone else's data is granted separately.
    _SELF_CAPABILITIES = frozenset({Capability.READ_OWN_WORK, Capability.READ_DIRECTORY})

    def can(self, principal: Principal, capability: Capability) -> bool:
        allowed = {
            Role.ADMIN: frozenset({Capability.DISPATCH_WORKFLOWS, Capability.MANAGE_CONFIG}),
            Role.DEV: frozenset(
                {
                    # Developers may write back only to their own issues; ownership is
                    # enforced by WriteBackService (claims come from the developer's
                    # own finalized check-in). Admin is short-circuited above.
                    Capability.WRITE_ISSUE_TRACKER,
                }
            ),
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
                    # The manager is the last step of the non-response escalation
                    # (developer -> scrum master -> manager), so they must be able
                    # to open the pod they are escalated about.
                    Capability.READ_POD_BLOCKERS,
                    Capability.READ_POD_CHECKINS,
                }
            ),
            Role.EXEC: frozenset(
                {
                    Capability.READ_EXEC_AGGREGATE,
                    Capability.READ_PROGRAM_ROLLUP,
                    Capability.READ_PORTFOLIO_HEATMAP,
                    # Project and workstream progress is aggregate: the same
                    # colours, factors and task health the program tree already
                    # gives an executive. Pod check-ins and blockers are
                    # per-person, so they stay with the scrum master and manager.
                    Capability.READ_PROJECT_PROGRESS,
                }
            ),
        }
        if principal.has_role(Role.ADMIN):
            return True
        if capability in self._SELF_CAPABILITIES and principal.roles:
            return True
        return any(capability in allowed.get(role, frozenset()) for role in principal.roles)

    def ensure(self, principal: Principal, capability: Capability) -> None:
        if not self.can(principal, capability):
            message = f"{principal.subject} is not authorized for {capability.value}"
            raise AuthorizationDenied(message)

    def can_read_field(self, principal: Principal, field: SensitiveField) -> bool:
        if field is SensitiveField.BUDGET:
            return principal.has_role(Role.ADMIN) or principal.has_role(Role.EXEC)
        return False
