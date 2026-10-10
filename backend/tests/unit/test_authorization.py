from __future__ import annotations

from typing import cast

import pytest

from core.application.authorization import AuthorizationPolicy, Capability, SensitiveField
from core.application.delivery_scope import (
    NodeAccess,
    pod_access,
    project_access,
    reads_pod_dates,
    reads_pod_detail,
    reads_project,
)
from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied


def test_policy_defaults_to_deny_for_unmapped_capability() -> None:
    principal = Principal(tenant_id="demo", subject="dev", roles=frozenset({Role.DEV}))
    policy = AuthorizationPolicy()
    assert not policy.can(principal, Capability.WRITE_CONNECTOR_SECRET)
    with pytest.raises(AuthorizationDenied):
        policy.ensure(principal, Capability.WRITE_CONNECTOR_SECRET)


def test_exec_cannot_read_raw_dm_but_can_read_budget_field() -> None:
    principal = Principal(tenant_id="demo", subject="exec", roles=frozenset({Role.EXEC}))
    policy = AuthorizationPolicy()
    assert policy.can(principal, Capability.READ_EXEC_AGGREGATE)
    assert policy.can(principal, Capability.READ_PROGRAM_ROLLUP)
    assert policy.can(principal, Capability.READ_PORTFOLIO_HEATMAP)
    assert policy.can(principal, Capability.READ_PROJECT_PROGRESS)
    assert not policy.can(principal, Capability.READ_POD_CHECKINS)
    assert not policy.can(principal, Capability.READ_POD_BLOCKERS)
    assert policy.can_read_field(principal, SensitiveField.BUDGET)


def test_persona_capabilities_follow_role_scope() -> None:
    policy = AuthorizationPolicy()
    dev = Principal(tenant_id="demo", subject="dev-asha", roles=frozenset({Role.DEV}))
    sm = Principal(tenant_id="demo", subject="sm", roles=frozenset({Role.SM}))
    po = Principal(tenant_id="demo", subject="po", roles=frozenset({Role.PO}))
    mgr = Principal(tenant_id="demo", subject="mgr", roles=frozenset({Role.MGR}))

    assert policy.can(dev, Capability.READ_OWN_WORK)
    assert policy.can(dev, Capability.READ_DIRECTORY)
    assert not policy.can(dev, Capability.READ_POD_BLOCKERS)
    assert policy.can(sm, Capability.READ_DIRECTORY)
    assert policy.can(sm, Capability.READ_POD_BLOCKERS)
    assert policy.can(sm, Capability.READ_POD_CHECKINS)
    # Not everywhere: a scrum master reads the projects their own pods work on,
    # a scoped read (core/application/delivery_scope.py, tested below).
    assert not policy.can(sm, Capability.READ_PROJECT_PROGRESS)
    assert policy.can(po, Capability.READ_PROJECT_PROGRESS)
    assert policy.can(po, Capability.READ_DIRECTORY)
    assert not policy.can(po, Capability.READ_POD_CHECKINS)
    assert policy.can(mgr, Capability.READ_PROGRAM_ROLLUP)
    assert policy.can(mgr, Capability.READ_PORTFOLIO_HEATMAP)
    assert policy.can(mgr, Capability.READ_POD_BLOCKERS)
    assert policy.can(mgr, Capability.READ_POD_CHECKINS)


# Who may open which kind of Delivery detail. Project and workstream progress is
# aggregate, so the executive reads it alongside the product owner and manager.
# Pod check-ins and blockers are per-person: the scrum master runs the pod, and
# the manager is the last step of the non-response escalation, so both read it;
# the executive does not.
_DETAIL_READERS: dict[Capability, frozenset[Role]] = {
    Capability.READ_PROJECT_PROGRESS: frozenset({Role.PO, Role.MGR, Role.EXEC, Role.ADMIN}),
    Capability.READ_POD_CHECKINS: frozenset({Role.SM, Role.MGR, Role.ADMIN}),
    Capability.READ_POD_BLOCKERS: frozenset({Role.SM, Role.MGR, Role.ADMIN}),
}

# Writes are unchanged by any read grant: a developer writes back to their own
# issues, and everything else is admin-only.
_WRITERS: dict[Capability, frozenset[Role]] = {
    Capability.WRITE_ISSUE_TRACKER: frozenset({Role.DEV, Role.ADMIN}),
    Capability.WRITE_CONNECTOR_SECRET: frozenset({Role.ADMIN}),
    Capability.DISPATCH_WORKFLOWS: frozenset({Role.ADMIN}),
    Capability.MANAGE_CONFIG: frozenset({Role.ADMIN}),
    # Delivery dates: the project's owners commit the project and its
    # releases; the scrum master commits the pod's part (their own pods only,
    # enforced where the date is set).
    Capability.SET_PROJECT_DATES: frozenset({Role.PO, Role.MGR, Role.ADMIN}),
    Capability.SET_POD_DATES: frozenset({Role.SM, Role.MGR, Role.ADMIN}),
    # Everyone who works the requirements confirms and tracks gate items; the
    # executive only reads them. Sign-off is limited per kind by the gate.
    Capability.EDIT_GATES: frozenset({Role.DEV, Role.SM, Role.PO, Role.MGR, Role.ADMIN}),
    # Day reports: the scrum master and the manager send and set them up (a
    # scrum master only for a project their pods work on, enforced where the
    # report is sent or saved). The day's note stays with set_project_dates.
    Capability.SEND_DAY_REPORTS: frozenset({Role.SM, Role.MGR, Role.ADMIN}),
    Capability.SET_UP_DAY_REPORTS: frozenset({Role.SM, Role.MGR, Role.ADMIN}),
}


def _roles_allowed(capability: Capability) -> frozenset[Role]:
    policy = AuthorizationPolicy()
    return frozenset(
        role
        for role in Role
        if policy.can(
            Principal(tenant_id="demo", subject=role.value, roles=frozenset({role})), capability
        )
    )


@pytest.mark.parametrize("capability", list(_DETAIL_READERS))
def test_delivery_detail_capabilities_follow_the_role_matrix(capability: Capability) -> None:
    assert _roles_allowed(capability) == _DETAIL_READERS[capability]


@pytest.mark.parametrize("capability", list(_WRITERS))
def test_write_capabilities_are_not_widened_by_read_grants(capability: Capability) -> None:
    assert _roles_allowed(capability) == _WRITERS[capability]


def test_every_role_can_read_its_own_work() -> None:
    """Own check-in and own directory lookup are not developer privileges.

    A scrum master, product owner, manager or executive is also asked for a
    check-in; the endpoints behind these capabilities resolve the caller's own
    subject, so granting them per role would only lock people out of their own
    status.
    """
    policy = AuthorizationPolicy()
    for role in Role:
        principal = Principal(tenant_id="demo", subject=role.value, roles=frozenset({role}))
        assert policy.can(principal, Capability.READ_OWN_WORK), role
        assert policy.can(principal, Capability.READ_DIRECTORY), role


def test_every_role_reads_the_day_reports() -> None:
    """A day report is what its readers are sent, so everyone may read it.

    Sending and setting up stay with the scrum master, manager and admin.
    """
    assert _roles_allowed(Capability.READ_DAY_REPORTS) == frozenset(Role)


def test_a_principal_with_no_role_can_read_nothing() -> None:
    policy = AuthorizationPolicy()
    principal = Principal(tenant_id="demo", subject="nobody", roles=frozenset())
    for capability in Capability:
        assert not policy.can(principal, capability), capability


def test_raw_dm_content_has_no_capability_or_sensitive_field_for_any_role() -> None:
    assert "READ_RAW_DM" not in Capability.__members__
    assert "RAW_DM_CONTENT" not in SensitiveField.__members__
    policy = AuthorizationPolicy()

    for role in Role:
        principal = Principal(tenant_id="demo", subject=role.value, roles=frozenset({role}))
        assert not policy.can_read_field(principal, cast(SensitiveField, "raw_dm_content"))


def test_dispatch_workflows_is_admin_only() -> None:
    policy = AuthorizationPolicy()
    admin = Principal(tenant_id="demo", subject="admin", roles=frozenset({Role.ADMIN}))
    manager = Principal(tenant_id="demo", subject="mgr", roles=frozenset({Role.MGR}))

    assert policy.can(admin, Capability.DISPATCH_WORKFLOWS)
    assert policy.can(admin, Capability.MANAGE_CONFIG)
    assert not policy.can(manager, Capability.DISPATCH_WORKFLOWS)
    assert not policy.can(manager, Capability.MANAGE_CONFIG)


def test_unknown_sensitive_field_and_scope_default_to_false() -> None:
    principal = Principal(
        tenant_id="demo",
        subject="dev",
        roles=frozenset({Role.DEV}),
        scopes=frozenset({"read:own"}),
    )
    policy = AuthorizationPolicy()

    assert principal.has_scope("read:own")
    assert not principal.has_scope("write:secrets")
    assert not policy.can_read_field(principal, cast(SensitiveField, "unknown"))


# Reads scoped to the caller's own part of the tree (delivery_scope.py): who reads
# a node they work on (own) and one they do not. The role capabilities above stay
# as they are; these only add a way in for the scrum master's projects and the
# developer's own pod.
_SCOPED_READS = {
    # role: (project own, project other, pod detail own, pod detail other,
    #        pod dates own, pod dates other)
    Role.DEV: (False, False, True, False, True, False),
    Role.SM: (True, False, True, True, True, True),
    Role.PO: (True, True, False, False, True, True),
    Role.MGR: (True, True, True, True, True, True),
    Role.EXEC: (True, True, False, False, True, True),
    Role.ADMIN: (True, True, True, True, True, True),
}


@pytest.mark.parametrize("role", list(_SCOPED_READS))
def test_scoped_reads_add_a_scrum_masters_projects_and_a_developers_own_pod(role: Role) -> None:
    policy = AuthorizationPolicy()
    principal = Principal(tenant_id="demo", subject=role.value, roles=frozenset({role}))
    got = tuple(
        read(policy, principal, own=own)
        for read in (reads_project, reads_pod_detail, reads_pod_dates)
        for own in (True, False)
    )
    assert got == _SCOPED_READS[role]


def test_what_a_listed_node_opens_on_follows_the_scoped_reads() -> None:
    """Delivery opens a pod's whole panel for its own people who read its detail.

    A manager or admin opens every pod; another pod of a scrum master's or
    product owner's projects opens as a project's reader sees it (its dates);
    a developer gets only its name. A developer's project is a name too: no
    project-level data beyond Today and Reports.
    """
    policy = AuthorizationPolicy()

    def who(role: Role) -> Principal:
        return Principal(tenant_id="demo", subject=role.value, roles=frozenset({role}))

    pods = {
        role: (pod_access(policy, who(role), own=True), pod_access(policy, who(role), own=False))
        for role in Role
    }
    assert pods == {
        Role.DEV: (NodeAccess.PANEL, NodeAccess.NAME),
        Role.SM: (NodeAccess.PANEL, NodeAccess.DATES),
        Role.PO: (NodeAccess.DATES, NodeAccess.DATES),
        Role.MGR: (NodeAccess.PANEL, NodeAccess.PANEL),
        Role.EXEC: (NodeAccess.DATES, NodeAccess.DATES),
        Role.ADMIN: (NodeAccess.PANEL, NodeAccess.PANEL),
    }
    projects = {role: project_access(policy, who(role), own=True) for role in Role}
    assert projects == {
        Role.DEV: NodeAccess.NAME,
        Role.SM: NodeAccess.PANEL,
        Role.PO: NodeAccess.PANEL,
        Role.MGR: NodeAccess.PANEL,
        Role.EXEC: NodeAccess.PANEL,
        Role.ADMIN: NodeAccess.PANEL,
    }
    nobody = Principal(tenant_id="demo", subject="nobody", roles=frozenset())
    assert pod_access(policy, nobody, own=True) is NodeAccess.NAME
    assert project_access(policy, nobody, own=True) is NodeAccess.NAME
