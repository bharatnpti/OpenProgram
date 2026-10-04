"""A reply after its check-in closed is a late update, not a dropped message (G9).

R1 live: Hana's check-in was asked at 21:00 her time (Asia/Tokyo) and closed
unanswered about an hour later. She replied at 15:11 UTC, 00:11 on the next
local day, and the reply was silently discarded: no check-in of that local day
matched it. Now a reply with no check-in of its local day goes to the person's
newest check-in. A closed one records it as a late update for its own day,
marked late and acknowledged once; an open one, such as a newer round's,
reads it as before.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from config.settings import Settings
from core.application.status_collector import LATE_UPDATE_ACK_TEXT, StatusCollector
from core.domain.graph import Developer, EntityRef, NodeKind
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import (
    CheckIn,
    CheckInCorrelation,
    CheckInPreference,
    StatusSource,
    WriteBackConsent,
)
from core.domain.writeback import WriteBackStatus
from core.ports.chat import ChatProvider
from core.ports.issue_tracker import IssueTracker
from core.ports.llm import LlmProvider
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from infra.workflows import nudge
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

_TENANT = "demo"
_HANA = "U-hana"
_DM = "D-hana"
_DAY = date(2026, 10, 3)
_R1_ASKED = datetime(2026, 10, 3, 12, 0, 9, tzinfo=UTC)  # 21:00 in Tokyo
_R2_ASKED = datetime(2026, 10, 3, 18, 0, 8, tzinfo=UTC)  # 03:00 in Tokyo, next day
_LATE_AT = datetime(2026, 10, 3, 15, 11, 20, tzinfo=UTC)  # 00:11 in Tokyo, next day
_LATE_TEXT = (
    "Sorry, late one - IDP-9 SSO onboarding guide not started yet, planning it Monday; no blockers."
)


def _evaluation(
    note: str,
    *,
    claims: list[dict[str, object]] | None = None,
    blockers_answered: bool = True,
    eta_answered: bool = False,
) -> str:
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": True,
            "question": None,
            "signals": {
                "progress_note": note,
                "blockers": [],
                "eta_change_days": None,
                "blockers_answered": blockers_answered,
                "eta_answered": eta_answered,
                "issue_updates": claims or [],
            },
        }
    )


_HANA_LATE = _evaluation(
    "IDP-9 SSO onboarding guide not started yet; planned for Monday. No blockers.",
    claims=[{"issue_key": "IDP-9", "claimed_state": "not started", "note": "planned Monday"}],
)


class _Registry(ServiceRegistry):
    """The memory-mode registry with a scripted model, a recording chat and a fake tracker."""

    def __init__(
        self,
        settings: Settings,
        *,
        texts: list[str],
        issues: dict[str, Issue] | None = None,
    ) -> None:
        super().__init__(settings)
        self.llm = SequenceLlmProvider(texts=texts)
        self.chat = FakeChatProvider()
        self.tracker = FakeIssueTracker(issues=issues or {})

    def llm_provider(self) -> LlmProvider:
        return self.llm

    def chat_provider(self) -> ChatProvider:
        return self.chat

    def issue_tracker(self) -> IssueTracker:
        return self.tracker

    @property
    def store(self) -> InMemoryGraphStore:
        return self._memory_graph_store()


def _idp9(state: IssueState = IssueState.TODO) -> Issue:
    return Issue(
        tenant_id=_TENANT,
        key="IDP-9",
        title="Enterprise SSO onboarding guide",
        state=state,
        assignee=UserRef(tenant_id=_TENANT, external_id=_HANA),
        updated_at=_R1_ASKED,
    )


async def _registry(
    settings: Settings,
    texts: list[str],
    *,
    roles: str = "po",
    timezone: str = "Asia/Tokyo",
    issues: dict[str, Issue] | None = None,
    consent: WriteBackConsent = WriteBackConsent.ALWAYS_ASK,
    writeback: bool = False,
) -> _Registry:
    registry = _Registry(
        settings.model_copy(
            update={
                "chat_provider": "fake",
                "issue_tracker_provider": "fake",
                "llm_provider": "fake",
                "jira_writeback_enabled": writeback,
            }
        ),
        texts=texts,
        issues={"IDP-9": _idp9()} if issues is None else issues,
    )
    store = registry.store
    await store.upsert_node(
        Developer(tenant_id=_TENANT, id=_HANA, name="Hana Kobayashi", metadata={"app_roles": roles})
    )
    await store.record_checkin_preference(
        CheckInPreference(
            tenant_id=_TENANT,
            developer_id=_HANA,
            timezone=timezone,
            write_back_consent=consent,
        )
    )
    return registry


async def _ask(registry: _Registry, correlation_id: str, asked_at: datetime) -> None:
    store = registry.store
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_HANA,
            correlation_id=correlation_id,
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
            checkin_date=_DAY,
        )
    )
    await store.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id=_TENANT,
            correlation_id=correlation_id,
            developer_id=_HANA,
            chat_user_ref=_HANA,
            chat_thread_ref=_DM,
            outbound_message_id=f"out-{correlation_id}",
            asked_at=asked_at,
        )
    )


async def _close(registry: _Registry, correlation_id: str) -> None:
    """The ladder's close-out: nobody answered."""
    await registry.status_collector().record_non_response(
        tenant_id=_TENANT, developer_id=_HANA, as_of=_DAY, correlation_id=correlation_id
    )


def _dm(
    text: str, message_id: str, received_at: datetime, *, thread_id: str = _DM
) -> InboundMessage:
    # As the Slack mapping builds it: a fresh correlation id, the DM as thread.
    return InboundMessage(
        tenant_id=_TENANT,
        user=ChatUserRef(tenant_id=_TENANT, external_id=_HANA),
        text=text,
        thread_id=thread_id,
        message_id=message_id,
        correlation_id=f"inbound-{message_id}",
        received_at=received_at,
    )


async def test_hanas_late_reply_after_close_is_a_late_update_with_one_ack(
    settings: Settings,
) -> None:
    registry = await _registry(settings, [_HANA_LATE])
    await _ask(registry, "corr-r1", _R1_ASKED)
    await _close(registry, "corr-r1")
    store = registry.store
    closed = await store.latest_developer_status(_TENANT, _HANA, _DAY)
    assert closed is not None and closed.source is not StatusSource.CONFIRMED

    result = await registry.process_inbound_message(
        _dm(_LATE_TEXT, "ts-late-1", _LATE_AT), allow_reprocess=True
    )
    again = await registry.process_inbound_message(
        _dm("Also: the outline is in the wiki.", "ts-late-2", _LATE_AT + timedelta(minutes=9)),
        allow_reprocess=True,
    )

    assert result.status == "processed"
    # The day of the check-in, not the reply's local day (already the 4th in Tokyo).
    status = await store.latest_developer_status(_TENANT, _HANA, _DAY)
    assert status is not None
    assert status.as_of == _DAY
    # A product owner with no issue under way owes no ETA (N16).
    assert status.source is StatusSource.CONFIRMED
    assert status.summary.startswith("Late update 15:11 UTC: ")
    assert "IDP-9" in status.summary
    checkin = await store.checkin_by_correlation(_TENANT, "corr-r1")
    assert checkin is not None
    assert checkin.replied_at == _LATE_AT
    facts = await store.list_facts(
        _TENANT, EntityRef(tenant_id=_TENANT, kind=NodeKind.DEVELOPER, id=_HANA)
    )
    (fact,) = [fact for fact in facts if fact.source == "checkin"]
    assert fact.payload["late_update"] is True
    assert fact.payload["late_update_at"] == _LATE_AT.isoformat()
    # One ack, and a later message on the answered check-in is a duplicate.
    assert [message.text for message in registry.chat.sent] == [LATE_UPDATE_ACK_TEXT]
    assert again.status == "duplicate"
    # No nudge, escalation or follow-up: one model reading, one DM.
    assert len(registry.llm.requests) == 1


async def test_a_late_reply_the_same_local_day_is_a_late_update_too(settings: Settings) -> None:
    registry = await _registry(settings, [_HANA_LATE], timezone="UTC")
    await _ask(registry, "corr-r1", _R1_ASKED)
    await _close(registry, "corr-r1")

    result = await registry.process_inbound_message(
        _dm(_LATE_TEXT, "ts-late-1", _LATE_AT), allow_reprocess=True
    )

    assert result.status == "processed"
    status = await registry.store.latest_developer_status(_TENANT, _HANA, _DAY)
    assert status is not None
    assert status.summary.startswith("Late update 15:11 UTC: ")
    assert [message.text for message in registry.chat.sent] == [LATE_UPDATE_ACK_TEXT]


async def test_a_reply_while_a_newer_checkin_is_open_goes_to_the_newer_one(
    settings: Settings,
) -> None:
    registry = await _registry(
        settings,
        [
            _evaluation(
                "Started the IDP-9 outline today; on track. No blockers.",
                claims=[{"issue_key": "IDP-9", "claimed_state": "started", "note": "outline"}],
            )
        ],
    )
    await _ask(registry, "corr-r1", _R1_ASKED)
    await _close(registry, "corr-r1")
    await _ask(registry, "corr-r2", _R2_ASKED)

    result = await registry.process_inbound_message(
        _dm(
            "Got it - I've started the outline for IDP-9 today. On track. No blockers.",
            "ts-r2",
            _R2_ASKED + timedelta(minutes=4),
        ),
        allow_reprocess=True,
    )

    assert result.status == "processed"
    store = registry.store
    r1 = await store.checkin_by_correlation(_TENANT, "corr-r1")
    r2 = await store.checkin_by_correlation(_TENANT, "corr-r2")
    assert r1 is not None and r1.replied_at is None
    assert r2 is not None and r2.replied_at is not None
    status = await store.latest_developer_status(_TENANT, _HANA, _DAY)
    assert status is not None
    assert not status.summary.startswith("Late update")
    # The on-time ack, not the late one.
    assert [message.text for message in registry.chat.sent] != [LATE_UPDATE_ACK_TEXT]
    assert len(registry.chat.sent) == 1


async def test_a_reply_across_local_midnight_reaches_the_open_checkin(
    settings: Settings,
) -> None:
    """Asked 23:30 in Kolkata, answered 00:10: the open check-in reads it, not late."""
    registry = await _registry(
        settings,
        [_evaluation("IDP-9 on track, no blockers.", eta_answered=True)],
        timezone="Asia/Kolkata",
        roles="dev",
    )
    await _ask(registry, "corr-r2", _R2_ASKED)

    result = await registry.process_inbound_message(
        _dm(
            "IDP-9 on track, done Friday, no blockers.", "ts-r2", _R2_ASKED + timedelta(minutes=40)
        ),
        allow_reprocess=True,
    )

    assert result.status == "processed"
    r2 = await registry.store.checkin_by_correlation(_TENANT, "corr-r2")
    assert r2 is not None and r2.replied_at is not None
    # Read as an on-time reply (no schedule run here, so its day is the reply's).
    status = await registry.store.latest_developer_status(_TENANT, _HANA, _DAY + timedelta(days=1))
    assert status is not None
    assert not status.summary.startswith("Late update")
    assert [message.text for message in registry.chat.sent] != [LATE_UPDATE_ACK_TEXT]


async def test_a_late_reply_without_a_status_changes_nothing(settings: Settings) -> None:
    registry = await _registry(settings, [])
    await _ask(registry, "corr-r1", _R1_ASKED)
    await _close(registry, "corr-r1")
    before = await registry.store.latest_developer_status(_TENANT, _HANA, _DAY)

    result = await registry.process_inbound_message(
        _dm("thanks", "ts-thanks", _LATE_AT), allow_reprocess=True
    )

    assert result.status == "ignored"
    assert await registry.store.latest_developer_status(_TENANT, _HANA, _DAY) == before
    assert registry.chat.sent == []
    # Kept on the record, not discarded.
    assert await registry.store.user_turn_exists(_TENANT, _HANA, "ts-thanks")


async def test_a_late_thread_reply_is_not_taken(settings: Settings) -> None:
    registry = await _registry(settings, [])
    await _ask(registry, "corr-r1", _R1_ASKED)
    await _close(registry, "corr-r1")

    result = await registry.process_inbound_message(
        _dm(_LATE_TEXT, "ts-thread", _LATE_AT, thread_id="ts-some-request-dm"),
        allow_reprocess=True,
    )

    assert result.status == "ignored"
    assert registry.chat.sent == []


async def test_an_old_checkin_takes_no_late_reply(settings: Settings) -> None:
    registry = await _registry(settings, [])
    await _ask(registry, "corr-r1", _R1_ASKED)
    await _close(registry, "corr-r1")

    result = await registry.process_inbound_message(
        _dm(_LATE_TEXT, "ts-week-later", _R1_ASKED + timedelta(days=7)),
        allow_reprocess=True,
    )

    assert result.status == "ignored"


async def test_late_claims_are_written_through_the_usual_gates(settings: Settings) -> None:
    """An auto_apply owner's late claim is written; the ack names the update."""
    registry = await _registry(
        settings,
        [
            _evaluation(
                "IDP-9 started.",
                claims=[{"issue_key": "IDP-9", "claimed_state": "in progress", "note": ""}],
            )
        ],
        roles="dev",
        timezone="UTC",
        consent=WriteBackConsent.AUTO_APPLY,
        writeback=True,
    )
    await _ask(registry, "corr-r1", _R1_ASKED)
    await _close(registry, "corr-r1")

    await registry.process_inbound_message(
        _dm("IDP-9 started, ETA Friday, no blockers.", "ts-late", _LATE_AT), allow_reprocess=True
    )

    assert registry.tracker.transitions == [(_TENANT, "IDP-9", "in_progress")]
    (ack,) = registry.chat.sent
    assert ack.text.startswith(LATE_UPDATE_ACK_TEXT)
    assert "I updated IDP-9" in ack.text


async def test_an_always_ask_persons_late_claims_are_not_proposed(settings: Settings) -> None:
    """A consent question after the close could not be answered: nothing is written."""
    registry = await _registry(
        settings,
        [
            _evaluation(
                "IDP-9 started.",
                claims=[{"issue_key": "IDP-9", "claimed_state": "in progress", "note": ""}],
            )
        ],
        roles="dev",
        timezone="UTC",
        writeback=True,
    )
    await _ask(registry, "corr-r1", _R1_ASKED)
    await _close(registry, "corr-r1")

    await registry.process_inbound_message(
        _dm("IDP-9 started, ETA Friday, no blockers.", "ts-late", _LATE_AT), allow_reprocess=True
    )

    assert registry.tracker.transitions == []
    rows = await registry.writeback_audit_repository().list_writeback_by_correlation(
        _TENANT, "corr-r1"
    )
    assert [row for row in rows if row.status is WriteBackStatus.PROPOSED] == []
    assert [message.text for message in registry.chat.sent] == [LATE_UPDATE_ACK_TEXT]


async def test_the_close_out_closes_the_checkin(settings: Settings) -> None:
    registry = await _registry(settings, [])
    await _ask(registry, "corr-r1", _R1_ASKED)

    await _close(registry, "corr-r1")

    correlation = await registry.store.checkin_correlation_by_id(_TENANT, "corr-r1")
    assert correlation is not None and correlation.consumed_at is not None


class _NudgeRegistry:
    def __init__(self, store: InMemoryGraphStore, collector: StatusCollector) -> None:
        self._store = store
        self._collector = collector

    def status_repository(self) -> InMemoryGraphStore:
        return self._store

    def status_collector(self) -> StatusCollector:
        return self._collector

    async def close(self) -> None:
        return None


async def test_a_second_closeout_of_the_day_closes_its_checkin_too(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day already reads unknown from an earlier round: this check-in closes as well."""
    registry = await _registry(settings, [])
    await _ask(registry, "corr-r1", _R1_ASKED)
    await _close(registry, "corr-r1")
    await _ask(registry, "corr-r2", _R2_ASKED)
    monkeypatch.setattr(
        nudge,
        "_service_registry",
        lambda: _NudgeRegistry(registry.store, registry.status_collector()),
    )

    result = await nudge.close_checkin_non_response_activity(
        nudge.NudgeInput(tenant_id=_TENANT, correlation_id="corr-r2", as_of=_DAY.isoformat())
    )

    assert result.status == "already_closed"
    correlation = await registry.store.checkin_correlation_by_id(_TENANT, "corr-r2")
    assert correlation is not None and correlation.consumed_at is not None
