"""The requester's "Got it" ack says who their check-in asked, and for what.

A reply that asks someone for a review, input or a dependency opens a request
and DMs that person. The requester used to read only "your update is
recorded" and learned who was asked when that person answered. The ack now
names them ("I've asked Noah Weber to review storefront-web !2 ..."), or says
plainly why nobody was asked: a duplicate of an open request, a member with no
chat id, a name no member has. It never says someone was asked unless their DM
went out, so the requests are recorded, and DMed, before the ack.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pytest

from config.settings import Settings
from core.application.checkin_ack_asks import AskAck, AskRouting, asks_note
from core.application.cross_person_service import CrossPersonRequestService
from core.application.status_collector import LATE_UPDATE_ACK_TEXT, StatusCollector
from core.domain.cross_person import (
    CrossPersonRequestResolution,
    CrossPersonRequestStatus,
    work_reference,
)
from core.domain.directory import DirectoryUser
from core.domain.errors import ProviderUnavailable
from core.domain.graph import Developer
from core.domain.identity import IdentityLink
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.status import CheckIn, CheckInCorrelation, CrossPersonMention
from core.ports.chat import ChatProvider
from core.ports.llm import LlmProvider
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeIssueTracker

TENANT = "demo"
ZOE = DirectoryUser(tenant_id=TENANT, external_id="U-zoe", display_name="Zoe Park")
NOAH = DirectoryUser(tenant_id=TENANT, external_id="U-noah", display_name="Noah Weber")
LIAM = DirectoryUser(tenant_id=TENANT, external_id="U-liam", display_name="Liam Chen")
CORRELATION = "corr-zoe-13oct"
ASKED_AT = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
REPLIED_AT = datetime(2026, 10, 13, 9, 20, tzinfo=UTC)
GOT_IT = "Got it \U0001f44d Thanks — your update is recorded."


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
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost_usd=0.0,
                latency_ms=1.0,
            ),
            trace_id=f"trace-{len(self.requests)}",
        )


@dataclass
class _Chat:
    """Records each DM with its recipient; a recipient in ``unreachable`` fails, as Slack does."""

    unreachable: set[str] = field(default_factory=set)
    sent: list[tuple[str, OutboundMessage]] = field(default_factory=list)

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        if user.external_id in self.unreachable:
            raise ProviderUnavailable("slack request failed: user_not_found")
        self.sent.append((user.external_id, message))
        return f"msg-{len(self.sent)}"

    async def open_thread(self, user: ChatUserRef) -> str:
        return f"thread-{user.external_id}"

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        return None

    def texts_to(self, recipient: str, purpose: str | None = None) -> list[str]:
        return [
            message.text
            for to, message in self.sent
            if to == recipient and (purpose is None or message.metadata.get("purpose") == purpose)
        ]

    def ack(self) -> str:
        (text,) = self.texts_to(ZOE.external_id, "status_ack")
        return text


def _status(*requests: dict[str, object], eta_answered: bool = True) -> str:
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": True,
            "question": None,
            "signals": {
                "progress_note": "CHK-8 is done and waits for review",
                "blockers": [],
                "eta_change_days": None,
                "blockers_answered": True,
                "eta_answered": eta_answered,
                "requests": list(requests),
            },
        }
    )


def _review(name: str, note: str = "Review storefront-web !2") -> dict[str, object]:
    return {"name": name, "kind": "review", "note": note, "email": None}


def _dependency(name: str, note: str) -> dict[str, object]:
    return {"name": name, "kind": "dependency", "note": note, "email": None}


async def _store(*members: DirectoryUser, correlation: str = CORRELATION) -> InMemoryGraphStore:
    """A tenant with Zoe and the given members, and Zoe's check-in of 13 Oct open."""
    store = InMemoryGraphStore()
    await InMemoryDirectoryUserRepository(store).upsert_users([ZOE, *members])
    for user in (ZOE, *members):
        await store.upsert_node(
            Developer(tenant_id=TENANT, id=user.external_id, name=user.display_name)
        )
    await _open_checkin(store, correlation, ASKED_AT)
    return store


async def _open_checkin(store: InMemoryGraphStore, correlation: str, asked_at: datetime) -> None:
    await store.record_checkin(
        CheckIn(
            tenant_id=TENANT,
            developer_id=ZOE.external_id,
            correlation_id=correlation,
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
            checkin_date=asked_at.date(),
        )
    )
    await store.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id=TENANT,
            correlation_id=correlation,
            developer_id=ZOE.external_id,
            chat_user_ref=ZOE.external_id,
            chat_thread_ref="D-zoe",
            outbound_message_id=f"out-{correlation}",
            asked_at=asked_at,
        )
    )


def _service(store: InMemoryGraphStore, chat: _Chat) -> CrossPersonRequestService:
    return CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=InMemoryDirectoryUserRepository(store),
        time_series_repository=store,
    )


def _collector(
    store: InMemoryGraphStore, chat: _Chat, llm: _ScriptedLlm, *, max_clarifications: int = 2
) -> StatusCollector:
    return StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=llm,
        status_repository=store,
        conversation_repository=store,
        directory_repository=InMemoryDirectoryUserRepository(store),
        identity_link_repository=store,
        graph_repository=store,
        cross_person_repository=store,
        model="test-model",
        checkin_max_clarifications=max_clarifications,
        request_recorder=_service(store, chat),
    )


def _reply(
    text: str, message_id: str = "msg-1", *, correlation: str = CORRELATION
) -> InboundMessage:
    return InboundMessage(
        tenant_id=TENANT,
        user=ChatUserRef(tenant_id=TENANT, external_id=ZOE.external_id),
        text=text,
        thread_id="D-zoe",
        message_id=message_id,
        correlation_id=correlation,
        received_at=REPLIED_AT,
    )


async def test_the_ack_names_the_person_asked_and_the_work_after_their_dm_went_out() -> None:
    store = await _store(NOAH)
    chat = _Chat()
    collector = _collector(store, chat, _ScriptedLlm([_status(_review("Noah"))]))

    outcome = await collector.handle_reply(_reply("CHK-8 done; storefront-web !2 needs Noah."))

    assert outcome.kind == "processed"
    assert chat.ack() == (
        f"{GOT_IT} I've asked Noah Weber to review storefront-web !2 "
        "and will tell you when they answer."
    )
    # Noah's DM went out first, so the ack says what happened, not what will.
    recipients = [to for to, _ in chat.sent]
    assert recipients == [NOAH.external_id, ZOE.external_id]
    assert "U-noah" not in chat.ack()
    # The outcome still carries the request; it was recorded once.
    (resolution,) = outcome.cross_person_requests
    assert resolution.counterpart_id == NOAH.external_id
    (request,) = await store.list_for_requester(TENANT, ZOE.external_id)
    assert request.notified


async def test_two_asks_are_listed_in_one_sentence() -> None:
    store = await _store(NOAH, LIAM)
    chat = _Chat()
    collector = _collector(
        store,
        chat,
        _ScriptedLlm(
            [_status(_review("Noah"), _dependency("Liam", "Liam's API contract for CHK-6"))]
        ),
    )

    await collector.handle_reply(
        _reply("storefront-web !2 waits for Noah; CHK-6 waits on Liam's API contract.")
    )

    assert chat.ack() == (
        f"{GOT_IT} I've asked Noah Weber to review storefront-web !2 and Liam Chen about "
        "CHK-6, and will tell you when they answer."
    )
    assert len(chat.texts_to(NOAH.external_id, "cross_person_request")) == 1
    assert len(chat.texts_to(LIAM.external_id, "cross_person_request")) == 1


async def test_a_repeat_of_an_open_request_says_so_and_nobody_is_asked_twice() -> None:
    store = await _store(NOAH)
    chat = _Chat()
    service = _service(store, chat)
    # Zoe asked Noah on 7 Oct, and the request is still open.
    (earlier,) = await service.record_from_checkin(
        tenant_id=TENANT,
        requester_id=ZOE.external_id,
        requester_chat_ref=ZOE.external_id,
        source_correlation_id="corr-zoe-7oct",
        resolutions=(
            CrossPersonRequestResolution(
                mention=CrossPersonMention(
                    raw_name="Noah", kind="review", note="Review storefront-web !2"
                ),
                status=CrossPersonRequestStatus.OPEN,
                counterpart_id=NOAH.external_id,
                counterpart_display_name=NOAH.display_name,
            ),
        ),
        observed_at=datetime(2026, 10, 7, 9, 30, tzinfo=UTC),
    )
    collector = _collector(store, chat, _ScriptedLlm([_status(_review("Noah"))]))

    await collector.handle_reply(_reply("Still waiting on Noah to review storefront-web !2."))

    assert chat.ack() == (
        f"{GOT_IT} Noah Weber already has your open review request for storefront-web !2 "
        "from 7 Oct; I'll tell you when they answer."
    )
    assert "I've asked" not in chat.ack()
    assert len(chat.texts_to(NOAH.external_id, "cross_person_request")) == 1
    assert [request.id for request in await store.list_for_requester(TENANT, "U-zoe")] == [
        earlier.id
    ]
    # Noah's answer still tells Zoe once that the ask is done.
    await service.handle_counterpart_reply(
        InboundMessage(
            tenant_id=TENANT,
            user=ChatUserRef(tenant_id=TENANT, external_id=NOAH.external_id),
            text="approved",
            thread_id=earlier.notify_message_id,
            message_id="msg-noah",
            correlation_id="corr-noah",
            received_at=datetime(2026, 10, 13, 11, 0, tzinfo=UTC),
        ),
        earlier,
    )
    assert chat.texts_to(ZOE.external_id, "cross_person_request_resolved") == [
        "Noah Weber marked your review request resolved: Review storefront-web !2"
    ]


async def test_a_counterpart_with_no_chat_id_is_named_and_not_claimed_as_asked() -> None:
    # Noah was configured by hand: a member id, a Jira link and no chat id.
    store = await _store()
    await store.upsert_node(Developer(tenant_id=TENANT, id="m-noah", name="Noah Weber"))
    await store.upsert_identity_link(
        IdentityLink(tenant_id=TENANT, developer_id="m-noah", jira_email="noah@acme.example")
    )
    chat = _Chat(unreachable={"m-noah"})
    collector = _collector(store, chat, _ScriptedLlm([_status(_review("Noah"))]))

    await collector.handle_reply(_reply("storefront-web !2 needs Noah's review."))

    assert chat.ack() == (
        f"{GOT_IT} Noah Weber has no chat id yet, so I couldn't ask them to review "
        "storefront-web !2."
    )
    assert "m-noah" not in chat.ack()
    (request,) = await store.list_for_requester(TENANT, ZOE.external_id)
    assert request.status is CrossPersonRequestStatus.OPEN
    assert not request.notified


async def test_a_failed_dm_to_a_linked_member_says_it_will_be_tried_again() -> None:
    store = await _store(NOAH)
    chat = _Chat(unreachable={NOAH.external_id})
    collector = _collector(store, chat, _ScriptedLlm([_status(_review("Noah"))]))

    await collector.handle_reply(_reply("storefront-web !2 needs Noah's review."))

    assert chat.ack() == (
        f"{GOT_IT} I couldn't reach Noah Weber in chat to ask them to review "
        "storefront-web !2; I'll try again."
    )


async def test_a_name_no_member_has_says_nobody_was_asked() -> None:
    store = await _store(NOAH)
    chat = _Chat()
    collector = _collector(
        store,
        chat,
        _ScriptedLlm([_status(_review("Sam Outsider", "Review CHK-8 on !1"))]),
        max_clarifications=0,
    )

    await collector.handle_reply(_reply("CHK-8 waits on Sam Outsider."))

    assert chat.ack() == (
        f"{GOT_IT} I couldn't find Sam Outsider on the team, so I haven't asked anyone "
        "to review CHK-8."
    )
    assert [to for to, _ in chat.sent] == [ZOE.external_id]


async def test_a_late_update_names_its_ask_after_the_late_lead() -> None:
    store = await _store(NOAH)
    # The check-in closed unanswered before Zoe replied.
    await store.consume_checkin_correlation(TENANT, CORRELATION, ASKED_AT)
    chat = _Chat()
    collector = _collector(store, chat, _ScriptedLlm([_status(_review("Noah"))]))

    outcome = await collector.handle_reply(_reply("Late one: storefront-web !2 needs Noah."))

    assert outcome.kind == "processed"
    (ack,) = chat.texts_to(ZOE.external_id, "status_late_update_ack")
    assert ack == (
        f"{LATE_UPDATE_ACK_TEXT} I've asked Noah Weber to review storefront-web !2 "
        "and will tell you when they answer."
    )
    assert [to for to, _ in chat.sent] == [NOAH.external_id, ZOE.external_id]


async def test_an_ask_stated_before_a_follow_up_is_named_in_the_final_ack() -> None:
    store = await _store(NOAH)
    chat = _Chat()
    collector = _collector(
        store,
        chat,
        _ScriptedLlm([_status(_review("Noah"), eta_answered=False), _status(eta_answered=True)]),
    )

    first = await collector.handle_reply(_reply("storefront-web !2 waits for Noah's review."))
    # Noah is asked the turn the ask is stated, while the ETA question goes out.
    assert first.kind == "clarifying"
    assert len(chat.texts_to(NOAH.external_id, "cross_person_request")) == 1
    assert chat.texts_to(ZOE.external_id, "status_ack") == []

    second = await collector.handle_reply(_reply("Friday.", "msg-2"))

    assert second.kind == "processed"
    assert chat.ack() == (
        f"{GOT_IT} I've asked Noah Weber to review storefront-web !2 "
        "and will tell you when they answer."
    )
    assert len(chat.texts_to(NOAH.external_id, "cross_person_request")) == 1


class _Registry(ServiceRegistry):
    """The memory-mode registry with a scripted model and one recording chat."""

    def __init__(self, settings: Settings, texts: list[str]) -> None:
        super().__init__(settings)
        self.llm = _ScriptedLlm(texts)
        self.chat = _Chat()

    def llm_provider(self) -> LlmProvider:
        return self.llm

    def chat_provider(self) -> ChatProvider:
        return self.chat


async def test_the_registry_records_the_ask_once_through_the_collector(
    settings: Settings,
) -> None:
    registry = _Registry(
        settings.model_copy(update={"chat_provider": "fake", "issue_tracker_provider": "fake"}),
        [_status(_review("Noah"))],
    )
    store = registry._memory_graph_store()
    await InMemoryDirectoryUserRepository(store).upsert_users([ZOE, NOAH])
    for user in (ZOE, NOAH):
        await store.upsert_node(
            Developer(tenant_id=TENANT, id=user.external_id, name=user.display_name)
        )
    await _open_checkin(store, CORRELATION, ASKED_AT)

    result = await registry.process_inbound_message(
        _reply("storefront-web !2 needs Noah's review.", correlation="inbound-msg-1"),
        allow_reprocess=True,
    )

    assert result.status == "processed"
    assert len(registry.chat.texts_to(NOAH.external_id, "cross_person_request")) == 1
    assert registry.chat.ack().endswith(
        "I've asked Noah Weber to review storefront-web !2 and will tell you when they answer."
    )
    (request,) = await store.list_for_requester(TENANT, ZOE.external_id)
    assert request.source_correlation_id == CORRELATION
    assert request.requester_chat_ref == ZOE.external_id


def test_a_long_note_names_only_the_people_and_one_that_does_not_fit_is_left_out() -> None:
    asks = [
        AskAck(routing=AskRouting.ASKED, kind="review", name=name, reference="storefront-web !2")
        for name in ("Noah Weber", "Liam Chen", "Asha Rao")
    ] + [AskAck(routing=AskRouting.NOT_FOUND, kind="input", name="Sam", reference="CHK-8")]

    full = asks_note(asks, 320)
    short = asks_note(asks, 120)

    assert full is not None and full.startswith(
        "I've asked Noah Weber to review storefront-web !2, Liam Chen to review"
    )
    assert short == (
        "I've asked Noah Weber, Liam Chen and Asha Rao; I'll tell you when they answer. "
        "I haven't asked Sam."
    )
    assert asks_note(asks, 40) is None
    assert asks_note([], 320) is None


def test_an_open_request_with_and_without_work_named() -> None:
    since = date(2026, 10, 7)
    note = asks_note(
        [
            AskAck(routing=AskRouting.ASKED, kind="dependency", name="Liam Chen", reference=None),
            AskAck(
                routing=AskRouting.ALREADY_OPEN,
                kind="review",
                name="Noah Weber",
                reference=None,
                since=since,
            ),
        ],
        320,
    )

    assert note == (
        "I've asked Liam Chen about what you're waiting on, and Noah Weber already has your "
        "open review request from 7 Oct; I'll tell you when they answer."
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Review storefront-web !2", "storefront-web !2"),
        ("Review CHK-8 on !1", "CHK-8"),
        ("Review !1 for CHK-8", "CHK-8"),
        ("checkout-api MR !3 and CHK-2", "checkout-api MR !3"),
        ("merge !1 please", "!1"),
        ("Need the API contract", None),
    ],
)
def test_the_work_a_request_names_is_kept_as_it_is_written(text: str, expected: str | None) -> None:
    assert work_reference(text) == expected
