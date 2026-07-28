from __future__ import annotations

from datetime import datetime

from core.domain.escalation import (
    EscalationContact,
    EscalationTarget,
    PodEscalationContacts,
    default_escalation_policy,
    escalation_contacts_from_metadata,
)
from infra.workflows.daily_checkin import DailyCheckinInput, DailyCheckinResult
from infra.workflows.daily_checkin import (
    nudge_input_for_daily_checkin_result as build_nudge_input,
)
from infra.workflows.nudge import (
    EscalationStepPayload,
    NudgeInput,
    decide_escalation_delivery,
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


def test_escalation_contacts_from_metadata_round_trip() -> None:
    contacts = escalation_contacts_from_metadata(
        {
            "escalation_sm_chat_external_id": "U-SM",
            "escalation_sm_display_name": "Sam",
            "escalation_manager_chat_external_id": "  ",
        }
    )
    assert contacts.scrum_master is not None
    assert contacts.scrum_master.chat_external_id == "U-SM"
    assert contacts.manager is None


def test_decide_delivery_developer_available_sends_to_developer() -> None:
    delivery = decide_escalation_delivery(
        target=EscalationTarget.DEVELOPER,
        developer_available=True,
        developer_chat_external_id="U-DEV",
        contact=None,
    )
    assert delivery.action == "send"
    assert delivery.recipient_chat_external_id == "U-DEV"


def test_decide_delivery_developer_unavailable_is_suppressed() -> None:
    delivery = decide_escalation_delivery(
        target=EscalationTarget.DEVELOPER,
        developer_available=False,
        developer_chat_external_id="U-DEV",
        contact=None,
    )
    assert delivery.action == "suppressed_unavailable"
    assert delivery.recipient_chat_external_id is None


def test_decide_delivery_human_target_uses_contact_or_reports_missing() -> None:
    contact = EscalationContact(
        target=EscalationTarget.MANAGER, chat_external_id="U-MGR", display_name="Mia"
    )
    sent = decide_escalation_delivery(
        target=EscalationTarget.MANAGER,
        developer_available=True,
        developer_chat_external_id="U-DEV",
        contact=contact,
    )
    assert sent.action == "send"
    assert sent.recipient_chat_external_id == "U-MGR"
    assert sent.recipient_display_name == "Mia"

    missing = decide_escalation_delivery(
        target=EscalationTarget.SCRUM_MASTER,
        developer_available=True,
        developer_chat_external_id="U-DEV",
        contact=None,
    )
    assert missing.action == "no_contact"


def test_nudge_input_resolved_steps_falls_back_to_single_developer_rung() -> None:
    payload = NudgeInput(
        tenant_id="demo",
        correlation_id="corr-1",
        as_of="2026-01-10",
        reply_wait_seconds=123,
    )
    steps = payload.resolved_steps()
    assert len(steps) == 1
    assert steps[0].target == EscalationTarget.DEVELOPER.value
    assert steps[0].wait_seconds == 123

    step_input = payload.step_input(2, EscalationTarget.MANAGER.value)
    assert step_input.nudge_number == 2
    assert step_input.target == EscalationTarget.MANAGER.value
    assert step_input.correlation_id == "corr-1"


def test_nudge_input_for_daily_checkin_result_propagates_escalation_steps() -> None:
    steps = (
        EscalationStepPayload(target="developer", wait_seconds=100),
        EscalationStepPayload(target="scrum_master", wait_seconds=200),
    )
    result = DailyCheckinResult(
        tenant_id="demo",
        developer_id="dev-1",
        correlation_id="corr-1",
        asked_at="2026-01-10T09:00:00+00:00",
        already_recorded=False,
        status="sent",
        escalation_steps=steps,
    )
    scheduled = DailyCheckinInput(
        tenant_id="demo",
        developer_id="dev-1",
        chat_external_id="U-DEV",
        checkin_date="2026-01-10",
    )
    nudge_input = build_nudge_input(result, scheduled, now=datetime(2026, 1, 10, 9, 0))
    assert nudge_input.escalation_steps == steps
    assert nudge_input.resolved_steps() == steps
