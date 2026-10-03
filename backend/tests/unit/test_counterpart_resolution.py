"""Who a check-in's cross-person request can name, and what happens to it.

Only the tenant's members are counterparts. The chat workspace directory also
holds duplicate accounts, guests and outsiders; none of them may be offered as
a candidate or DMed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from core.application.counterparts import (
    MemberContact,
    MemberDirectory,
    member_named_in_answer,
    members_for_mention,
)
from core.application.cross_person_service import CrossPersonRequestService
from core.application.status_collector import ReplyOutcome, StatusCollector
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestStatus,
    is_placeholder_name,
)
from core.domain.directory import DirectoryUser
from core.domain.graph import Developer, GraphNode, NodeKind
from core.domain.identity import IdentityLink
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.status import CheckIn, CrossPersonMention
from infra.adapters.chat.slack import SlackChatWebhookMapper
from infra.adapters.chat.slack_text import plain_text
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from tests.contract.fakes import FakeIssueTracker

TENANT = "demo"
REQUESTER = "U-zoe"
NOAH = DirectoryUser(
    tenant_id=TENANT,
    external_id="U-noah",
    display_name="Noah Weber",
    email="noah@acme.example",
)
# A second account with the same name, in the workspace but not a member.
NOAH_DUPLICATE = DirectoryUser(
    tenant_id=TENANT,
    external_id="U-noah-dup",
    display_name="Noah Weber",
    email="noah.weber@elsewhere.example",
)
NOAH_BACKEND = DirectoryUser(
    tenant_id=TENANT,
    external_id="U-noah-backend",
    display_name="Noah Weber",
    email="noah.backend@acme.example",
)
OUTSIDER = DirectoryUser(
    tenant_id=TENANT,
    external_id="U-sam",
    display_name="Sam Outsider",
    email="sam@outside.example",
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
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost_usd=0.0,
                latency_ms=1.0,
            ),
            trace_id=f"trace-{len(self.requests)}",
        )


@dataclass
class _RecordingChat:
    sent: list[tuple[str, OutboundMessage]] = field(default_factory=list)

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        self.sent.append((user.external_id, message))
        return f"msg-{len(self.sent)}"

    async def open_thread(self, user: ChatUserRef) -> str:
        return f"thread-{user.external_id}"

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        return None

    def texts_to(self, recipient: str) -> list[str]:
        return [message.text for to, message in self.sent if to == recipient]

    def purposes(self) -> list[object]:
        return [message.metadata.get("purpose") for _, message in self.sent]


def _status_json(*requests: dict[str, object], eta_answered: bool = True) -> str:
    """A status the model extracts, naming the given requests."""
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": True,
            "question": None,
            "signals": {
                "progress_note": "CHK-8 is done and waits for review",
                "blockers": ["CHK-8 waits for review"],
                "eta_change_days": None,
                "blockers_answered": True,
                "eta_answered": eta_answered,
                "requests": list(requests),
            },
        }
    )


_NOT_A_STATUS = json.dumps(
    {"is_status_update": False, "sufficient": False, "question": None, "signals": None}
)


def _review_of_chk8(name: str, email: str | None = None) -> dict[str, object]:
    return {"name": name, "kind": "review", "note": "Review CHK-8 on !1", "email": email}


def _message(text: str, message_id: str = "msg-1") -> InboundMessage:
    return InboundMessage(
        tenant_id=TENANT,
        user=ChatUserRef(tenant_id=TENANT, external_id=REQUESTER),
        text=text,
        thread_id="thread-zoe",
        message_id=message_id,
        correlation_id="corr-zoe",
        received_at=datetime(2026, 10, 3, 12, 15, tzinfo=UTC),
    )


async def _store_with(
    *, members: tuple[DirectoryUser, ...], others: tuple[DirectoryUser, ...] = ()
) -> InMemoryGraphStore:
    """A tenant whose directory holds members and others, with Zoe's check-in open."""
    store = InMemoryGraphStore()
    await InMemoryDirectoryUserRepository(store).upsert_users([*members, *others])
    for user in members:
        await store.upsert_node(
            Developer(
                tenant_id=TENANT,
                id=user.external_id,
                name=user.display_name,
                metadata={"email": user.email} if user.email else {},
            )
        )
    await store.record_checkin(
        CheckIn(
            tenant_id=TENANT,
            developer_id=REQUESTER,
            correlation_id="corr-zoe",
            asked_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    return store


def _collector(
    store: InMemoryGraphStore,
    llm: _ScriptedLlm,
    chat: _RecordingChat,
    *,
    max_clarifications: int = 2,
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
        checkin_ack_enabled=False,
    )


def _service(store: InMemoryGraphStore, chat: _RecordingChat) -> CrossPersonRequestService:
    return CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=InMemoryDirectoryUserRepository(store),
        time_series_repository=store,
    )


async def test_a_member_wins_over_a_non_member_of_the_same_name_without_a_question() -> None:
    store = await _store_with(members=(NOAH,), others=(NOAH_DUPLICATE,))
    chat = _RecordingChat()
    collector = _collector(store, _ScriptedLlm([_status_json(_review_of_chk8("Noah"))]), chat)

    outcome = await collector.handle_reply(
        _message("CHK-8 is done on !1 but blocked until Noah reviews it.")
    )

    assert outcome.kind == "processed"
    assert chat.sent == []  # no "which Noah Weber?" question
    (resolution,) = outcome.cross_person_requests
    assert resolution.status is CrossPersonRequestStatus.OPEN
    assert resolution.counterpart_id == "U-noah"
    assert resolution.counterpart_email == "noah@acme.example"


async def test_a_name_only_a_non_member_has_stays_unresolved_and_nobody_is_dmed() -> None:
    store = await _store_with(members=(NOAH,), others=(OUTSIDER,))
    chat = _RecordingChat()
    collector = _collector(
        store,
        _ScriptedLlm([_status_json(_review_of_chk8("Sam Outsider"))]),
        chat,
        max_clarifications=0,
    )

    outcome = await collector.handle_reply(_message("Waiting on Sam Outsider to review CHK-8."))
    created = await _service(store, chat).record_from_checkin(
        tenant_id=TENANT,
        requester_id=REQUESTER,
        requester_chat_ref=REQUESTER,
        source_correlation_id="corr-zoe",
        resolutions=outcome.cross_person_requests,
    )

    assert outcome.kind == "processed"
    (request,) = created
    assert request.status is CrossPersonRequestStatus.NEEDS_RESOLUTION
    assert request.counterpart_id is None
    assert chat.texts_to("U-sam") == []
    assert "cross_person_request" not in chat.purposes()


async def test_a_non_member_is_never_offered_as_a_candidate() -> None:
    store = await _store_with(members=(NOAH,), others=(OUTSIDER,))
    chat = _RecordingChat()
    collector = _collector(store, _ScriptedLlm([_status_json(_review_of_chk8("Sam"))]), chat)

    outcome = await collector.handle_reply(_message("Waiting on Sam to review CHK-8."))

    assert outcome.kind == "clarifying"
    ((recipient, question),) = chat.sent
    assert recipient == REQUESTER
    assert "could not find Sam" in question.text
    assert "sam@outside.example" not in question.text
    assert "in the directory" not in question.text


async def test_members_are_matched_by_name_words_email_and_chat_id() -> None:
    members = (
        MemberContact(member_id="m-noah", chat_id="U0NOAH", name="Noah Weber", email="n@x.io"),
        MemberContact(member_id="m-mira", chat_id="U0MIRA", name="Mira Patel"),
    )

    def matched(name: str, email: str | None = None) -> list[str]:
        mention = CrossPersonMention(raw_name=name, kind="review", note="n", email=email)
        return [member.member_id for member in members_for_mention(mention, members)]

    assert matched("Noah") == ["m-noah"]
    assert matched("noah w") == ["m-noah"]
    assert matched("Ira") == []  # not a word start of "Mira"
    assert matched("@U0NOAH") == ["m-noah"]
    assert matched("m-noah") == ["m-noah"]
    assert matched("Noah", email="N@X.io") == ["m-noah"]
    # An address no member has names nobody, even with a member's name on it.
    assert matched("Noah Weber", email="noah.weber@elsewhere.example") == []


async def test_a_member_whose_chat_account_is_deactivated_is_not_a_candidate() -> None:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(tenant_id=TENANT, external_id="U-left", display_name="Lee Left"),
            DirectoryUser(tenant_id=TENANT, external_id="U-chat", display_name="Kim Linked"),
        ]
    )
    await directory.deactivate_missing(TENANT, ["U-chat"])
    await store.upsert_node(Developer(tenant_id=TENANT, id="U-left", name="Lee Left"))
    await store.upsert_node(Developer(tenant_id=TENANT, id="dev-kim", name="Kim Linked"))
    await store.upsert_node(Developer(tenant_id=TENANT, id="dev-hand", name="Hand Added"))
    await store.upsert_identity_link(
        IdentityLink(tenant_id=TENANT, developer_id="dev-kim", chat_user_id="U-chat")
    )

    members = await MemberDirectory(
        graph_repository=store,
        identity_link_repository=store,
        directory_repository=directory,
    ).active_members(TENANT)

    assert [(member.member_id, member.chat_id) for member in members] == [
        ("dev-hand", "dev-hand"),
        ("dev-kim", "U-chat"),
    ]


class _CountingGraph:
    """The store, counting how often the member pool is read."""

    def __init__(self, store: InMemoryGraphStore) -> None:
        self._store = store
        self.member_reads = 0

    async def list_nodes(self, tenant_id: str, kind: NodeKind | None = None) -> list[GraphNode]:
        if kind is NodeKind.DEVELOPER:
            self.member_reads += 1
        return await self._store.list_nodes(tenant_id, kind)

    def __getattr__(self, name: str) -> object:
        return getattr(self._store, name)


async def test_a_role_word_is_no_request_no_lookup_and_no_could_not_find() -> None:
    store = await _store_with(members=(NOAH,), others=(NOAH_DUPLICATE,))
    graph = _CountingGraph(store)
    chat = _RecordingChat()
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=_ScriptedLlm([_status_json(_review_of_chk8("reviewer"))]),
        status_repository=store,
        conversation_repository=store,
        directory_repository=InMemoryDirectoryUserRepository(store),
        identity_link_repository=store,
        graph_repository=graph,  # type: ignore[arg-type]
        model="test-model",
        checkin_ack_enabled=False,
    )

    outcome = await collector.handle_reply(_message("INS-2 MR !1 is waiting on reviewer."))

    assert outcome.kind == "processed"
    assert outcome.cross_person_requests == ()
    assert graph.member_reads == 0
    assert all("could not find" not in message.text for _, message in chat.sent)
    checkin = await store.checkin_by_correlation(TENANT, "corr-zoe")
    assert checkin is not None and checkin.signals is not None
    assert checkin.signals.requests == ()
    assert checkin.signals.blockers == ("CHK-8 waits for review",)


@pytest.mark.parametrize(
    "name",
    [
        "reviewer",
        "Reviewers",
        "someone",
        "anyone",
        "someone from QA",
        "the team",
        "QA",
        "QA team",
        "the payments team",
        "a dev",
        "my lead",
        "the tech lead",
        "no one",
    ],
)
def test_role_and_placeholder_words_are_not_names(name: str) -> None:
    assert is_placeholder_name(name)


@pytest.mark.parametrize("name", ["Noah", "Noah Weber", "Dev", "Dev Patel", "Lea", "Team Rocket"])
def test_people_are_names(name: str) -> None:
    assert not is_placeholder_name(name)


# --- Defect R1-4: the request survives "who did you mean?" ---------------------


def _slack_message(text: str, ts: str) -> InboundMessage:
    """A DM reply as the Slack transports hand it over, through the real mapper."""
    message = SlackChatWebhookMapper(tenant_id=TENANT).map_webhook(
        {
            "event": {
                "type": "message",
                "user": REQUESTER,
                "text": text,
                "ts": ts,
                "channel": "D-zoe",
            }
        },
        "corr-zoe",
    )
    assert message is not None
    return replace(message, received_at=datetime(2026, 10, 3, 12, 16, tzinfo=UTC))


async def _handle(
    collector: StatusCollector,
    service: CrossPersonRequestService,
    message: InboundMessage,
) -> ReplyOutcome:
    """One reply as the registry processes it: collect, then record its requests."""
    outcome = await collector.handle_reply(message)
    if outcome.cross_person_requests:
        await service.record_from_checkin(
            tenant_id=TENANT,
            requester_id=REQUESTER,
            requester_chat_ref=REQUESTER,
            source_correlation_id="corr-zoe",
            resolutions=outcome.cross_person_requests,
            observed_at=message.received_at,
        )
    return outcome


async def _raised(store: InMemoryGraphStore) -> list[CrossPersonRequest]:
    return await store.list_for_requester(TENANT, REQUESTER)


async def test_two_members_of_one_name_are_settled_by_a_slack_mailto_answer() -> None:
    store = await _store_with(members=(NOAH, NOAH_BACKEND), others=(NOAH_DUPLICATE,))
    chat = _RecordingChat()
    llm = _ScriptedLlm(
        [
            _status_json(_review_of_chk8("Noah")),
            # As in R1: the model reads the answer on its own, keeps no request
            # and the ETA is missing, so the next question is about the ETA.
            _status_json(eta_answered=False),
            _status_json(),
        ]
    )
    collector = _collector(store, llm, chat)
    service = _service(store, chat)

    first = await _handle(
        collector, service, _message("CHK-8 is done on !1 but blocked until Noah reviews it.")
    )

    assert first.kind == "clarifying"
    question = chat.texts_to(REQUESTER)[-1]
    assert "noah@acme.example" in question
    assert "noah.backend@acme.example" in question
    assert "noah.weber@elsewhere.example" not in question
    (waiting,) = await _raised(store)
    assert waiting.status is CrossPersonRequestStatus.NEEDS_RESOLUTION
    assert "cross_person_request" not in chat.purposes()

    answer = _slack_message(
        "The backend one, <mailto:noah.backend@acme.example|noah.backend@acme.example>", "1.2"
    )
    assert answer.text == "The backend one, noah.backend@acme.example"
    second = await _handle(collector, service, answer)

    assert second.kind == "clarifying"
    assert chat.texts_to(REQUESTER)[-1] == "Thanks. What is your ETA to finish it?"
    (request,) = await _raised(store)
    assert request.id == waiting.id
    assert request.status is CrossPersonRequestStatus.OPEN
    assert request.counterpart_id == "U-noah-backend"
    assert request.counterpart_email == "noah.backend@acme.example"
    (dm,) = chat.texts_to("U-noah-backend")
    assert "Review CHK-8 on !1" in dm
    assert chat.texts_to("U-noah") == []
    assert chat.texts_to("U-noah-dup") == []

    # A redelivered answer neither reassigns the request nor DMs a second time.
    await service.record_from_checkin(
        tenant_id=TENANT,
        requester_id=REQUESTER,
        requester_chat_ref=REQUESTER,
        source_correlation_id="corr-zoe",
        resolutions=second.cross_person_requests,
    )
    third = await _handle(collector, service, _message("CHK-8 ETA is unchanged.", "msg-3"))

    assert third.kind == "processed"
    assert [item.id for item in await _raised(store)] == [waiting.id]
    assert len(chat.texts_to("U-noah-backend")) == 1


async def test_an_answer_naming_neither_member_keeps_the_request_unresolved() -> None:
    store = await _store_with(members=(NOAH, NOAH_BACKEND))
    chat = _RecordingChat()
    llm = _ScriptedLlm(
        [
            _status_json(_review_of_chk8("Noah")),
            _status_json(_review_of_chk8("Noah")),
            _status_json(),
        ]
    )
    collector = _collector(store, llm, chat)
    service = _service(store, chat)

    await _handle(collector, service, _message("Blocked until Noah reviews CHK-8."))
    second = await _handle(collector, service, _message("The one on payments.", "msg-2"))

    assert second.kind == "clarifying"
    assert chat.texts_to(REQUESTER)[-1].startswith("Did you mean Noah Weber")
    third = await _handle(collector, service, _message("Payments, as I said.", "msg-3"))

    assert third.kind == "processed"
    (request,) = await _raised(store)
    assert request.status is CrossPersonRequestStatus.NEEDS_RESOLUTION
    assert request.counterpart_id is None
    assert request.raw_name == "Noah"
    assert "cross_person_request" not in chat.purposes()


async def test_a_mention_settles_the_request_even_when_the_model_sees_no_status() -> None:
    store = await _store_with(members=(NOAH, NOAH_BACKEND))
    chat = _RecordingChat()
    llm = _ScriptedLlm([_status_json(_review_of_chk8("Noah Weber")), _NOT_A_STATUS])
    collector = _collector(store, llm, chat)
    service = _service(store, chat)

    await _handle(collector, service, _message("Blocked until Noah Weber reviews CHK-8."))
    answer = _slack_message("<@U-noah-backend>", "1.2")
    outcome = await _handle(collector, service, answer)

    assert answer.text == "@U-noah-backend"
    assert outcome.kind == "acknowledged"
    (request,) = await _raised(store)
    assert request.status is CrossPersonRequestStatus.OPEN
    assert request.counterpart_id == "U-noah-backend"
    assert len(chat.texts_to("U-noah-backend")) == 1


async def test_a_request_stated_with_a_missing_eta_is_recorded_that_turn() -> None:
    store = await _store_with(members=(NOAH,))
    chat = _RecordingChat()
    llm = _ScriptedLlm([_status_json(_review_of_chk8("Noah"), eta_answered=False), _status_json()])
    collector = _collector(store, llm, chat)
    service = _service(store, chat)

    first = await _handle(collector, service, _message("Blocked until Noah reviews CHK-8."))
    second = await _handle(collector, service, _message("ETA is Wednesday.", "msg-2"))

    assert (first.kind, second.kind) == ("clarifying", "processed")
    (request,) = await _raised(store)
    assert request.status is CrossPersonRequestStatus.OPEN
    assert request.counterpart_id == "U-noah"
    assert len(chat.texts_to("U-noah")) == 1


def test_an_answer_settles_on_a_word_the_question_did_not_already_have() -> None:
    alex = MemberContact(member_id="U1", chat_id="U1", name="Alex Chen", email="a@x.io")
    alexa = MemberContact(member_id="U2", chat_id="U2", name="Alexa Roy", email="ar@x.io")
    liam = MemberContact(member_id="U3", chat_id="U3", name="Liam Chen")
    members = (alex, alexa, liam)
    asked = CrossPersonMention(raw_name="Alex", kind="input", note="schema")
    candidates = (alex, alexa)

    def pick(answer: str, offered: tuple[MemberContact, ...] = candidates) -> str | None:
        member = member_named_in_answer(answer, asked, offered, members)
        return member.member_id if member is not None else None

    assert pick("Alexa") == "U2"
    assert pick("Alex Chen, the backend one") == "U1"
    assert pick("Alex.") is None
    assert pick("Just Alex") is None
    assert pick("<mailto:ar@x.io|ar@x.io>") == "U2"
    assert pick("@U1") == "U1"
    assert pick("a@x.io or ar@x.io") is None
    # Nobody offered: only a full name counts, not a first name in passing.
    assert pick("Liam Chen", offered=()) == "U3"
    assert pick("Liam said so", offered=()) is None


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        ("<mailto:noah@acme.io|noah@acme.io>", "noah@acme.io"),
        ("ask <@U0123ABCD> please", "ask @U0123ABCD please"),
        ("<@U0123ABCD|noah>", "@U0123ABCD"),
        ("on <https://git.example/mr/1|!1>", "on !1 (https://git.example/mr/1)"),
        ("<https://git.example/mr/1>", "https://git.example/mr/1"),
        ("<http://example.com|example.com>", "http://example.com"),
        ("in <#C123|payments>", "in #payments"),
        ("<!here> &lt;b&gt; &amp; co", "@here <b> & co"),
        ("<!subteam^S1|@payments> look", "@payments look"),
        ("no markup at all", "no markup at all"),
    ],
)
def test_slack_markup_is_unwrapped_into_plain_text(raw: str, plain: str) -> None:
    assert plain_text(raw) == plain
