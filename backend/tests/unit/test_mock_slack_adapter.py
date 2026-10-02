from __future__ import annotations

from datetime import UTC, datetime

from core.domain.messaging import ChatUserRef, OutboundMessage
from infra.adapters.chat.mock_slack import (
    InMemoryMockSlackStore,
    MockSlackChatAdapter,
    MockSlackHttpClient,
    MockSlackMessage,
    slack_event_payload,
)
from infra.adapters.chat.rate_limit import InMemoryRateLimiter
from infra.adapters.chat.slack import SlackChatWebhookMapper
from tests.contract.contracts import assert_chat_contract


async def test_mock_slack_chat_adapter_records_outbound_metadata() -> None:
    store = InMemoryMockSlackStore()
    adapter = MockSlackChatAdapter(
        tenant_id="demo",
        store=store,
        rate_limiter=InMemoryRateLimiter(),
    )
    user = ChatUserRef(tenant_id="demo", external_id="U1001", display_name="Asha Rao")

    message_id = await adapter.send_dm(
        user,
        OutboundMessage(
            tenant_id="demo",
            text="status?",
            correlation_id="corr-1",
            metadata={"purpose": "status_checkin"},
        ),
    )

    messages = await store.list_messages("demo")
    assert message_id == messages[0].message_id
    assert messages[0].channel_id == "D1001"
    assert messages[0].user_id == "U1001"
    assert messages[0].correlation_id == "corr-1"
    assert messages[0].purpose == "status_checkin"


async def test_mock_slack_chat_adapter_satisfies_chat_contract() -> None:
    await assert_chat_contract(
        MockSlackChatAdapter(
            tenant_id="demo",
            store=InMemoryMockSlackStore(),
            rate_limiter=InMemoryRateLimiter(),
        )
    )


async def test_mock_slack_reply_uses_slack_shaped_webhook_payload() -> None:
    store = InMemoryMockSlackStore()
    channel_id = await store.open_channel("demo", "U1001")
    outbound = await store.record_bot_message(
        tenant_id="demo",
        channel_id=channel_id,
        text="status?",
        correlation_id="corr-1",
        metadata={"purpose": "status_checkin"},
        created_at=datetime(2026, 1, 13, 9, 30, tzinfo=UTC),
    )

    reply = await store.record_user_reply(
        tenant_id="demo",
        reply_to_message_id=outbound.message_id,
        text="Done, no blockers.",
        created_at=datetime(2026, 1, 13, 9, 35, tzinfo=UTC),
    )
    mapped = SlackChatWebhookMapper("demo").map_webhook(slack_event_payload(reply), "request-1")

    assert mapped.user.external_id == "U1001"
    assert mapped.thread_id == channel_id
    assert mapped.message_id == reply.message_id
    assert mapped.text == "Done, no blockers."


async def test_mock_slack_thread_reply_carries_the_thread_and_no_inherited_correlation() -> None:
    store = InMemoryMockSlackStore()
    channel_id = await store.open_channel("demo", "U1002")
    request_dm = await store.record_bot_message(
        tenant_id="demo",
        channel_id=channel_id,
        text="Asha Rao asked for your review: API schema",
        correlation_id="xreq-1",
        metadata={"purpose": "cross_person_request"},
        created_at=datetime(2026, 1, 13, 9, 30, tzinfo=UTC),
    )

    threaded = await store.record_user_reply(
        tenant_id="demo",
        reply_to_message_id=request_dm.message_id,
        text="on it",
        created_at=datetime(2026, 1, 13, 9, 35, tzinfo=UTC),
        in_thread=True,
    )
    plain = await store.record_user_reply(
        tenant_id="demo",
        reply_to_message_id=request_dm.message_id,
        text="a channel reply",
        created_at=datetime(2026, 1, 13, 9, 36, tzinfo=UTC),
    )
    mapped = SlackChatWebhookMapper("demo").map_webhook(slack_event_payload(threaded), "request-1")
    mapped_plain = SlackChatWebhookMapper("demo").map_webhook(slack_event_payload(plain), "r-2")

    # Slack's thread_ts is the parent's ts, and the reply has a fresh id of its
    # own, so routing has to go by the thread rather than by correlation.
    assert threaded.thread_id == request_dm.message_id
    assert mapped is not None
    assert mapped.thread_id == request_dm.message_id
    assert mapped.correlation_id == "request-1"
    # A channel reply is unchanged: the channel is its thread, the question's
    # correlation id rides along, and nothing marks it as a thread reply.
    assert plain.thread_id is None
    assert mapped_plain is not None
    assert mapped_plain.thread_id == channel_id
    assert mapped_plain.correlation_id == "xreq-1"
    assert MockSlackMessage.from_dict(threaded.to_dict()) == threaded
    assert MockSlackMessage.from_dict(plain.to_dict()) == plain


async def test_mock_slack_http_client_lists_local_users() -> None:
    client = MockSlackHttpClient(tenant_id="demo", store=InMemoryMockSlackStore())

    payload = await client.list_users()

    assert payload["ok"] is True
    members = payload["members"]
    assert isinstance(members, list)
    ids = [member["id"] for member in members]
    # The roster is demo data that grows; what matters is that it is a stable,
    # unique, well-formed set every caller sees the same way.
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert all(isinstance(member_id, str) and member_id.startswith("U") for member_id in ids)
    assert ids[:3] == ["U1001", "U1002", "U1003"]
    assert all(member["profile"]["real_name"] for member in members)
    assert all(member["deleted"] is False for member in members)
