"""A check-in summary keeps the issue keys the person named (V2 B1d).

V2 live: Hana replied about IDP-9, "about half done, no blockers", and the
model's progress note, stored as her summary, read "About half done.": her card
said nothing about which task.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from core.application.status_collector import StatusCollector
from core.application.status_parsing import ClarificationEvaluator, StatusParser
from core.domain.graph import Developer
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CheckIn, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

# Hana's V2 reply, word for word.
_HANA_V2 = (
    "Hi! IDP-9 (Enterprise SSO onboarding guide) is in progress, about half done. No "
    "blockers, ETA hasn't changed. No MR for it, it's docs only."
)
_TENANT = "demo"
_HANA = "U-hana"
_DAY = date(2026, 10, 11)
_ASKED_AT = datetime(2026, 10, 11, 4, 52, tzinfo=UTC)


def _signals(note: str, *, claims: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "progress_note": note,
        "blockers": [],
        "eta_change_days": None,
        "blockers_answered": True,
        "eta_answered": True,
        "issue_updates": claims or [],
    }


def _sufficient(note: str, *, claims: list[dict[str, object]] | None = None) -> str:
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": True,
            "question": None,
            "signals": _signals(note, claims=claims),
        }
    )


async def _parsed_note(note: str, reply: str) -> str:
    llm = SequenceLlmProvider(texts=[json.dumps(_signals(note))])
    signals = await StatusParser(llm, model="test-model").parse_reply(
        tenant_id=_TENANT, developer_id=_HANA, raw_reply=reply, correlation_id="corr-hana"
    )
    return signals.progress_note


async def test_hanas_v2_summary_names_idp_9() -> None:
    assert await _parsed_note("About half done.", _HANA_V2) == "IDP-9: About half done."


async def test_the_evaluators_note_names_the_issue_too() -> None:
    llm = SequenceLlmProvider(texts=[_sufficient("About half done.")])
    decision = await ClarificationEvaluator(llm, model="test-model").evaluate(
        tenant_id=_TENANT, developer_id=_HANA, raw_reply=_HANA_V2, correlation_id="corr-hana"
    )

    assert decision.signals is not None
    assert decision.signals.progress_note == "IDP-9: About half done."


@pytest.mark.parametrize(
    ("note", "reply", "expected"),
    [
        # A note that names every key is kept as the model wrote it, in any case.
        ("IDP-9 is about half done.", _HANA_V2, "IDP-9 is about half done."),
        ("idp-9 is about half done.", _HANA_V2, "idp-9 is about half done."),
        # Keys lead in the order the reply names them.
        (
            "Both in review.",
            "CHK-6 and CHK-3 are both in review.",
            "CHK-6, CHK-3: Both in review.",
        ),
        # A note that names some of them ends with the rest.
        (
            "IDP-3 is in review",
            "IDP-3 is in review on identity-service !1, IDP-9 outline started.",
            "IDP-3 is in review. Also about IDP-9.",
        ),
        # A reply that names no issue changes nothing.
        (
            "Quiet day, nothing blocking.",
            "Quiet day, nothing blocking.",
            "Quiet day, nothing blocking.",
        ),
    ],
)
async def test_the_note_keeps_every_key_the_reply_names(
    note: str, reply: str, expected: str
) -> None:
    assert await _parsed_note(note, reply) == expected


async def test_both_prompts_ask_for_the_keys_in_the_note() -> None:
    parser_llm = SequenceLlmProvider(texts=[json.dumps(_signals("IDP-9 about half done."))])
    await StatusParser(parser_llm, model="test-model").parse_reply(
        tenant_id=_TENANT, developer_id=_HANA, raw_reply=_HANA_V2, correlation_id="corr-hana"
    )
    evaluator_llm = SequenceLlmProvider(texts=[_sufficient("IDP-9 about half done.")])
    await ClarificationEvaluator(evaluator_llm, model="test-model").evaluate(
        tenant_id=_TENANT, developer_id=_HANA, raw_reply=_HANA_V2, correlation_id="corr-hana"
    )

    for request in (*parser_llm.requests, *evaluator_llm.requests):
        assert "names every issue key the reply names" in request.prompt


async def test_hanas_stored_summary_names_idp_9() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(
        Developer(tenant_id=_TENANT, id=_HANA, name="Hana", metadata={"app_roles": "dev"})
    )
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_HANA,
            correlation_id="corr-hana",
            asked_at=_ASKED_AT,
            replied_at=None,
            raw_reply=None,
            signals=None,
            checkin_date=_DAY,
        )
    )
    tracker = FakeIssueTracker(
        issues={
            "IDP-9": Issue(
                tenant_id=_TENANT,
                key="IDP-9",
                title="Enterprise SSO onboarding guide",
                state=IssueState.IN_PROGRESS,
                assignee=UserRef(tenant_id=_TENANT, external_id=_HANA),
                updated_at=_ASKED_AT,
            )
        }
    )
    collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(
            texts=[
                _sufficient(
                    "About half done.",
                    claims=[
                        {
                            "issue_key": "IDP-9",
                            "claimed_done": False,
                            "claimed_state": "in progress",
                            "note": "about half done, docs only",
                        }
                    ],
                )
            ]
        ),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
        checkin_ack_enabled=False,
    )

    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id=_TENANT,
            user=ChatUserRef(tenant_id=_TENANT, external_id=_HANA),
            text=_HANA_V2,
            thread_id="thread-hana",
            message_id="m1",
            correlation_id="corr-hana",
            received_at=datetime(2026, 10, 11, 4, 53, tzinfo=UTC),
        )
    )

    assert outcome.status is not None
    assert outcome.status.source is StatusSource.CONFIRMED
    stored = await store.latest_developer_status(_TENANT, _HANA, _DAY)
    assert stored is not None
    assert stored.summary == "IDP-9: About half done."
