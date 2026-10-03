"""Who a check-in's cross-person request can name, and what happens to it.

Only the tenant's members are counterparts. The chat workspace directory also
holds duplicate accounts, guests and outsiders; none of them may be offered as
a candidate or DMed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.application.counterparts import MemberContact, MemberDirectory, members_for_mention
from core.application.cross_person_service import CrossPersonRequestService
from core.application.status_collector import StatusCollector
from core.domain.cross_person import CrossPersonRequestStatus
from core.domain.directory import DirectoryUser
from core.domain.graph import Developer
from core.domain.identity import IdentityLink
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.status import CheckIn, CrossPersonMention
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


def _status_json(*requests: dict[str, object]) -> str:
    """A complete status the model extracts, naming the given requests."""
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
                "eta_answered": True,
                "requests": list(requests),
            },
        }
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
