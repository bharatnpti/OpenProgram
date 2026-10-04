"""A counterpart's short reply is read, and the requester hears when it matters (N12).

In R2 Omar answered Zoe's dependency request with "CHK-17 should merge
tomorrow". The model's answer was not bare JSON, the reply fell back to
"acknowledged", the ETA was lost and Zoe was never told. In R3 Liam's "LGTM,
one nit" on Noah's review went the same way.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.application.counterpart_replies import eta_phrase, read_counterpart_reply
from core.application.cross_person_service import CrossPersonRequestService
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.directory import DirectoryUser
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.messaging import ChatUserRef, InboundMessage
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider

R2 = datetime(2026, 10, 3, 18, 6, 59, tzinfo=UTC)
RESOLVED = CrossPersonRequestStatus.RESOLVED
ACKNOWLEDGED = CrossPersonRequestStatus.ACKNOWLEDGED


@pytest.mark.parametrize(
    ("reply", "status", "eta"),
    [
        # Live replies from the QA rounds.
        ("CHK-17 should merge tomorrow", ACKNOWLEDGED, "tomorrow"),
        ("LGTM, one nit", RESOLVED, None),
        ("approved", RESOLVED, None),
        ("Merged…", RESOLVED, None),
        ("Approved !1, looks good to merge.", RESOLVED, None),
        # Other short natural replies.
        ("done", RESOLVED, None),
        ("Done ✅", RESOLVED, None),
        ("lgtm 👍", RESOLVED, None),
        ("+1", RESOLVED, None),
        ("merged today", RESOLVED, None),
        ("will review by Friday", ACKNOWLEDGED, "by Friday"),
        ("should be done by EOD", ACKNOWLEDGED, "by end of day"),
        ("I'll get to it on Monday", ACKNOWLEDGED, "on Monday"),
        ("will finish it today", ACKNOWLEDGED, "today"),
        ("in 2 days", ACKNOWLEDGED, "in 2 days"),
        ("next week", ACKNOWLEDGED, "next week"),
        ("not done yet, maybe tomorrow", ACKNOWLEDGED, "tomorrow"),
        ("not approved yet", ACKNOWLEDGED, None),
        ("still reviewing", ACKNOWLEDGED, None),
        ("on it", ACKNOWLEDGED, None),
        ("ok", ACKNOWLEDGED, None),
    ],
)
def test_short_replies_are_read_without_the_model(
    reply: str, status: CrossPersonRequestStatus, eta: str | None
) -> None:
    reading = read_counterpart_reply(reply)

    assert reading is not None
    assert (reading.status, reading.eta) == (status, eta)


@pytest.mark.parametrize("reply", ["looked at it, left two comments", "see my thread above", ""])
def test_a_reply_this_cannot_read_goes_to_the_model(reply: str) -> None:
    assert read_counterpart_reply(reply) is None


def test_an_eta_is_a_fixed_phrase_never_the_reply_itself() -> None:
    assert eta_phrase("probably tmrw afternoon, the CI is slow") == "tomorrow"
    assert eta_phrase("by thurs") == "by Thursday"
    assert eta_phrase("in an hour") == "in 1 hour"
    assert eta_phrase("merged today") is None
    assert eta_phrase("left two comments") is None


async def test_omars_when_statement_acknowledges_and_tells_zoe_once() -> None:
    service, store, chat = await _service()
    request = await store.create(_request("Complete HTTP client upgrade for CHK-17", "dependency"))

    acknowledged = await service.handle_counterpart_reply(
        _reply(request, "CHK-17 should merge tomorrow"), request
    )
    again = await service.handle_counterpart_reply(
        _reply(request, "still should be in by Friday"), acknowledged
    )

    assert acknowledged.status is ACKNOWLEDGED
    assert again.status is ACKNOWLEDGED
    assert [(message.metadata["purpose"], message.text) for message in chat.sent] == [
        (
            "cross_person_request_acknowledged",
            "Omar Haddad acknowledged your dependency request and expects it tomorrow: "
            "Complete HTTP client upgrade for CHK-17",
        )
    ]
    assert chat.sent[0].metadata["idempotency_key"] == f"xreq-acknowledged:{request.id}"
    # The notice relays the fixed ETA phrase, never the reply.
    assert "should merge" not in chat.sent[0].text


async def test_an_acknowledgement_without_an_eta_tells_nobody() -> None:
    service, store, chat = await _service()
    request = await store.create(_request("review storefront-web !1 for CHK-8", "review"))

    acknowledged = await service.handle_counterpart_reply(_reply(request, "on it"), request)

    assert acknowledged.status is ACKNOWLEDGED
    assert chat.sent == []


async def test_lgtm_resolves_and_the_resolution_is_announced_once() -> None:
    service, store, chat = await _service()
    request = await store.create(_request("review CHK-6 on checkout-api !3", "review"))

    acknowledged = await service.handle_counterpart_reply(
        _reply(request, "will review by Friday"), request
    )
    resolved = await service.handle_counterpart_reply(
        _reply(request, "LGTM, one nit"), acknowledged
    )
    late = await service.handle_counterpart_reply(_reply(request, "thanks!"), resolved)

    assert resolved.status is RESOLVED
    assert late.status is RESOLVED
    assert [message.metadata["purpose"] for message in chat.sent] == [
        "cross_person_request_acknowledged",
        "cross_person_request_resolved",
    ]


async def test_a_fenced_model_answer_is_read() -> None:
    llm = _Llm('Sure!\n```json\n{"status": "resolved"}\n```')
    service, store, _ = await _service(llm)
    request = await store.create(_request("review storefront-web !1 for CHK-8", "review"))

    resolved = await service.handle_counterpart_reply(
        _reply(request, "looked at it, left two comments"), request
    )

    assert resolved.status is RESOLVED
    assert len(llm.requests) == 1


async def test_an_unreadable_model_answer_still_acknowledges() -> None:
    llm = _Llm("I think they are basically finished with it")
    service, store, chat = await _service(llm)
    request = await store.create(_request("review storefront-web !1 for CHK-8", "review"))

    acknowledged = await service.handle_counterpart_reply(
        _reply(request, "looked at it, left two comments"), request
    )

    assert acknowledged.status is ACKNOWLEDGED
    assert chat.sent == []


async def test_a_short_reply_never_reaches_the_model() -> None:
    llm = _Llm('{"status": "acknowledged"}')
    service, store, _ = await _service(llm)
    request = await store.create(_request("review CHK-6 on checkout-api !3", "review"))

    resolved = await service.handle_counterpart_reply(_reply(request, "LGTM, one nit"), request)

    assert resolved.status is RESOLVED
    assert llm.requests == []


# --- helpers --------------------------------------------------------------------


class _Llm:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            tenant_id=request.tenant_id,
            text=self.text,
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0.0, latency_ms=1.0
            ),
            trace_id="trace-1",
        )


async def _service(
    llm: _Llm | None = None,
) -> tuple[CrossPersonRequestService, InMemoryGraphStore, FakeChatProvider]:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(tenant_id="demo", external_id="U-zoe", display_name="Zoe Almeida"),
            DirectoryUser(tenant_id="demo", external_id="U-omar", display_name="Omar Haddad"),
        ]
    )
    chat = FakeChatProvider()
    service = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=directory,
        time_series_repository=store,
        llm_provider=llm,
    )
    return service, store, chat


def _request(note: str, kind: str) -> CrossPersonRequest:
    return CrossPersonRequest(
        tenant_id="demo",
        id="xreq-d78d",
        requester_id="U-zoe",
        requester_chat_ref="U-zoe",
        counterpart_id="U-omar",
        counterpart_display_name="Omar Haddad",
        kind=CrossPersonRequestKind(kind),
        note=note,
        source_correlation_id="checkin-zoe-r2",
        status=CrossPersonRequestStatus.OPEN,
        created_at=R2,
        updated_at=R2,
        notify_message_id="msg-1",
        notify_correlation_id="xreq-xreq-d78d",
    )


def _reply(request: CrossPersonRequest, text: str) -> InboundMessage:
    return InboundMessage(
        tenant_id="demo",
        user=ChatUserRef(tenant_id="demo", external_id="U-omar"),
        text=text,
        thread_id="msg-1",
        message_id=f"m-{text}",
        correlation_id=request.notify_correlation_id or "",
        received_at=R2,
    )
