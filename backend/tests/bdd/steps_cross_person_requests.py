from __future__ import annotations

import asyncio
import json
import zlib
from dataclasses import dataclass, field
from typing import Any, cast

from pytest_bdd import given, parsers, then, when

from config.settings import Settings
from core.domain.directory import DirectoryUser
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.ports.llm import LlmProvider
from tests.bdd.fixtures import World, WorldRegistry, mock_slack_settings
from tests.bdd.steps_common import _submit_reply


@dataclass
class _ScriptedLlmProvider:
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
            trace_id=f"trace-cross-person-{len(self.requests)}",
        )


class _ScriptedLlmRegistry(WorldRegistry):
    def __init__(self, settings: Settings, texts: list[str]) -> None:
        super().__init__(settings)
        self._scripted_llm = _ScriptedLlmProvider(texts)

    def llm_provider(self) -> LlmProvider:
        return self._scripted_llm


_CHECKIN_QUESTION = "Can you share progress, blockers, and ETA changes?"


def _signals_reply(name: str, kind: str, note: str, *, email: str | None = None) -> str:
    """What the scripted model returns for a reply that asks `name` for something."""
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": True,
            "question": None,
            "signals": {
                "progress_note": f"Blocked on {note}",
                "blockers": [note],
                "eta_change_days": None,
                "blockers_answered": True,
                "eta_answered": True,
                "requests": [{"name": name, "kind": kind, "note": note, "email": email}],
            },
        }
    )


@given("the cross-person request stack is running with Liam review extraction")
def _given_liam_review_stack(world: World) -> None:
    # No cross_person_auto_notify override: these scenarios run on the default.
    _start_scripted_stack(
        world, [_CHECKIN_QUESTION, _signals_reply("Liam Chen", "review", "API schema review")]
    )


@given(
    "the cross-person request stack is running with Liam review extraction and auto notify disabled"
)
def _given_liam_review_stack_without_auto_notify(world: World) -> None:
    _start_scripted_stack(
        world,
        [_CHECKIN_QUESTION, _signals_reply("Liam Chen", "review", "API schema review")],
        auto_notify=False,
    )


@given("the cross-person request stack is running with ambiguous Alex responses")
def _given_ambiguous_alex_stack(world: World) -> None:
    _start_scripted_stack(
        world,
        [
            _CHECKIN_QUESTION,
            _signals_reply("Alex", "input", "schema confirmation"),
            _signals_reply("Alex", "input", "schema confirmation", email="alexa.roy@example.com"),
        ],
    )


@given(parsers.parse('the cross-person request stack is running where every reply names "{name}"'))
def _given_stack_naming_the_same_person_every_reply(world: World, name: str) -> None:
    # One initial question, then the same unresolved person for every reply the
    # check-in will take before it gives up asking.
    _start_scripted_stack(
        world,
        [_CHECKIN_QUESTION] + [_signals_reply(name, "review", "API schema review")] * 3,
    )


@given("the mock Slack directory is synced")
def _given_directory_synced(world: World) -> None:
    assert world.client is not None
    response = world.client.post("/config/directory/sync")
    assert response.status_code == 200, response.text


@given("the directory contains ambiguous Alex users")
def _given_ambiguous_alex_users(world: World) -> None:
    registry = world.registry()
    asyncio.run(
        registry.directory_user_repository().upsert_users(
            [
                DirectoryUser(
                    tenant_id="demo",
                    external_id="U2001",
                    display_name="Alex Chen",
                    email="alex.chen@example.com",
                ),
                DirectoryUser(
                    tenant_id="demo",
                    external_id="U2002",
                    display_name="Alexa Roy",
                    email="alexa.roy@example.com",
                ),
            ]
        )
    )


@when(parsers.parse('member "{member_id}" replies to the cross-person request with text "{text}"'))
def _when_counterpart_replies(world: World, member_id: str, text: str) -> None:
    message = _latest_cross_person_bot_message(world, member_id)
    world.response = cast(Any, _submit_reply(world, str(message["message_id"]), text))


@when(parsers.parse('I reply to the latest bot message for "{member_id}" with text "{text}"'))
def _when_reply_to_latest_bot_message(world: World, member_id: str, text: str) -> None:
    message = _latest_bot_message_uncached(world, member_id)
    world.response = cast(Any, _submit_reply(world, str(message["message_id"]), text))


@when(
    parsers.parse(
        'Slack delivers the check-in reply event "{event_id}" from "{member_id}" with text "{text}"'
    )
)
def _when_slack_delivers_checkin_reply(
    world: World, event_id: str, member_id: str, text: str
) -> None:
    check_in = world.stash[f"bot_message:{member_id}"]
    world.response = cast(
        Any,
        _deliver_slack_event(
            world,
            event_id=event_id,
            user_id=member_id,
            text=text,
            channel_id=str(check_in["channel_id"]),
        ),
    )


@when(
    parsers.parse(
        'member "{member_id}" replies in the thread of the cross-person request with text "{text}"'
    )
)
def _when_counterpart_replies_in_thread(world: World, member_id: str, text: str) -> None:
    message = _latest_cross_person_bot_message(world, member_id)
    world.response = cast(
        Any,
        _deliver_slack_event(
            world,
            event_id=f"Ev-thread-{member_id}-{len(_messages(world))}",
            user_id=member_id,
            text=text,
            channel_id=str(message["channel_id"]),
            thread_ts=str(message["message_id"]),
        ),
    )


@when(
    parsers.parse(
        'member "{member_id}" answers the cross-person request from the built-in chat '
        'thread with text "{text}"'
    )
)
def _when_counterpart_answers_in_built_in_chat_thread(
    world: World, member_id: str, text: str
) -> None:
    """What the chat page's Reply action sends: the DM's id as the thread."""
    assert world.client is not None
    message = _latest_cross_person_bot_message(world, member_id)
    world.response = cast(
        Any,
        world.client.post(
            f"/test/chat-simulator/users/{member_id}/messages",
            json={"text": text, "thread_id": message["message_id"]},
        ),
    )


@then(parsers.parse('no check-in should have been opened for "{member_id}"'))
def _then_no_checkin_opened(world: World, member_id: str) -> None:
    opened = [
        item
        for item in _messages(world)
        if item["direction"] == "bot"
        and item["user_id"] == member_id
        and item["purpose"] == "status_checkin"
    ]
    assert opened == []


@then(
    parsers.parse(
        'a cross-person request should notify "{member_id}" with text containing "{fragment}"'
    )
)
def _then_counterpart_notified(world: World, member_id: str, fragment: str) -> None:
    message = _latest_cross_person_bot_message(world, member_id)
    assert fragment in message["text"]


@then(parsers.parse('no cross-person request should notify "{member_id}"'))
def _then_counterpart_not_notified(world: World, member_id: str) -> None:
    messages = [
        item
        for item in _messages(world)
        if item["direction"] == "bot"
        and item["user_id"] == member_id
        and item["purpose"] == "cross_person_request"
    ]
    assert messages == []


@then(parsers.parse("no cross-person request should notify anyone"))
def _then_nobody_notified(world: World) -> None:
    assert _cross_person_bot_messages(world) == []


@then(parsers.parse('the cross-person request DM to "{member_id}" should not contain "{fragment}"'))
def _then_dm_does_not_contain(world: World, member_id: str, fragment: str) -> None:
    message = _latest_cross_person_bot_message(world, member_id)
    assert fragment not in message["text"], message["text"]


@then(parsers.parse('exactly {count:d} cross-person request DM should have gone to "{member_id}"'))
def _then_dm_count(world: World, count: int, member_id: str) -> None:
    sent = [item for item in _cross_person_bot_messages(world) if item["user_id"] == member_id]
    assert len(sent) == count, sent


@then(
    parsers.parse('"{member_id}" should be told the cross-person request was resolved by "{name}"')
)
def _then_requester_told_resolved(world: World, member_id: str, name: str) -> None:
    told = [
        item
        for item in _messages(world)
        if item["direction"] == "bot"
        and item["user_id"] == member_id
        and item["purpose"] == "cross_person_request_resolved"
    ]
    assert len(told) == 1, told
    assert name in told[0]["text"]


@then(
    parsers.parse('the cross-person request raised by "{member_id}" should have status "{status}"')
)
def _then_raised_request_status(world: World, member_id: str, status: str) -> None:
    requests = asyncio.run(
        world.registry().cross_person_request_repository().list_for_requester("demo", member_id)
    )
    assert len(requests) == 1
    assert requests[0].status.value == status


@then(parsers.parse('the cross-person request for "{member_id}" should have status "{status}"'))
def _then_cross_person_request_status(world: World, member_id: str, status: str) -> None:
    requests = asyncio.run(
        world.registry()
        .cross_person_request_repository()
        .list_for_counterpart(
            "demo",
            member_id,
        )
    )
    assert len(requests) == 1
    assert requests[0].status.value == status


@then(parsers.parse('the latest bot message for "{member_id}" should contain "{fragment}"'))
def _then_latest_bot_message_contains(world: World, member_id: str, fragment: str) -> None:
    message = _latest_bot_message_uncached(world, member_id)
    assert fragment in message["text"]


@then("no cross-person requests should be recorded")
def _then_no_cross_person_requests(world: World) -> None:
    requests = asyncio.run(world.registry().cross_person_request_repository().list_open("demo"))
    assert requests == []


def _start_scripted_stack(
    world: World, texts: list[str], *, auto_notify: bool | None = None
) -> None:
    overrides: dict[str, object] = {}
    if auto_notify is not None:
        overrides["cross_person_auto_notify"] = auto_notify
    settings = mock_slack_settings(**overrides)
    world.start_app(settings=settings, registry=_ScriptedLlmRegistry(settings, texts))


def _cross_person_bot_messages(world: World) -> list[dict[str, Any]]:
    return [
        item
        for item in _messages(world)
        if item["direction"] == "bot" and item["purpose"] == "cross_person_request"
    ]


def _latest_cross_person_bot_message(world: World, member_id: str) -> dict[str, Any]:
    messages = [item for item in _cross_person_bot_messages(world) if item["user_id"] == member_id]
    assert messages, f"no cross-person bot message found for {member_id}"
    return messages[-1]


def _deliver_slack_event(
    world: World,
    *,
    event_id: str,
    user_id: str,
    text: str,
    channel_id: str,
    thread_ts: str | None = None,
) -> object:
    """Post a Slack Events API envelope the way Slack would, thread and all.

    The simulator's own reply endpoint copies the bot message's correlation id
    onto the reply, which routes it by correlation. Real Slack carries only the
    channel and, for a threaded reply, ``thread_ts`` -- so this goes through the
    webhook route to exercise the routing a deployment actually relies on. The
    timestamp is derived from the event id so a redelivery is byte-identical.
    """
    assert world.client is not None
    event: dict[str, object] = {
        "type": "message",
        "user": user_id,
        "text": text,
        "ts": f"1900000000.{zlib.crc32(event_id.encode()) % 1_000_000:06d}",
        "channel": channel_id,
    }
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    return world.client.post("/webhooks/chat/slack", json={"event_id": event_id, "event": event})


def _latest_bot_message_uncached(world: World, member_id: str) -> dict[str, Any]:
    messages = [
        item
        for item in _messages(world)
        if item["direction"] == "bot" and item["user_id"] == member_id
    ]
    assert messages, f"no bot message found for {member_id}"
    return messages[-1]


def _messages(world: World) -> list[dict[str, Any]]:
    assert world.client is not None
    response = world.client.get("/test/chat-simulator/messages")
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json()["items"])
