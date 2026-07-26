from __future__ import annotations

from core.domain.escalation import (
    EscalationTarget,
    PodEscalationContacts,
    default_escalation_policy,
)


def test_default_policy_full_ladder() -> None:
    policy = default_escalation_policy(
        developer_wait_seconds=100,
        scrum_master_wait_seconds=200,
        manager_wait_seconds=300,
    )
    assert [step.target for step in policy.steps] == [
        EscalationTarget.DEVELOPER,
        EscalationTarget.SCRUM_MASTER,
        EscalationTarget.MANAGER,
    ]
    assert [step.wait_seconds for step in policy.steps] == [100, 200, 300]
    assert policy.step(1) is not None
    assert policy.step(1).target is EscalationTarget.DEVELOPER  # type: ignore[union-attr]
    assert policy.step(4) is None
    assert policy.step(0) is None


def test_default_policy_disables_human_rungs() -> None:
    policy = default_escalation_policy(
        developer_wait_seconds=100,
        scrum_master_wait_seconds=200,
        manager_wait_seconds=300,
        escalate_to_scrum_master=False,
        escalate_to_manager=False,
    )
    assert [step.target for step in policy.steps] == [EscalationTarget.DEVELOPER]


def test_default_policy_drops_non_positive_waits() -> None:
    policy = default_escalation_policy(
        developer_wait_seconds=100,
        scrum_master_wait_seconds=0,
        manager_wait_seconds=300,
    )
    assert [step.target for step in policy.steps] == [
        EscalationTarget.DEVELOPER,
        EscalationTarget.MANAGER,
    ]


def test_pod_escalation_contacts_lookup() -> None:
    contacts = PodEscalationContacts()
    assert contacts.contact_for(EscalationTarget.SCRUM_MASTER) is None
    assert contacts.contact_for(EscalationTarget.DEVELOPER) is None
