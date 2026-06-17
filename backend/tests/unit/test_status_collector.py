from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from core.application.status_collector import StatusCollector
from core.application.status_parsing import StatusParser
from core.domain.graph import EntityRef, FactEvent, NodeKind
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CheckIn, DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker


@dataclass
class SequenceLlmProvider:
    texts: list[str]
    requests: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        text = self.texts.pop(0)
        return LlmResponse(
            tenant_id=request.tenant_id,
            text=text,
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost_usd=0.0,
                latency_ms=1.0,
            ),
            trace_id=f"trace-{len(self.requests)}",
        )


async def test_status_collector_graph_sends_dm_and_records_checkin() -> None:
    assignee = UserRef(tenant_id="demo", external_id="dev-1")
    tracker = FakeIssueTracker(
        issues={
            "PO-1": Issue(
                tenant_id="demo",
                key="PO-1",
                title="Build graph sync",
                state=IssueState.IN_PROGRESS,
                assignee=assignee,
            )
        }
    )
    store = InMemoryGraphStore()
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="unit",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="PO-1"),
            payload={"risk": "schema review"},
            observed_at=datetime(2026, 1, 10, 8, 0, tzinfo=UTC),
            correlation_id="fact-1",
        )
    )
    chat = FakeChatProvider()
    llm = SequenceLlmProvider(texts=["Can you share progress, blockers, and ETA changes?"])
    collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=chat,
        llm_provider=llm,
        status_repository=store,
        time_series_repository=store,
        model="test-model",
    )

    checkin = await collector.start_checkin(
        tenant_id="demo",
        developer_id="dev-1",
        developer_name="Asha",
        chat_external_id="U123",
        correlation_id="corr-1",
        asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
    )

    assert checkin == await store.checkin_by_correlation("demo", "corr-1")
    assert checkin.raw_reply is None
    assert chat.sent[0].correlation_id == "corr-1"
    assert chat.sent[0].metadata == {"purpose": "status_checkin"}
    assert "Build graph sync" in llm.requests[0].prompt
    assert "schema review" in llm.requests[0].prompt


async def test_status_collector_handles_reply_by_correlation() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    parser_llm = SequenceLlmProvider(
        texts=[
            '{"progress_note":"Graph sync is in review",'
            '"blockers":["schema review"],"eta_change_days":1,"mood":"neutral"}'
        ]
    )
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=["unused"]),
        status_repository=store,
        model="test-model",
        parser=StatusParser(parser_llm, model="test-model"),
    )

    status = await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="U123"),
            text="Graph sync is in review, blocked on schema review.",
            thread_id="thread-1",
            message_id="msg-1",
            correlation_id="corr-1",
            received_at=datetime(2026, 1, 10, 9, 7, tzinfo=UTC),
        )
    )

    updated = await store.checkin_by_correlation("demo", "corr-1")
    assert updated is not None
    assert updated.raw_reply == "Graph sync is in review, blocked on schema review."
    assert status.source is StatusSource.CONFIRMED
    assert status.blockers == ("schema review",)
    assert await store.latest_developer_status("demo", "dev-1", date(2026, 1, 10)) == status
    assert "Graph sync is in review" not in parser_llm.requests[0].metadata.values()


async def test_status_collector_nudges_once_and_records_stale_non_response() -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 9),
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Yesterday was on track.",
        )
    )
    chat = FakeChatProvider()
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=SequenceLlmProvider(texts=["Quick follow-up on your status update."]),
        status_repository=store,
        model="test-model",
    )

    result = await collector.nudge_then_mark_stale(
        tenant_id="demo",
        correlation_id="corr-1",
        as_of=date(2026, 1, 10),
        developer_name="Asha",
        chat_external_id="U123",
    )

    assert result.nudge_message_id == "msg-U123-1"
    assert len(chat.sent) == 1
    assert result.inferred_status is not None
    assert result.inferred_status.source is StatusSource.INFERRED
    assert result.stale_status.source is StatusSource.STALE
    assert result.stale_status.blockers == ("no confirmed reply",)
    assert "Yesterday was on track" in result.stale_status.summary
