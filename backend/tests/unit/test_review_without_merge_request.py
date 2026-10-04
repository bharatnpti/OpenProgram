"""An issue said to be in review with no open merge request is asked about, then flagged.

R1 live (SC6): Omar said IDP-6 was "up for review" when only a branch existed.
The check-in accepted it, never asked for the merge request, and nothing
recorded the gap. He opened sso-gateway !1 only after correcting himself.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from core.application.blocker_resolution import BlockerResolutionService
from core.application.checkin_drift import (
    CHECKIN_DRIFT_FACT_SOURCE,
    in_review_claim_keys,
    review_without_merge_request_question,
)
from core.application.risk_service import RiskService
from core.application.status_collector import StatusCollector
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    NodeKind,
    Project,
    Task,
)
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.risk import DriftFindingKind, RiskProviderConfig
from core.domain.rollup import Rag
from core.domain.status import CheckIn, IssueClaim, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

_TENANT = "demo"
_DAY = date(2026, 10, 3)
_OMAR = "U-omar"
_QUESTION = "I can't find a merge request for IDP-6 yet. Is it opened?"


def _evaluation(claims: list[dict[str, object]]) -> str:
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": True,
            "question": None,
            "signals": {
                "progress_note": "IDP-6 metadata refresh up for review; no blockers.",
                "blockers": [],
                "eta_change_days": None,
                "blockers_answered": True,
                "eta_answered": True,
                "issue_updates": claims,
            },
        }
    )


_UP_FOR_REVIEW = _evaluation(
    [{"issue_key": "IDP-6", "claimed_done": False, "claimed_state": "up for review", "note": ""}]
)


async def _seed(store: InMemoryGraphStore) -> None:
    """Identity project with IDP-6 (Omar's) and one unrelated synced merge request."""
    await store.upsert_node(Project(tenant_id=_TENANT, id="proj-idp", name="Identity"))
    await store.upsert_node(
        Task(
            tenant_id=_TENANT,
            id="IDP-6",
            name="Metadata refresh",
            metadata={"key": "IDP-6", "state": "in_progress", "status": "In Progress"},
        )
    )
    await store.upsert_node(Developer(tenant_id=_TENANT, id=_OMAR, name="Omar Haddad"))
    await store.add_edge(
        GraphEdge(
            tenant_id=_TENANT,
            from_node_id="proj-idp",
            to_node_id="IDP-6",
            kind=EdgeKind.CONTAINS,
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id=_TENANT, from_node_id=_OMAR, to_node_id="IDP-6", kind=EdgeKind.ASSIGNED_TO
        )
    )
    await _merge_request(store, "sso-gateway", "7", branch="IDP-5-token-refresh", state="opened")
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_OMAR,
            correlation_id="corr-omar",
            asked_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )


async def _merge_request(
    store: InMemoryGraphStore, repo: str, pr_id: str, *, branch: str, state: str
) -> None:
    await store.append_fact(
        FactEvent(
            tenant_id=_TENANT,
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id=_TENANT, kind=NodeKind.DEVELOPER, id=_OMAR),
            payload={
                "repo": repo,
                "id": pr_id,
                "source_branch": branch,
                "title": "work",
                "state": state,
                "merged": False,
                "web_url": f"https://git.test/{repo}/-/merge_requests/{pr_id}",
            },
            observed_at=datetime(2026, 10, 3, 12, 30, tzinfo=UTC),
            correlation_id=f"mr-{repo}-{pr_id}",
        )
    )


def _collector(
    store: InMemoryGraphStore, chat: FakeChatProvider, texts: list[str], **kwargs: object
) -> StatusCollector:
    return StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=SequenceLlmProvider(texts=texts),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
        checkin_ack_enabled=False,
        **kwargs,  # type: ignore[arg-type]
    )


def _message(text: str, message_id: str, minute: int) -> InboundMessage:
    return InboundMessage(
        tenant_id=_TENANT,
        user=ChatUserRef(tenant_id=_TENANT, external_id=_OMAR),
        text=text,
        thread_id="thread-omar",
        message_id=message_id,
        correlation_id="corr-omar",
        received_at=datetime(2026, 10, 3, 12, minute, tzinfo=UTC),
    )


def _risks(store: InMemoryGraphStore) -> RiskService:
    return RiskService(
        graph_repository=store,
        time_series_repository=store,
        status_repository=store,
        blocker_resolution=BlockerResolutionService(store, store),
        rollup_repository=store,
        provider_config=RiskProviderConfig(),
    )


async def test_up_for_review_without_a_merge_request_asks_once_then_flags_drift() -> None:
    store = InMemoryGraphStore()
    await _seed(store)
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            _UP_FOR_REVIEW,
            # "Not opened yet": the claim still says up for review.
            _evaluation([]),
        ],
    )

    asked = await collector.handle_reply(
        _message("IDP-6 is up for review, no blockers, ETA unchanged", "m1", 10)
    )
    answered = await collector.handle_reply(_message("not yet, only the branch", "m2", 12))

    assert asked.kind == "clarifying"
    assert asked.status is not None
    assert asked.status.source is StatusSource.PARTIAL
    assert [message.text for message in chat.sent] == [_QUESTION]
    assert await store.checkin_clarification_count(_TENANT, "corr-omar") == 1
    # The answer does not draw the same question again; the check-in finalizes.
    assert answered.kind == "processed"
    findings = await _risks(store).project_drift(_TENANT, "proj-idp", _DAY)
    assert [finding.kind for finding in findings] == [DriftFindingKind.SAID_IN_REVIEW_NO_MR]
    finding = findings[0]
    assert finding.severity is Rag.AMBER
    assert finding.entity_ref == EntityRef(tenant_id=_TENANT, kind=NodeKind.TASK, id="IDP-6")
    assert finding.owner_id == _OMAR
    assert finding.reason == (
        "Omar Haddad said IDP-6 is in review, but no open merge request names it."
    )


async def test_drift_clears_once_the_merge_request_is_synced() -> None:
    store = InMemoryGraphStore()
    await _seed(store)
    collector = _collector(
        store, FakeChatProvider(), [_UP_FOR_REVIEW], checkin_max_clarifications=0
    )

    outcome = await collector.handle_reply(_message("IDP-6 is up for review", "m1", 10))
    flagged = await _risks(store).project_drift(_TENANT, "proj-idp", _DAY)
    await _merge_request(store, "sso-gateway", "1", branch="IDP-6-metadata-refresh", state="opened")
    cleared = await _risks(store).project_drift(_TENANT, "proj-idp", _DAY)

    # The follow-up limit is spent: no question, the round ends, the gap is recorded.
    assert outcome.kind == "processed"
    assert [finding.kind for finding in flagged] == [DriftFindingKind.SAID_IN_REVIEW_NO_MR]
    assert cleared == []


async def test_open_merge_request_for_the_issue_means_no_question_and_no_drift() -> None:
    store = InMemoryGraphStore()
    await _seed(store)
    await _merge_request(store, "sso-gateway", "1", branch="IDP-6-metadata-refresh", state="opened")
    chat = FakeChatProvider()
    collector = _collector(store, chat, [_UP_FOR_REVIEW])

    outcome = await collector.handle_reply(_message("IDP-6 is up for review", "m1", 10))

    assert outcome.kind == "processed"
    assert chat.sent == []
    assert await store.list_recent_facts(_TENANT, sources=(CHECKIN_DRIFT_FACT_SOURCE,)) == []
    assert await _risks(store).project_drift(_TENANT, "proj-idp", _DAY) == []


async def test_no_question_when_no_merge_request_is_synced_at_all() -> None:
    store_without_mrs = InMemoryGraphStore()
    await store_without_mrs.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_OMAR,
            correlation_id="corr-omar",
            asked_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    chat = FakeChatProvider()
    collector = _collector(store_without_mrs, chat, [_UP_FOR_REVIEW])

    outcome = await collector.handle_reply(_message("IDP-6 is up for review", "m1", 10))

    assert outcome.kind == "processed"
    assert chat.sent == []


def test_only_a_review_state_now_counts_as_in_review() -> None:
    claims = (
        IssueClaim(issue_key="IDP-6", claimed_state="up for review"),
        IssueClaim(issue_key="CHK-17", claimed_state="waiting for review"),
        IssueClaim(issue_key="IDP-3", claimed_state="in review"),
        IssueClaim(issue_key="CHK-4", claimed_state="ready for review by end of week"),
        IssueClaim(issue_key="INS-5", claimed_state="in progress"),
        IssueClaim(issue_key="IDP-5", claimed_state="merged", claimed_done=True),
    )

    assert in_review_claim_keys(claims) == ("IDP-6", "CHK-17", "IDP-3")
    assert review_without_merge_request_question(["IDP-6", "CHK-17"]) == (
        "I can't find a merge request for IDP-6 or CHK-17 yet. Are they opened?"
    )
