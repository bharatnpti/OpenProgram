from __future__ import annotations

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
    assert not policy.can(principal, Capability.READ_PROJECT_PROGRESS)
    assert not policy.can(principal, Capability.READ_RAW_DM)
    assert policy.can_read_field(principal, SensitiveField.BUDGET)
    assert not policy.can_read_field(principal, SensitiveField.RAW_DM_CONTENT)


def test_persona_capabilities_follow_role_scope() -> None:
    policy = AuthorizationPolicy()
    dev = Principal(tenant_id="demo", subject="dev-asha", roles=frozenset({Role.DEV}))
    sm = Principal(tenant_id="demo", subject="sm", roles=frozenset({Role.SM}))
    po = Principal(tenant_id="demo", subject="po", roles=frozenset({Role.PO}))
    mgr = Principal(tenant_id="demo", subject="mgr", roles=frozenset({Role.MGR}))

    assert policy.can(dev, Capability.READ_OWN_WORK)
    assert not policy.can(dev, Capability.READ_POD_BLOCKERS)
    assert policy.can(sm, Capability.READ_POD_BLOCKERS)
    assert policy.can(sm, Capability.READ_POD_CHECKINS)
    assert not policy.can(sm, Capability.READ_PROJECT_PROGRESS)
    assert policy.can(po, Capability.READ_PROJECT_PROGRESS)
    assert not policy.can(po, Capability.READ_POD_CHECKINS)
    assert policy.can(mgr, Capability.READ_PROGRAM_ROLLUP)
    assert policy.can(mgr, Capability.READ_PORTFOLIO_HEATMAP)


def test_admin_can_read_raw_dm() -> None:
    principal = Principal(tenant_id="demo", subject="admin", roles=frozenset({Role.ADMIN}))
    assert AuthorizationPolicy().can_read_field(principal, SensitiveField.RAW_DM_CONTENT)


def test_dispatch_workflows_is_admin_only() -> None:
    policy = AuthorizationPolicy()
    admin = Principal(tenant_id="demo", subject="admin", roles=frozenset({Role.ADMIN}))
    manager = Principal(tenant_id="demo", subject="mgr", roles=frozenset({Role.MGR}))

    assert policy.can(admin, Capability.DISPATCH_WORKFLOWS)
    assert not policy.can(manager, Capability.DISPATCH_WORKFLOWS)
