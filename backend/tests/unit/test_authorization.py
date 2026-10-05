from __future__ import annotations

from typing import cast

import pytest

from core.application.authorization import AuthorizationPolicy, Capability, SensitiveField
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
