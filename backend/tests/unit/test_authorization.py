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
    assert not policy.can(principal, Capability.READ_RAW_DM)
    assert policy.can_read_field(principal, SensitiveField.BUDGET)
    assert not policy.can_read_field(principal, SensitiveField.RAW_DM_CONTENT)


def test_admin_can_read_raw_dm() -> None:
    principal = Principal(tenant_id="demo", subject="admin", roles=frozenset({Role.ADMIN}))
    assert AuthorizationPolicy().can_read_field(principal, SensitiveField.RAW_DM_CONTENT)
