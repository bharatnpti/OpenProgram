"""What the bot says around a write-back (R1-5): never "update Jira yourself" for an
update OpenProgram is about to make, and say what it did update."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.application import status_collector as status_collector_module
from core.application.status_collector import StatusCollector, asks_person_to_update_tracker
from core.application.writeback_service import WriteBackService
from core.domain.identity import IdentityLink
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import (
    CheckIn,
    CheckInCorrelation,
    CheckInPreference,
    CheckInSignals,
    WriteBackConsent,
)
from core.domain.writeback import WriteBackAudit, WriteBackStatus
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker

_TENANT = "qa2"
_NOAH = "U-NOAH"
_CORRELATION = "corr-noah"
# The R1 follow-up Noah got after "Go ahead and mark IDP-5 Done" (12:20:28).
_R1_QUESTION = (
    "IDP-5 is still marked In Progress in Jira. Can you update the Jira ticket to Done "
    "or clarify if there is any remaining work?"
)
# The R1 follow-up before it (12:16:00): a statement plus a question, no ask to edit Jira.
_R1_CONFIRM_QUESTION = (
    "IDP-5 is still marked in progress in Jira. Can you confirm if all acceptance "
    "criteria are met and it's ready to close?"
)


@dataclass
class _ScriptedLlm:
    texts: list[str]
    requests: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            tenant_id=request.tenant_id,
            text=self.texts.pop(0),
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0.0, latency_ms=1.0
            ),
            trace_id=f"trace-{len(self.requests)}",
        )


def _evaluator_asks_to_update_jira() -> str:
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": False,
            "question": _R1_QUESTION,
            "signals": {
                "progress_note": "IDP-5 merged on identity-service !2; ready to close.",
                "blockers": [],
                "eta_change_days": None,
                "blockers_answered": True,
                "eta_answered": True,
                "issue_updates": [
                    {
                        "issue_key": "IDP-5",
                        "claimed_done": True,
                        "claimed_state": "ready to close",
                        "note": "Fix merged, all acceptance criteria met.",
                    }
                ],
            },
        }
    )


async def _noah_collector(
    *,
    consent: WriteBackConsent,
    assignee: str = "acct-noah",
) -> tuple[StatusCollector, FakeIssueTracker, FakeChatProvider, _ScriptedLlm]:
    store = InMemoryGraphStore()
    asked_at = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_NOAH,
            correlation_id=_CORRELATION,
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id=_TENANT,
            developer_id=_NOAH,
            correlation_id=_CORRELATION,
            chat_user_ref=_NOAH,
            chat_thread_ref="thread-noah",
            outbound_message_id="msg-question",
            asked_at=asked_at,
        )
    )
    await store.record_checkin_preference(
        CheckInPreference(tenant_id=_TENANT, developer_id=_NOAH, write_back_consent=consent)
    )
    await store.upsert_identity_link(
        IdentityLink(tenant_id=_TENANT, developer_id=_NOAH, jira_account_id="acct-noah")
    )
    await store.set_writeback_enabled(_TENANT, True)
    tracker = FakeIssueTracker(
        issues={
            "IDP-5": Issue(
                tenant_id=_TENANT,
                key="IDP-5",
                title="Login loop on expired refresh tokens",
                state=IssueState.IN_PROGRESS,
                assignee=UserRef(tenant_id=_TENANT, external_id=assignee),
            )
        }
    )
    chat = FakeChatProvider()
    llm = _ScriptedLlm(texts=[_evaluator_asks_to_update_jira()])
    collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=chat,
        llm_provider=llm,
        status_repository=store,
        conversation_repository=store,
        identity_link_repository=store,
        write_back_service=WriteBackService(
            issue_tracker=tracker,
            audit_repository=store,
            config_repository=store,
            status_repository=store,
            identity_link_repository=store,
        ),
        model="test-model",
    )
    return collector, tracker, chat, llm


def _noah_reply() -> InboundMessage:
    return InboundMessage(
        tenant_id=_TENANT,
        user=ChatUserRef(tenant_id=_TENANT, external_id=_NOAH),
        text="Yes, all acceptance criteria are met. Go ahead and mark IDP-5 Done.",
        thread_id="thread-noah",
        message_id="msg-reply",
        correlation_id=_CORRELATION,
        received_at=datetime(2026, 10, 3, 12, 16, tzinfo=UTC),
    )


def _sent(chat: FakeChatProvider, purpose: str) -> list[str]:
    return [message.text for message in chat.sent if message.metadata.get("purpose") == purpose]


async def test_auto_apply_owner_is_not_told_to_update_jira_and_hears_it_was_done() -> None:
    collector, tracker, chat, llm = await _noah_collector(consent=WriteBackConsent.AUTO_APPLY)

    outcome = await collector.handle_reply(_noah_reply())

    # No "update the Jira ticket" follow-up: OpenProgram moves IDP-5 itself.
    assert _sent(chat, "status_clarification") == []
    assert outcome.kind == "processed"
    assert tracker.transitions == [(_TENANT, "IDP-5", "done")]
    acks = _sent(chat, "status_ack")
    assert len(acks) == 1
    assert "I updated IDP-5 to Done in the issue tracker." in acks[0]
    assert not asks_person_to_update_tracker(acks[0])
    # The evaluator was told that tracker updates are OpenProgram's job here.
    assert "never ask them to update the tracker" in llm.requests[0].prompt


async def test_always_ask_person_keeps_the_follow_up_and_nothing_is_written() -> None:
    collector, tracker, chat, llm = await _noah_collector(consent=WriteBackConsent.ALWAYS_ASK)

    outcome = await collector.handle_reply(_noah_reply())

    assert outcome.kind == "clarifying"
    assert _sent(chat, "status_clarification") == [_R1_QUESTION]
    assert tracker.transitions == []
    assert "never ask them to update the tracker" not in llm.requests[0].prompt


async def test_follow_up_is_kept_when_the_issue_is_someone_elses() -> None:
    collector, tracker, chat, _ = await _noah_collector(
        consent=WriteBackConsent.AUTO_APPLY, assignee="acct-sofia"
    )

    outcome = await collector.handle_reply(_noah_reply())

    assert outcome.kind == "clarifying"
    assert _sent(chat, "status_clarification") == [_R1_QUESTION]
    assert tracker.transitions == []


def test_asks_person_to_update_tracker_only_for_a_request_to_edit_it() -> None:
    assert asks_person_to_update_tracker(_R1_QUESTION)
    assert asks_person_to_update_tracker("Please move CHK-3 to In Review on the board.")
    assert asks_person_to_update_tracker("Could you close the ticket once it's deployed?")
    # Statements about the tracker, or questions that ask for something else.
    assert not asks_person_to_update_tracker(_R1_CONFIRM_QUESTION)
    assert not asks_person_to_update_tracker("What is your ETA for IDP-3?")
    assert not asks_person_to_update_tracker("Jira shows CHK-6 as In Progress.")


def test_ack_names_applied_updates_within_the_cap() -> None:
    signals = CheckInSignals(progress_note="merged", parser_confident=True)
    applied = WriteBackAudit(
        id="wb-1",
        tenant_id=_TENANT,
        developer_id=_NOAH,
        issue_key="IDP-5",
        correlation_id=_CORRELATION,
        status=WriteBackStatus.APPLIED,
        target_state="done",
        before_state="in_progress",
        after_state="done",
        created_at=datetime(2026, 10, 3, 12, 16, tzinfo=UTC),
    )

    text = status_collector_module._compose_checkin_ack_text(
        signals=signals, max_chars=320, applied=[applied]
    )
    plain = status_collector_module._compose_checkin_ack_text(signals=signals, max_chars=320)
    short = status_collector_module._compose_checkin_ack_text(
        signals=signals, max_chars=60, applied=[applied]
    )

    note = "I updated IDP-5 to Done in the issue tracker."
    assert text == f"{status_collector_module._CHECKIN_ACK_PLAIN} {note}"
    assert plain == status_collector_module._CHECKIN_ACK_PLAIN
    assert len(short) <= 60


def test_consent_prompt_names_the_state_not_the_canonical_value() -> None:
    proposal = WriteBackAudit(
        id="wb-2",
        tenant_id=_TENANT,
        developer_id="U-LIAM",
        issue_key="CHK-4",
        correlation_id="corr-liam",
        status=WriteBackStatus.PROPOSED,
        target_state="in_progress",
        before_state="todo",
        after_state="in_progress",
        created_at=datetime(2026, 10, 3, 12, 15, tzinfo=UTC),
    )

    text = status_collector_module._compose_consent_prompt_text([proposal])

    assert text == "Want me to update CHK-4 to “In Progress” in the issue tracker? Reply yes or no."
