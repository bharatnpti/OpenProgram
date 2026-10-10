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
    #: Commit a project's or a release's delivery date, and define releases.
    SET_PROJECT_DATES = "set_project_dates"
    #: Commit a pod's part of a project; a scrum master only for their own pods.
    SET_POD_DATES = "set_pod_dates"
    #: Confirm, add and track gate items and questions. Signing an item off is
    #: further limited to the roles its gate names for the item's kind.
    EDIT_GATES = "edit_gates"
    #: Read the day reports: which there are, today's report, and past sends.
    READ_DAY_REPORTS = "read_day_reports"
    #: Send a day report now; a scrum master only for a project one of their pods works on.
    SEND_DAY_REPORTS = "send_day_reports"
    #: Create, change, switch and remove day reports, with the same per-project limit.
    SET_UP_DAY_REPORTS = "set_up_day_reports"
    #: Run the release readiness check and act on its findings: link, not applicable,
    #: dismiss and create a draft. A scrum master only for pods they run and the
    #: projects those pods work on.
    ACT_ON_READINESS = "act_on_readiness"


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
                    Capability.EDIT_GATES,
                    Capability.READ_DAY_REPORTS,
                }
            ),
            Role.PO: frozenset(
                {
                    Capability.READ_TEAM_AGGREGATE,
                    Capability.READ_PROJECT_PROGRESS,
                    # The product owner owns the date the project promises, and
                    # writes the note the day's report opens with under it.
                    Capability.SET_PROJECT_DATES,
                    Capability.EDIT_GATES,
                    Capability.READ_DAY_REPORTS,
                    Capability.ACT_ON_READINESS,
                }
            ),
            Role.SM: frozenset(
                {
                    Capability.READ_TEAM_AGGREGATE,
                    Capability.READ_POD_BLOCKERS,
                    Capability.READ_POD_CHECKINS,
                    # The scrum master owns the date of the pod's part.
                    Capability.SET_POD_DATES,
                    Capability.EDIT_GATES,
                    # The scrum master runs the day, so sends and sets up the
                    # report of a project their pods work on.
                    Capability.READ_DAY_REPORTS,
                    Capability.SEND_DAY_REPORTS,
                    Capability.SET_UP_DAY_REPORTS,
                    Capability.ACT_ON_READINESS,
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
                    Capability.SET_PROJECT_DATES,
                    Capability.SET_POD_DATES,
                    Capability.EDIT_GATES,
                    Capability.READ_DAY_REPORTS,
                    Capability.SEND_DAY_REPORTS,
                    Capability.SET_UP_DAY_REPORTS,
                    Capability.ACT_ON_READINESS,
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
                    # A day report says what its readers are sent: aggregate
                    # state and asks, never anyone's reply.
                    Capability.READ_DAY_REPORTS,
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
