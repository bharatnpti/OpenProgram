from __future__ import annotations

from datetime import UTC, datetime

from core.application.cross_person_service import CrossPersonRequestService
from core.domain.cross_person import CrossPersonRequestResolution, CrossPersonRequestStatus
from core.domain.directory import DirectoryUser
from core.domain.graph import EntityRef, NodeKind
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CrossPersonMention
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider


async def test_record_from_checkin_notifies_counterpart_idempotently() -> None:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U-alice",
                display_name="Alice Chen",
                email="alice@example.com",
            )
        ]
    )
    chat = FakeChatProvider()
    service = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=directory,
        time_series_repository=store,
    )
    resolution = _resolution()

    first = await service.record_from_checkin(
        tenant_id="demo",
        requester_id="dev-1",
        requester_chat_ref="U-dev",
        source_correlation_id="corr-1",
        resolutions=(resolution,),
        observed_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
    )
    second = await service.record_from_checkin(
        tenant_id="demo",
        requester_id="dev-1",
        requester_chat_ref="U-dev",
        source_correlation_id="corr-1",
        resolutions=(resolution,),
        observed_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
    )

    assert first == second
    assert len(chat.sent) == 1
    stored = first[0]
    assert stored.notify_message_id == "msg-U-alice-1"
    assert stored.notify_correlation_id == f"xreq-{stored.id}"
    assert chat.sent[0].metadata["idempotency_key"] == f"xreq-notify:{stored.id}"
    facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="U-alice"),
    )
    assert len(facts) == 1
    assert facts[0].source == "cross_person_request"
    assert facts[0].payload["transition"] == "opened"


async def test_counterpart_reply_acknowledges_and_resolves_request() -> None:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U-alice",
                display_name="Alice Chen",
                email="alice@example.com",
            )
        ]
    )
    chat = FakeChatProvider()
    service = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=directory,
        time_series_repository=store,
    )
    created = await service.record_from_checkin(
        tenant_id="demo",
        requester_id="dev-1",
        requester_chat_ref="U-dev",
        source_correlation_id="corr-1",
        resolutions=(_resolution(),),
        observed_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
    )
    request = created[0]

    acknowledged = await service.handle_counterpart_reply(
        _counterpart_reply(request.notify_correlation_id or "", "on it"),
        request,
    )
    resolved = await service.handle_counterpart_reply(
        _counterpart_reply(request.notify_correlation_id or "", "done and approved"),
        acknowledged,
    )

    assert acknowledged.status is CrossPersonRequestStatus.ACKNOWLEDGED
    assert resolved.status is CrossPersonRequestStatus.RESOLVED
    assert len(chat.sent) == 2
    assert chat.sent[1].metadata["purpose"] == "cross_person_request_resolved"
    assert "marked your review request resolved" in chat.sent[1].text
    facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="U-alice"),
    )
    assert [fact.payload["transition"] for fact in facts] == [
        "opened",
        "acknowledged",
        "resolved",
    ]


def _resolution() -> CrossPersonRequestResolution:
    return CrossPersonRequestResolution(
        mention=CrossPersonMention(
            raw_name="Alice Chen",
            kind="review",
            note="API schema review",
            email="alice@example.com",
        ),
        status=CrossPersonRequestStatus.OPEN,
        counterpart_id="U-alice",
        counterpart_display_name="Alice Chen",
        counterpart_email="alice@example.com",
    )


def _counterpart_reply(correlation_id: str, text: str) -> InboundMessage:
    return InboundMessage(
        tenant_id="demo",
        user=ChatUserRef(tenant_id="demo", external_id="U-alice"),
        text=text,
        thread_id="thread-U-alice",
        message_id=f"reply-{text}",
        correlation_id=correlation_id,
        received_at=datetime(2026, 1, 10, 9, 20, tzinfo=UTC),
    )
