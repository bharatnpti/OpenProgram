from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from core.application.availability import AvailabilityService
from core.application.status_collector import StatusCollector
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.directory import DirectoryUser
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
from core.domain.inbound import InboundChatEvent, conversation_key
from core.domain.status import CheckIn, CheckInClarification, CheckInCorrelation
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.workflows import nudge
from infra.workflows.daily_checkin import DailyCheckinInput, DailyCheckinResult
from infra.workflows.daily_checkin import (
    nudge_input_for_daily_checkin_result as build_nudge_input,
)
from infra.workflows.nudge import (
    EscalationStepPayload,
    NudgeInput,
    NudgeResult,
    decide_escalation_delivery,
)
from tests.contract.fakes import (
    FakeCalendarProvider,
    FakeChatProvider,
    FakeIssueTracker,
    FakeLlmProvider,
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
        self.settings = SimpleNamespace(tenant_default_timezone="UTC")

    def status_repository(self) -> InMemoryGraphStore:
        return self._store

    def graph_repository(self) -> InMemoryGraphStore:
        return self._store

    def identity_link_repository(self) -> InMemoryGraphStore:
        return self._store

    def inbound_chat_event_repository(self) -> InMemoryGraphStore:
        return self._store

    def availability_service(self) -> AvailabilityService:
        return AvailabilityService(FakeCalendarProvider())

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


# A person who has answered the open check-in is never nudged or escalated as a
# non-responder, even while a clarification is still pending. Silence still goes
# up the whole ladder.

_ASKED_AT = datetime(2026, 1, 12, 9, 0, tzinfo=UTC)
_CHECKIN_TS = "1768208400.000100"
_LADDER = (
    EscalationStepPayload(target=EscalationTarget.DEVELOPER.value, wait_seconds=900),
    EscalationStepPayload(target=EscalationTarget.SCRUM_MASTER.value, wait_seconds=600),
    EscalationStepPayload(target=EscalationTarget.MANAGER.value, wait_seconds=600),
)
_LADDER_PURPOSES = {"status_nudge", "status_escalation"}


async def _open_checkin_in_pod_with_contacts(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[InMemoryGraphStore, FakeChatProvider, NudgeInput]:
    """An open check-in for U-DEV, whose pod's SM and manager were picked from members."""
    store = InMemoryGraphStore()
    await store.upsert_node(
        Pod(
            tenant_id="demo",
            id="pod-1",
            name="Pod",
            metadata={
                "escalation_sm_member_id": "sam",
                "escalation_sm_chat_external_id": "U-SM",
                "escalation_sm_display_name": "Sam Ortiz",
                "escalation_manager_member_id": "mia",
                "escalation_manager_chat_external_id": "U-MGR",
                "escalation_manager_display_name": "Mia Kovacs",
            },
        )
    )
    for member_id, name in (("U-DEV", "Rosa Lind"), ("sam", "Sam Ortiz"), ("mia", "Mia Kovacs")):
        await store.upsert_node(Developer(tenant_id="demo", id=member_id, name=name))
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-1",
            to_node_id="U-DEV",
            kind=EdgeKind.CONTAINS,
            valid_from=date(2026, 1, 1),
        )
    )
    for member_id, chat_id in (("sam", "U-SM"), ("mia", "U-MGR")):
        await store.upsert_identity_link(
            IdentityLink(tenant_id="demo", developer_id=member_id, chat_user_id=chat_id)
        )
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="U-DEV",
            correlation_id="corr-esc",
            asked_at=_ASKED_AT,
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id="demo",
            correlation_id="corr-esc",
            developer_id="U-DEV",
            chat_user_ref="U-DEV",
            chat_thread_ref="D-DEV",
            outbound_message_id=_CHECKIN_TS,
            asked_at=_ASKED_AT,
        )
    )
    chat = FakeChatProvider()
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=FakeLlmProvider(),
        status_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
    )
    registry = _EscalationRegistry(store, collector)
    monkeypatch.setattr(nudge, "_service_registry", lambda: registry)
    # As the scheduled fan-out builds it: no developer name.
    payload = NudgeInput(
        tenant_id="demo",
        correlation_id="corr-esc",
        as_of="2026-01-12",
        escalation_steps=_LADDER,
    )
    return store, chat, payload


async def _turn(
    store: InMemoryGraphStore,
    *,
    role: ConversationRole,
    content: str,
    minutes: int,
    correlation_id: str = "corr-esc",
) -> None:
    observed_at = _ASKED_AT + timedelta(minutes=minutes)
    await store.append_turn(
        ConversationTurn(
            tenant_id="demo",
            developer_id="U-DEV",
            conversation_id=correlation_id,
            conversation_date=observed_at.date(),
            role=role,
            content=content,
            correlation_id=correlation_id,
            chat_message_id=f"ts-{correlation_id}-{minutes}",
            observed_at=observed_at,
        )
    )


async def _reply_with_unanswered_clarification(store: InMemoryGraphStore) -> None:
    """The person replied; the follow-up question the bot asked is still unanswered."""
    await _turn(store, role=ConversationRole.USER, content="Cache work is in review.", minutes=15)
    await store.record_checkin_clarification(
        CheckInClarification(
            tenant_id="demo",
            correlation_id="corr-esc",
            clarification_number=1,
            question="What is your ETA?",
            sent_at=_ASKED_AT + timedelta(minutes=16),
            outbound_message_id="ts-clarification-1",
        )
    )
    await _turn(store, role=ConversationRole.AGENT, content="What is your ETA?", minutes=16)


async def _run_ladder(payload: NudgeInput) -> tuple[list[str], NudgeResult | None]:
    """The rung loop of the DBOS and Temporal nudge workflows, without the sleeps."""
    statuses: list[str] = []
    for number, step in enumerate(payload.resolved_steps(), start=1):
        result = await nudge.send_escalation_step_activity(payload.step_input(number, step.target))
        statuses.append(result.status)
        if result.status == "already_replied":
            return statuses, None
    return statuses, await nudge.close_checkin_non_response_activity(payload)


@pytest.mark.parametrize("target", list(EscalationTarget))
def test_decide_delivery_suppresses_every_rung_once_the_developer_replied(
    target: EscalationTarget,
) -> None:
    contact = EscalationContact(target=target, chat_external_id="U-MGR", display_name="Mia")
    delivery = decide_escalation_delivery(
        target=target,
        developer_available=True,
        developer_chat_external_id="U-DEV",
        contact=contact,
        developer_replied=True,
    )
    assert delivery.action == "suppressed_replied"
    assert delivery.recipient_chat_external_id is None


async def test_ladder_does_not_escalate_a_reply_whose_clarification_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, chat, payload = await _open_checkin_in_pod_with_contacts(monkeypatch)
    await _reply_with_unanswered_clarification(store)

    statuses, closed = await _run_ladder(payload)

    # No reminder to them, and no "hasn't completed" notice to the SM or manager.
    assert statuses == ["suppressed_replied"] * 3
    assert [m for m in chat.sent if m.metadata.get("purpose") in _LADDER_PURPOSES] == []
    for number in (1, 2, 3):
        assert await store.checkin_nudge_for("demo", "corr-esc", number) is None
    # The ladder still ran to its close-out, which finalized their reply.
    assert closed is not None
    assert closed.status == "closed"
    assert closed.terminal_source == "confirmed"
    checkin = await store.checkin_by_correlation("demo", "corr-esc")
    assert checkin is not None
    assert checkin.replied_at == _ASKED_AT + timedelta(minutes=15)


async def test_ladder_still_nudges_and_escalates_someone_who_never_replied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, chat, payload = await _open_checkin_in_pod_with_contacts(monkeypatch)
    # Only a reply to another check-in is on record, which does not answer this one.
    await _turn(
        store,
        role=ConversationRole.USER,
        content="Yesterday's work is done.",
        minutes=5,
        correlation_id="corr-other",
    )

    statuses, closed = await _run_ladder(payload)

    assert statuses == ["nudged", "escalated", "escalated"]
    assert [message.metadata["purpose"] for message in chat.sent] == [
        "status_nudge",
        "status_escalation",
        "status_escalation",
    ]
    for message in chat.sent[1:]:
        assert "hasn't completed today's check-in" in message.text
    nudges = [await store.checkin_nudge_for("demo", "corr-esc", number) for number in (1, 2, 3)]
    assert [sent.outbound_message_id if sent else None for sent in nudges] == [
        "msg-U-DEV-1",
        "msg-U-SM-2",
        "msg-U-MGR-3",
    ]
    # Silence is never read as fine.
    assert closed is not None
    assert closed.terminal_source == "unknown"


@pytest.mark.parametrize(
    ("thread_ref", "minutes", "expected"),
    [
        # Top-level reply in the DM, still waiting for the coalesce debounce or sweeper.
        ("D-DEV", 24, "suppressed_replied"),
        # Reply in a thread on the check-in message, also still buffered.
        (_CHECKIN_TS, 24, "suppressed_replied"),
        # A message left from before this check-in was asked does not answer it.
        ("D-DEV", -60, "escalated"),
    ],
)
async def test_escalation_step_counts_a_reply_still_waiting_in_the_inbound_buffer(
    monkeypatch: pytest.MonkeyPatch,
    thread_ref: str,
    minutes: int,
    expected: str,
) -> None:
    store, chat, payload = await _open_checkin_in_pod_with_contacts(monkeypatch)
    await store.append(
        InboundChatEvent(
            tenant_id="demo",
            provider="slack",
            event_id=f"evt-{minutes}",
            conversation_key=conversation_key("demo", thread_ref),
            chat_user_ref="U-DEV",
            chat_thread_ref=thread_ref,
            message_ref=f"ts-{minutes}",
            text="CHK-1 is in review.",
            correlation_id="req-1",
            received_at=_ASKED_AT + timedelta(minutes=minutes),
        )
    )

    result = await nudge.send_escalation_step_activity(
        payload.step_input(2, EscalationTarget.SCRUM_MASTER.value)
    )

    assert result.status == expected
    assert len(chat.sent) == (1 if expected == "escalated" else 0)


# The escalation notice names the person by display name, never by a raw chat id.


async def test_escalation_notice_names_the_member_not_their_chat_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The scheduled fan-out gives the ladder no developer name.
    _, chat, payload = await _open_checkin_in_pod_with_contacts(monkeypatch)
    assert payload.developer_name is None

    for number, target in ((2, EscalationTarget.SCRUM_MASTER), (3, EscalationTarget.MANAGER)):
        result = await nudge.send_escalation_step_activity(payload.step_input(number, target.value))
        assert result.status == "escalated"

    assert [message.text for message in chat.sent] == [
        "Heads up: Rosa Lind hasn't completed today's check-in yet. "
        "You're notified as the scrum master so you can follow up if needed.",
        "Heads up: Rosa Lind hasn't completed today's check-in yet. "
        "You're notified as the manager so you can follow up if needed.",
    ]
    assert all("U-DEV" not in message.text for message in chat.sent)


@pytest.mark.parametrize(
    ("developer_name", "member_name", "directory_name", "expected"),
    [
        # No name supplied: the member record's display name.
        (None, "Rosa Lind", "Rosa L.", "Rosa Lind"),
        # The "name" supplied is only the person's chat id.
        ("U-DEV", "Rosa Lind", None, "Rosa Lind"),
        # A real name supplied by the caller is kept.
        ("Rosa", "Rosa Lind", None, "Rosa"),
        # No member record: the chat directory's display name.
        (None, None, "Rosa L.", "Rosa L."),
        # The member record holds only the id, and nothing else knows a name.
        (None, "U-DEV", None, "a team member"),
        (None, None, None, "a team member"),
    ],
)
async def test_escalation_notice_falls_back_to_a_neutral_phrase_never_an_id(
    developer_name: str | None,
    member_name: str | None,
    directory_name: str | None,
    expected: str,
) -> None:
    store = InMemoryGraphStore()
    if member_name is not None:
        await store.upsert_node(Developer(tenant_id="demo", id="U-DEV", name=member_name))
    if directory_name is not None:
        await store.upsert_users(
            [DirectoryUser(tenant_id="demo", external_id="U-DEV", display_name=directory_name)]
        )
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="U-DEV",
            correlation_id="corr-name",
            asked_at=_ASKED_AT,
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    chat = FakeChatProvider()
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=FakeLlmProvider(),
        status_repository=store,
        conversation_repository=store,
        graph_repository=store,
        directory_repository=InMemoryDirectoryUserRepository(store),
        model="test-model",
    )

    await collector.send_nudge(
        tenant_id="demo",
        correlation_id="corr-name",
        developer_name=developer_name,
        chat_external_id="U-DEV",
        nudge_number=2,
        target=EscalationTarget.SCRUM_MASTER,
        recipient_chat_external_id="U-SM",
    )

    assert len(chat.sent) == 1
    assert chat.sent[0].text.startswith(f"Heads up: {expected} hasn't completed")
    assert "U-DEV" not in chat.sent[0].text
