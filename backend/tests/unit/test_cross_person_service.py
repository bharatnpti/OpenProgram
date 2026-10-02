from __future__ import annotations

from datetime import UTC, datetime

import pytest

from config.settings import Settings
from core.application.cross_person_service import CrossPersonRequestService
from core.domain.cross_person import CrossPersonRequestResolution, CrossPersonRequestStatus
from core.domain.directory import DirectoryUser
from core.domain.graph import EntityRef, NodeKind
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CrossPersonMention
from core.ports.chat import ChatProvider
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeChatProvider


async def test_record_from_checkin_records_fact_without_dm_when_auto_notify_is_off() -> None:
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
        auto_notify=False,
    )
    resolution = _resolution()

    created = await service.record_from_checkin(
        tenant_id="demo",
        requester_id="dev-1",
        requester_chat_ref="U-dev",
        source_correlation_id="corr-1",
        resolutions=(resolution,),
        observed_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
    )

    assert len(chat.sent) == 0
    stored = created[0]
    assert stored.notify_message_id is None
    assert stored.notify_correlation_id is None
    facts = await store.list_facts(
        "demo",
        EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="U-alice"),
    )
    assert len(facts) == 1
    assert facts[0].source == "cross_person_request"
    assert facts[0].payload["transition"] == "opened"
    assert facts[0].payload["reporter_id"] == "dev-1"
    assert facts[0].payload["referenced_person_id"] == "U-alice"
    assert facts[0].payload["referenced_person_name"] == "Alice Chen"
    assert facts[0].payload["dependency_kind"] == "needs_review"
    assert facts[0].payload["dependency_status"] == "open"
    assert facts[0].payload["summary"] == "API schema review"
    assert set(facts[0].payload).isdisjoint(
        {"requester_id", "counterpart_id", "counterpart_name", "kind", "status", "note"}
    )


async def test_record_from_checkin_notifies_by_default_and_is_idempotent() -> None:
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


async def test_registry_routes_slack_thread_reply_by_notify_message_id_first() -> None:
    store = InMemoryGraphStore()
    registry = ServiceRegistry(_settings(), graph_store=store)
    directory = registry.directory_user_repository()
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
    service = registry.cross_person_request_service()
    first = (
        await service.record_from_checkin(
            tenant_id="demo",
            requester_id="dev-1",
            requester_chat_ref="U-dev",
            source_correlation_id="corr-first",
            resolutions=(_resolution(),),
            observed_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
        )
    )[0]
    second = (
        await service.record_from_checkin(
            tenant_id="demo",
            requester_id="dev-2",
            requester_chat_ref="U-dev-2",
            source_correlation_id="corr-second",
            resolutions=(_resolution(),),
            observed_at=datetime(2026, 1, 10, 9, 11, tzinfo=UTC),
        )
    )[0]

    result = await registry.process_chat_webhook(
        "slack",
        {
            "event": {
                "type": "message",
                "user": "U-alice",
                "text": "on it",
                "ts": "1700000000.000123",
                "channel": "C-alice",
                "thread_ts": second.notify_message_id,
            }
        },
        correlation_id="unmatched-reply-correlation",
        received_at=datetime(2026, 1, 10, 9, 20, tzinfo=UTC),
    )

    assert result.status == CrossPersonRequestStatus.ACKNOWLEDGED.value
    assert result.message_id == "1700000000.000123"
    stored_first = await store.get("demo", first.id)
    stored_second = await store.get("demo", second.id)
    assert stored_first is not None
    assert stored_second is not None
    assert stored_first.status is CrossPersonRequestStatus.OPEN
    assert stored_second.status is CrossPersonRequestStatus.ACKNOWLEDGED


async def test_unmatched_person_is_recorded_for_the_requester_without_a_dm() -> None:
    store = InMemoryGraphStore()
    chat = FakeChatProvider()
    service = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=InMemoryDirectoryUserRepository(store),
        time_series_repository=store,
    )
    unmatched = CrossPersonRequestResolution(
        mention=CrossPersonMention(raw_name="Zed Quinn", kind="review", note="API schema review"),
        status=CrossPersonRequestStatus.NEEDS_RESOLUTION,
    )

    created = await service.record_from_checkin(
        tenant_id="demo",
        requester_id="dev-1",
        requester_chat_ref="U-dev",
        source_correlation_id="corr-1",
        resolutions=(unmatched,),
        observed_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
    )

    assert chat.sent == []
    assert created[0].status is CrossPersonRequestStatus.NEEDS_RESOLUTION
    assert created[0].counterpart_id is None
    assert created[0].notify_message_id is None
    assert [r.id for r in await service.list_raised("demo", "dev-1")] == [created[0].id]
    assert [r.id for r in await service.list_portfolio("demo")] == [created[0].id]


class _SharedChatRegistry(ServiceRegistry):
    """A registry whose chat provider is one shared fake, so sent DMs can be read."""

    def __init__(self, settings: Settings, *, graph_store: InMemoryGraphStore) -> None:
        super().__init__(settings, graph_store=graph_store)
        self.chat = FakeChatProvider()

    def chat_provider(self) -> ChatProvider:
        return self.chat


@pytest.mark.parametrize(
    ("overrides", "expected_dms"),
    [
        pytest.param({}, 1, id="default-is-on"),
        pytest.param({"cross_person_auto_notify": True}, 1, id="explicitly-on"),
        pytest.param({"cross_person_auto_notify": False}, 0, id="switched-off"),
    ],
)
async def test_registry_wires_the_auto_notify_setting_into_the_service(
    overrides: dict[str, object], expected_dms: int
) -> None:
    store = InMemoryGraphStore()
    registry = _SharedChatRegistry(_settings(**overrides), graph_store=store)
    await registry.directory_user_repository().upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U-alice",
                display_name="Alice Chen",
                email="alice@example.com",
            )
        ]
    )

    created = await registry.cross_person_request_service().record_from_checkin(
        tenant_id="demo",
        requester_id="dev-1",
        requester_chat_ref="U-dev",
        source_correlation_id="corr-1",
        resolutions=(_resolution(),),
        observed_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
    )

    assert len(registry.chat.sent) == expected_dms
    assert (created[0].notify_message_id is not None) is bool(expected_dms)
    assert created[0].status is CrossPersonRequestStatus.OPEN


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


def _settings(**overrides: object) -> Settings:
    values = {
        "secret_key": "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        "runtime_mode": "memory",
        "chat_provider": "fake",
        "directory_provider": "fake",
        "issue_tracker_provider": "fake",
        "vcs_provider": "fake",
        "calendar_provider": "fake",
        "llm_provider": "fake",
        "workflow_provider": "fake",
        **overrides,
    }
    return Settings.model_validate(values)
