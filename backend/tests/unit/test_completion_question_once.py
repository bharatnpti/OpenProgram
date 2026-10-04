"""A person who said an issue is done is not asked again whether it is complete (N41).

R5 live (qa2, everyone ``always_ask``): Omar said CHK-17 was merged and its ticket
needed closing. The bot asked "Can you confirm if all work for CHK-17 is complete
and ready to close the ticket?", he said yes, and it asked the same again because
Jira still showed CHK-17 in progress. Both follow-ups were spent before the
consent question, so his IDP-6 ETA was never asked and he closed ``partial``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.application.status_collector import StatusCollector, _asks_only_to_confirm_done
from core.application.writeback_service import WriteBackService
from core.domain.graph import Developer, EntityRef, FactEvent, NodeKind
from core.domain.identity import IdentityLink
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
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_writeback_reply import _evaluation, _ScriptedLlm, _sent

_TENANT = "qa2"
_OMAR = "dev-omar"
_CORRELATION = "corr-omar"
_ASKED_AT = datetime(2026, 10, 4, 12, 0, 7, tzinfo=UTC)

# The two follow-ups R5 sent Omar (12:06:29 and 12:07:42).
_FIRST_QUESTION = (
    "Can you confirm if all work for CHK-17 is complete and ready to close the ticket?"
)
_SECOND_QUESTION = (
    "CHK-17 is still marked in progress in Jira. Can you confirm if all work for CHK-17 "
    "is complete and ready to close?"
)
_FIRST_TEXT = (
    "IDP-6 still waiting on review, sso-gateway !1 has no reviewer. CHK-17 merged "
    "(platform-libs !1), ticket needs closing. IDP-8 and CHK-18 not started, no blockers."
)
_IDP6_WAITING = {
    "issue_key": "IDP-6",
    "claimed_done": False,
    "claimed_state": "waiting on review",
    "note": "sso-gateway !1 has no reviewer yet.",
}
_CHK17_MERGED = {
    "issue_key": "CHK-17",
    "claimed_done": True,
    "claimed_state": "merged",
    "note": "platform-libs !1 merged; the ticket needs closing.",
}
_NOT_STARTED = [
    {"issue_key": key, "claimed_done": False, "claimed_state": "not started", "note": ""}
    for key in ("IDP-8", "CHK-18")
]
_FIRST_EVALUATION = _evaluation(
    progress_note="IDP-6 waiting on review; CHK-17 merged, ticket needs closing; no blockers",
    issue_updates=[_IDP6_WAITING, _CHK17_MERGED, *_NOT_STARTED],
    question=_FIRST_QUESTION,
    blockers_answered=True,
)
_YES_TEXT = "Yes, CHK-17 is complete. OK to close it."
_YES_EVALUATION = _evaluation(
    progress_note="CHK-17 complete and OK to close",
    issue_updates=[
        {
            "issue_key": "CHK-17",
            "claimed_done": True,
            "claimed_state": "complete",
            "note": "All work done, OK to close.",
        }
    ],
    question=_SECOND_QUESTION,
)
_ETA_TEXT = "IDP-6 should be done by Wednesday once someone reviews sso-gateway !1."
_ETA_EVALUATION = _evaluation(
    progress_note="IDP-6 done by Wednesday once reviewed",
    issue_updates=[
        {
            "issue_key": "IDP-6",
            "claimed_done": False,
            "claimed_state": "in review",
            "note": "Done by Wednesday once reviewed.",
        }
    ],
    eta_answered=True,
)


def _merge_request(repo: str, issue_key: str, *, state: str) -> FactEvent:
    observed_at = datetime(2026, 10, 4, 11, 30, tzinfo=UTC)
    return FactEvent(
        tenant_id=_TENANT,
        source="vcs_pull_request",
        entity_ref=EntityRef(tenant_id=_TENANT, kind=NodeKind.REPO, id=f"acme/{repo}"),
        payload={
            "repo": f"acme/{repo}",
            "id": "1",
            "title": f"{issue_key}: {repo} work",
            "merged": state == "merged",
            "state": state,
            "draft": False,
            "source_branch": f"feature/{issue_key}",
            "web_url": f"https://gitlab.example/acme/{repo}/-/merge_requests/1",
        },
        observed_at=observed_at,
        correlation_id=f"vcs:pull_request:{_TENANT}:acme/{repo}:1:{observed_at}",
    )


async def _omar_collector(
    texts: list[str], *, write_back: bool = True
) -> tuple[StatusCollector, FakeIssueTracker, FakeChatProvider, InMemoryGraphStore]:
    """Omar on qa2 before R5: CHK-17 (!1 merged) and IDP-6 (!1 open) In Progress, his."""
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id=_TENANT, id=_OMAR, name="Omar Haddad"))
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_OMAR,
            correlation_id=_CORRELATION,
            asked_at=_ASKED_AT,
            replied_at=None,
            raw_reply=None,
            signals=None,
            checkin_date=_ASKED_AT.date(),
        )
    )
    await store.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id=_TENANT,
            developer_id=_OMAR,
            correlation_id=_CORRELATION,
            chat_user_ref=_OMAR,
            chat_thread_ref="thread-omar",
            outbound_message_id="msg-question-omar",
            asked_at=_ASKED_AT,
        )
    )
    await store.record_checkin_preference(
        CheckInPreference(
            tenant_id=_TENANT,
            developer_id=_OMAR,
            write_back_consent=WriteBackConsent.ALWAYS_ASK,
        )
    )
    await store.upsert_identity_link(
        IdentityLink(tenant_id=_TENANT, developer_id=_OMAR, jira_account_id="acct-omar")
    )
    await store.set_writeback_enabled(_TENANT, True)
    await store.append_fact(_merge_request("platform-libs", "CHK-17", state="merged"))
    await store.append_fact(_merge_request("sso-gateway", "IDP-6", state="opened"))
    omar = UserRef(tenant_id=_TENANT, external_id="acct-omar")
    tracker = FakeIssueTracker(
        issues={
            key: Issue(tenant_id=_TENANT, key=key, title=title, state=state, assignee=omar)
            for key, title, state in (
                ("CHK-17", "Shared retry helper", IssueState.IN_PROGRESS),
                ("IDP-6", "SSO session refresh", IssueState.IN_PROGRESS),
                ("IDP-8", "Audit log export", IssueState.TODO),
                ("CHK-18", "Checkout feature flags", IssueState.TODO),
            )
        }
    )
    chat = FakeChatProvider()
    collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=chat,
        llm_provider=_ScriptedLlm(texts=list(texts)),
        status_repository=store,
        conversation_repository=store,
        identity_link_repository=store,
        write_back_service=(
            WriteBackService(
                issue_tracker=tracker,
                audit_repository=store,
                config_repository=store,
                status_repository=store,
                identity_link_repository=store,
                time_series_repository=store,
                graph_repository=store,
            )
            if write_back
            else None
        ),
        model="test-model",
    )
    return collector, tracker, chat, store


def _omar_says(text: str, message_id: str, minute: int, second: int) -> InboundMessage:
    return InboundMessage(
        tenant_id=_TENANT,
        user=ChatUserRef(tenant_id=_TENANT, external_id=_OMAR),
        text=text,
        thread_id="thread-omar",
        message_id=message_id,
        correlation_id=_CORRELATION,
        received_at=datetime(2026, 10, 4, 12, minute, second, tzinfo=UTC),
    )


async def test_omar_r5_is_asked_his_eta_and_the_consent_question_confirms_chk17() -> None:
    collector, tracker, chat, store = await _omar_collector([_FIRST_EVALUATION, _ETA_EVALUATION])

    first = await collector.handle_reply(_omar_says(_FIRST_TEXT, "m-1", 5, 53))

    # The "is CHK-17 complete?" follow-up is not sent: the write-back will ask
    # him to confirm CHK-17 Done. The one follow-up is the ETA he still owes.
    assert first.kind == "clarifying"
    [question] = _sent(chat, "status_clarification")
    assert "ETA" in question and "IDP-6" in question
    assert "CHK-17" not in question
    assert _sent(chat, "writeback_consent_prompt") == []

    second = await collector.handle_reply(_omar_says(_ETA_TEXT, "m-2", 7, 8))

    assert len(_sent(chat, "status_clarification")) == 1
    assert _sent(chat, "writeback_consent_prompt") == [
        "Want me to update CHK-17 to “Done” in the issue tracker? Reply yes or no."
    ]
    rows = {
        row.issue_key: row
        for row in await store.list_writeback_by_correlation(_TENANT, _CORRELATION)
    }
    assert (rows["CHK-17"].status, rows["CHK-17"].target_state) == (
        WriteBackStatus.PROPOSED,
        "done",
    )
    assert tracker.transitions == []  # nothing moves before his yes
    assert second.status is not None
    assert second.status.source is StatusSource.CONFIRMED  # the ETA was asked and given


async def test_a_completion_question_already_answered_is_not_asked_again() -> None:
    # Without the write-back (or for consent never) the evaluator's re-ask after
    # "Yes, CHK-17 is complete" is still dropped, and the ETA is asked instead.
    collector, tracker, chat, _ = await _omar_collector(
        [_FIRST_EVALUATION, _YES_EVALUATION], write_back=False
    )

    await collector.handle_reply(_omar_says(_FIRST_TEXT, "m-1", 5, 53))
    outcome = await collector.handle_reply(_omar_says(_YES_TEXT, "m-2", 7, 8))

    sent = _sent(chat, "status_clarification")
    assert sent[0] == _FIRST_QUESTION  # the first message's follow-up is the evaluator's
    assert _SECOND_QUESTION not in sent
    assert len(sent) == 2
    assert "ETA" in sent[1] and "IDP-6" in sent[1]
    assert outcome.kind == "clarifying"
    assert tracker.transitions == []


async def test_a_completion_question_about_an_issue_not_said_done_is_kept() -> None:
    in_review = _evaluation(
        progress_note="CHK-17 in review",
        issue_updates=[{"issue_key": "CHK-17", "claimed_state": "in review", "note": ""}],
        question=_SECOND_QUESTION,
        blockers_answered=True,
    )
    collector, _, chat, _ = await _omar_collector([_FIRST_EVALUATION, in_review], write_back=False)

    await collector.handle_reply(_omar_says(_FIRST_TEXT, "m-1", 5, 53))
    await collector.handle_reply(_omar_says("CHK-17 is back in review.", "m-2", 7, 8))

    assert _sent(chat, "status_clarification") == [_FIRST_QUESTION, _SECOND_QUESTION]


def test_asks_only_to_confirm_done_reads_the_ask_not_the_statement() -> None:
    assert _asks_only_to_confirm_done(_FIRST_QUESTION)
    assert _asks_only_to_confirm_done(_SECOND_QUESTION)
    assert _asks_only_to_confirm_done("Is CHK-17 complete?")
    # An ask for anything else as well, or no completion ask at all.
    assert not _asks_only_to_confirm_done("What is your ETA to finish IDP-6?")
    assert not _asks_only_to_confirm_done("Is CHK-17 done, and who reviews IDP-6?")
    assert not _asks_only_to_confirm_done("Which merge request completed CHK-17?")
    assert not _asks_only_to_confirm_done("CHK-17 is still marked in progress in Jira.")
