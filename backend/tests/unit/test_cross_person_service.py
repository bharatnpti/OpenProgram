from __future__ import annotations

from datetime import UTC, datetime

import pytest

from config.settings import Settings
from core.application.cross_person_service import (
    CrossPersonRequestService,
    RequestStatusNotSettable,
)
from core.application.status_collector import OUTBOUND_DM_MAX_CHARS
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestResolution,
    CrossPersonRequestStatus,
    may_set_status,
)
from core.domain.directory import DirectoryUser
from core.domain.errors import AuthorizationDenied, ProviderUnavailable
from core.domain.graph import EntityRef, JsonScalar, NodeKind
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
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


_REQUESTER = DirectoryUser(
    tenant_id="demo",
    external_id="U-dev",
    display_name="Dana Ortiz",
    email="dana@example.com",
)
_ALICE = DirectoryUser(
    tenant_id="demo",
    external_id="U-alice",
    display_name="Alice Chen",
    email="alice@example.com",
)


async def _service_with(
    *users: DirectoryUser, chat: ChatProvider | None = None
) -> tuple[CrossPersonRequestService, InMemoryGraphStore, FakeChatProvider]:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(list(users))
    fake = FakeChatProvider()
    service = CrossPersonRequestService(
        repository=store,
        chat_provider=chat or fake,
        directory_repository=directory,
        time_series_repository=store,
    )
    return service, store, fake


async def _record(
    service: CrossPersonRequestService,
    *resolutions: CrossPersonRequestResolution,
    requester_id: str = "dev-1",
    requester_chat_ref: str | None = "U-dev",
    source: str = "corr-1",
) -> list[CrossPersonRequest]:
    return await service.record_from_checkin(
        tenant_id="demo",
        requester_id=requester_id,
        requester_chat_ref=requester_chat_ref,
        source_correlation_id=source,
        resolutions=resolutions,
        observed_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
    )


async def test_counterpart_dm_names_the_requester_and_carries_only_the_extracted_ask() -> None:
    service, _, chat = await _service_with(_ALICE, _REQUESTER)

    await _record(service, _resolution())

    assert [message.text for message in chat.sent] == [
        "Dana Ortiz asked for your review: API schema review\n"
        "Reply in this thread to acknowledge, or say when it is done."
    ]
    # Neither the requester's internal id nor their chat id is what the reader sees.
    assert "dev-1" not in chat.sent[0].text
    assert "U-dev" not in chat.sent[0].text


@pytest.mark.parametrize(
    ("kind", "lead"),
    [
        ("review", "asked for your review"),
        ("input", "asked for your input"),
        ("dependency", "is waiting on you"),
    ],
)
async def test_counterpart_dm_words_each_kind_of_ask(kind: str, lead: str) -> None:
    service, _, chat = await _service_with(_ALICE, _REQUESTER)

    await _record(service, _resolution(kind=kind, note="the deploy key"))

    assert chat.sent[0].text.startswith(f"Dana Ortiz {lead}: the deploy key\n")


async def test_counterpart_dm_finds_the_requester_by_chat_id_when_member_id_differs() -> None:
    service, _, chat = await _service_with(_ALICE, _REQUESTER)

    await _record(service, _resolution(), requester_id="member-17", requester_chat_ref="U-dev")

    assert chat.sent[0].text.startswith("Dana Ortiz asked for your review")


async def test_counterpart_dm_does_not_show_an_unknown_requester_as_an_id() -> None:
    service, _, chat = await _service_with(_ALICE)

    await _record(service, _resolution(), requester_id="member-17", requester_chat_ref="U-ghost")

    assert chat.sent[0].text.startswith("A teammate asked for your review: API schema review\n")
    assert "member-17" not in chat.sent[0].text
    assert "U-ghost" not in chat.sent[0].text


async def test_counterpart_dm_keeps_a_runaway_note_short() -> None:
    service, _, chat = await _service_with(_ALICE, _REQUESTER)
    pasted_reply = "Blocked until the schema is reviewed, " + "and so on " * 200

    await _record(service, _resolution(note=pasted_reply))

    text = chat.sent[0].text
    assert len(text) <= OUTBOUND_DM_MAX_CHARS
    assert "…\n" in text
    assert "and so on " * 20 not in text
    assert text.count("\n") == 1


async def test_counterpart_dm_flattens_a_multiline_note_onto_one_line() -> None:
    service, _, chat = await _service_with(_ALICE, _REQUESTER)

    await _record(service, _resolution(note="API schema\n\n  review   please"))

    assert chat.sent[0].text.splitlines()[0] == (
        "Dana Ortiz asked for your review: API schema review please"
    )


@pytest.mark.parametrize(
    ("requester_id", "requester_chat_ref"),
    [("U-alice", "U-x"), ("dev-1", "U-alice")],
    ids=["same-member-id", "same-chat-id"],
)
async def test_asking_yourself_sends_no_dm_but_the_request_is_kept(
    requester_id: str, requester_chat_ref: str
) -> None:
    service, store, chat = await _service_with(_ALICE)

    created = await _record(
        service, _resolution(), requester_id=requester_id, requester_chat_ref=requester_chat_ref
    )

    assert chat.sent == []
    assert created[0].status is CrossPersonRequestStatus.OPEN
    assert created[0].notify_message_id is None
    assert await store.get("demo", created[0].id) == created[0]


class _FailingChat:
    """A chat provider that cannot deliver to some people."""

    def __init__(self, unreachable: set[str]) -> None:
        self.unreachable = unreachable
        self.sent: list[tuple[str, str]] = []

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        if user.external_id in self.unreachable:
            raise ProviderUnavailable("slack is down for this person")
        self.sent.append((user.external_id, message.text))
        return f"msg-{user.external_id}"

    async def open_thread(self, user: ChatUserRef) -> str:
        return f"thread-{user.external_id}"

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        return None


async def test_a_failed_dm_does_not_lose_the_request_or_the_ones_after_it() -> None:
    bob = DirectoryUser(tenant_id="demo", external_id="U-bob", display_name="Bob Lee")
    chat = _FailingChat(unreachable={"U-alice"})
    service, store, _ = await _service_with(_ALICE, bob, _REQUESTER, chat=chat)

    created = await _record(
        service,
        _resolution(),
        _resolution(name="Bob Lee", counterpart_id="U-bob", note="deploy key"),
    )

    assert [request.counterpart_id for request in created] == ["U-alice", "U-bob"]
    assert [request.status for request in created] == [CrossPersonRequestStatus.OPEN] * 2
    # Alice could not be reached, so nothing claims she was told; Bob was.
    assert created[0].notify_message_id is None
    assert created[1].notify_message_id == "msg-U-bob"
    assert [user for user, _ in chat.sent] == ["U-bob"]
    assert {r.id for r in await store.list_for_requester("demo", "dev-1")} == {
        request.id for request in created
    }


async def test_a_dm_that_failed_is_sent_when_the_request_is_recorded_again() -> None:
    chat = _FailingChat(unreachable={"U-alice"})
    service, _, _ = await _service_with(_ALICE, chat=chat)
    first = await _record(service, _resolution())
    assert first[0].notify_message_id is None

    chat.unreachable.clear()
    second = await _record(service, _resolution())

    assert second[0].id == first[0].id
    assert second[0].notify_message_id == "msg-U-alice"
    assert len(chat.sent) == 1


async def test_a_late_reply_does_not_reopen_or_renotify_a_resolved_request() -> None:
    service, store, chat = await _service_with(_ALICE, _REQUESTER)
    request = (await _record(service, _resolution()))[0]
    resolved = await service.handle_counterpart_reply(
        _counterpart_reply(request.notify_correlation_id or "", "done and approved"), request
    )
    assert resolved.status is CrossPersonRequestStatus.RESOLVED
    dms_after_resolution = len(chat.sent)

    for text in ("thanks!", "done, merged it too", "on it"):
        again = await service.handle_counterpart_reply(
            _counterpart_reply(request.notify_correlation_id or "", text), resolved
        )
        assert again.status is CrossPersonRequestStatus.RESOLVED

    stored = await store.get("demo", request.id)
    assert stored is not None
    assert stored.status is CrossPersonRequestStatus.RESOLVED
    assert len(chat.sent) == dms_after_resolution


async def test_resolving_twice_from_the_console_tells_the_requester_once() -> None:
    service, _, chat = await _service_with(_ALICE, _REQUESTER)
    request = (await _record(service, _resolution()))[0]

    resolved = CrossPersonRequestStatus.RESOLVED
    first = await service.update_status("demo", request.id, resolved, actor="U-alice")
    second = await service.update_status("demo", request.id, resolved, actor="U-alice")

    assert first is not None
    assert second is not None
    assert second.status is CrossPersonRequestStatus.RESOLVED
    told = [m for m in chat.sent if m.metadata.get("purpose") == "cross_person_request_resolved"]
    assert len(told) == 1


async def test_a_resolve_by_the_person_asked_tells_the_requester_and_names_them() -> None:
    service, store, chat = await _service_with(_ALICE, _REQUESTER)
    request = (await _record(service, _resolution()))[0]

    await service.update_status(
        "demo", request.id, CrossPersonRequestStatus.RESOLVED, actor="U-alice"
    )

    told = [m for m in chat.sent if m.metadata.get("purpose") == "cross_person_request_resolved"]
    assert [m.text for m in told] == [
        "Alice Chen marked your review request resolved: API schema review"
    ]
    assert await _changes(store, request.id) == {"opened": "dev-1", "resolved": "U-alice"}


async def test_a_requester_who_resolves_their_own_ask_is_not_told_about_it() -> None:
    """They know, and the notice would say the person asked had resolved it."""
    service, store, chat = await _service_with(_ALICE, _REQUESTER)
    request = (await _record(service, _resolution()))[0]

    resolved = await service.update_status(
        "demo", request.id, CrossPersonRequestStatus.RESOLVED, actor="dev-1"
    )

    assert resolved is not None
    assert resolved.status is CrossPersonRequestStatus.RESOLVED
    told = [m for m in chat.sent if m.metadata.get("purpose") == "cross_person_request_resolved"]
    assert told == []
    assert await _changes(store, request.id) == {"opened": "dev-1", "resolved": "dev-1"}


async def test_a_refused_change_leaves_the_request_as_it_was_and_records_nothing() -> None:
    service, store, chat = await _service_with(_ALICE, _REQUESTER)
    request = (await _record(service, _resolution()))[0]
    sent = len(chat.sent)

    with pytest.raises(AuthorizationDenied):
        await service.update_status(
            "demo", request.id, CrossPersonRequestStatus.ACKNOWLEDGED, actor="dev-1"
        )
    with pytest.raises(AuthorizationDenied):
        await service.update_status(
            "demo", request.id, CrossPersonRequestStatus.RESOLVED, actor="U-manager"
        )
    with pytest.raises(RequestStatusNotSettable):
        await service.update_status(
            "demo", request.id, CrossPersonRequestStatus.DISMISSED, actor="dev-1"
        )

    stored = await store.get("demo", request.id)
    assert stored is not None
    assert stored.status is CrossPersonRequestStatus.OPEN
    assert await _changes(store, request.id) == {"opened": "dev-1"}
    assert len(chat.sent) == sent


async def test_acknowledging_never_reopens_a_resolved_request() -> None:
    """Acknowledging takes an open ask on; a closed request stays closed."""
    service, store, _ = await _service_with(_ALICE, _REQUESTER)
    request = (await _record(service, _resolution()))[0]
    await service.update_status(
        "demo", request.id, CrossPersonRequestStatus.RESOLVED, actor="U-alice"
    )

    again = await service.update_status(
        "demo", request.id, CrossPersonRequestStatus.ACKNOWLEDGED, actor="U-alice"
    )

    assert again is not None
    assert again.status is CrossPersonRequestStatus.RESOLVED
    assert await _changes(store, request.id) == {"opened": "dev-1", "resolved": "U-alice"}


async def test_a_reply_records_the_person_asked_as_who_moved_the_request() -> None:
    service, store, _ = await _service_with(_ALICE, _REQUESTER)
    request = (await _record(service, _resolution()))[0]

    await service.handle_counterpart_reply(
        _counterpart_reply(request.notify_correlation_id or "", "on it"), request
    )

    assert await _changes(store, request.id) == {"opened": "dev-1", "acknowledged": "U-alice"}


@pytest.mark.parametrize(
    ("member", "counterpart", "status", "allowed"),
    [
        ("U-alice", "U-alice", CrossPersonRequestStatus.ACKNOWLEDGED, True),
        ("dev-1", "U-alice", CrossPersonRequestStatus.ACKNOWLEDGED, False),
        ("U-manager", "U-alice", CrossPersonRequestStatus.ACKNOWLEDGED, False),
        ("U-alice", "U-alice", CrossPersonRequestStatus.RESOLVED, True),
        ("dev-1", "U-alice", CrossPersonRequestStatus.RESOLVED, True),
        ("U-manager", "U-alice", CrossPersonRequestStatus.RESOLVED, False),
        # Nobody matched yet: nobody to take it on, and the requester may close it.
        ("dev-1", None, CrossPersonRequestStatus.ACKNOWLEDGED, False),
        ("dev-1", None, CrossPersonRequestStatus.RESOLVED, True),
        # OpenProgram records these; no one sets them by hand.
        ("U-alice", "U-alice", CrossPersonRequestStatus.OPEN, False),
        ("dev-1", "U-alice", CrossPersonRequestStatus.DISMISSED, False),
        ("dev-1", None, CrossPersonRequestStatus.NEEDS_RESOLUTION, False),
    ],
)
def test_who_may_set_a_status_by_hand(
    member: str, counterpart: str | None, status: CrossPersonRequestStatus, allowed: bool
) -> None:
    request = CrossPersonRequest(
        tenant_id="demo",
        id="xreq-1",
        requester_id="dev-1",
        requester_chat_ref="U-dev",
        counterpart_id=counterpart,
        kind=CrossPersonRequestKind.REVIEW,
        note="API schema review",
        source_correlation_id="corr-1",
        status=CrossPersonRequestStatus.OPEN,
        created_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
        updated_at=datetime(2026, 1, 10, 9, 10, tzinfo=UTC),
    )

    assert may_set_status(request, member, status) is allowed


async def _changes(store: InMemoryGraphStore, request_id: str) -> dict[str, JsonScalar]:
    """Each transition recorded for the request, and who made it."""
    facts = await store.list_recent_facts("demo", sources=("cross_person_request",))
    return {
        str(fact.payload["transition"]): fact.payload["changed_by"]
        for fact in facts
        if fact.payload["request_id"] == request_id
    }


def _resolution(
    *,
    name: str = "Alice Chen",
    counterpart_id: str = "U-alice",
    kind: str = "review",
    note: str = "API schema review",
) -> CrossPersonRequestResolution:
    return CrossPersonRequestResolution(
        mention=CrossPersonMention(
            raw_name=name,
            kind=kind,
            note=note,
            email="alice@example.com",
        ),
        status=CrossPersonRequestStatus.OPEN,
        counterpart_id=counterpart_id,
        counterpart_display_name=name,
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
