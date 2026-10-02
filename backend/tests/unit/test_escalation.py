from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from core.application.status_collector import StatusCollector
from core.domain.escalation import (
    EscalationContact,
    EscalationTarget,
    PodEscalationContacts,
    apply_escalation_contacts_to_metadata,
    default_escalation_policy,
    escalation_contacts_from_metadata,
    with_member_identity,
)
from core.domain.graph import Developer, EdgeKind, GraphEdge, JsonScalar, Pod
from core.domain.identity import IdentityLink
from core.domain.status import CheckIn
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.workflows import nudge
from infra.workflows.daily_checkin import DailyCheckinInput, DailyCheckinResult
from infra.workflows.daily_checkin import (
    nudge_input_for_daily_checkin_result as build_nudge_input,
)
from infra.workflows.nudge import (
    EscalationStepPayload,
    NudgeInput,
    decide_escalation_delivery,
)
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker, FakeLlmProvider


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
    assert contacts.scrum_master.member_id is None
    assert contacts.manager is None


def test_escalation_contacts_metadata_carries_the_member_reference() -> None:
    metadata: dict[str, JsonScalar] = {}
    apply_escalation_contacts_to_metadata(
        metadata,
        PodEscalationContacts(
            manager=EscalationContact(
                target=EscalationTarget.MANAGER,
                chat_external_id="U-MGR",
                display_name="Mia",
                member_id="mia",
            )
        ),
    )
    assert metadata["escalation_manager_member_id"] == "mia"
    assert metadata["escalation_sm_member_id"] is None

    contacts = escalation_contacts_from_metadata(metadata)
    assert contacts.manager is not None
    assert contacts.manager.member_id == "mia"
    assert contacts.scrum_master is None


def test_with_member_identity_follows_the_link_and_falls_back_to_the_stored_chat_id() -> None:
    picked = EscalationContact(
        target=EscalationTarget.SCRUM_MASTER,
        chat_external_id="U-OLD",
        display_name="Sam",
        member_id="sam",
    )
    moved = with_member_identity(picked, member_name="Sam Renamed", chat_user_id="U-NEW")
    assert moved.chat_external_id == "U-NEW"
    assert moved.display_name == "Sam Renamed"

    assert with_member_identity(picked, member_name="Sam", chat_user_id=None) == picked
    assert with_member_identity(picked, member_name=None, chat_user_id="U-NEW") == picked
    legacy = EscalationContact(target=EscalationTarget.SCRUM_MASTER, chat_external_id="U-OLD")
    assert with_member_identity(legacy, member_name="Sam", chat_user_id="U-NEW") == legacy


class _EscalationRegistry:
    def __init__(self, store: InMemoryGraphStore, collector: StatusCollector) -> None:
        self._store = store
        self._collector = collector

    def status_repository(self) -> InMemoryGraphStore:
        return self._store

    def graph_repository(self) -> InMemoryGraphStore:
        return self._store

    def identity_link_repository(self) -> InMemoryGraphStore:
        return self._store

    def status_collector(self) -> StatusCollector:
        return self._collector

    async def close(self) -> None:
        return None


@pytest.mark.parametrize(
    ("pod_metadata", "expected_message_id"),
    [
        # Picked from a member: delivered to the chat id the member's link holds now.
        (
            {
                "escalation_sm_member_id": "sam",
                "escalation_sm_chat_external_id": "U-SM-SAVED",
                "escalation_sm_display_name": "Sam",
            },
            "msg-U-SM-NOW-1",
        ),
        # Saved as a bare chat id before contacts were picked: still delivered there.
        ({"escalation_sm_chat_external_id": "U-LEGACY"}, "msg-U-LEGACY-1"),
    ],
)
async def test_escalation_step_delivers_to_the_member_backed_contact(
    monkeypatch: pytest.MonkeyPatch,
    pod_metadata: dict[str, JsonScalar],
    expected_message_id: str,
) -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(Pod(tenant_id="demo", id="pod-1", name="Pod", metadata=pod_metadata))
    await store.upsert_node(Developer(tenant_id="demo", id="dev-1", name="Dev"))
    await store.upsert_node(Developer(tenant_id="demo", id="sam", name="Sam"))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-1",
            to_node_id="dev-1",
            kind=EdgeKind.CONTAINS,
            valid_from=date(2026, 1, 1),
        )
    )
    await store.upsert_identity_link(
        IdentityLink(tenant_id="demo", developer_id="sam", chat_user_id="U-SM-NOW")
    )
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-esc",
            asked_at=datetime(2026, 1, 12, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=FakeLlmProvider(),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )
    registry = _EscalationRegistry(store, collector)
    monkeypatch.setattr(nudge, "_service_registry", lambda: registry)
    payload = NudgeInput(tenant_id="demo", correlation_id="corr-esc", as_of="2026-01-12")

    result = await nudge.send_escalation_step_activity(
        payload.step_input(2, EscalationTarget.SCRUM_MASTER.value)
    )

    assert result.status == "escalated"
    assert result.nudge_message_id == expected_message_id


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
