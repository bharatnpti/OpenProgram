from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

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


@given("the cross-person request stack is running with Liam review extraction")
def _given_liam_review_stack(world: World) -> None:
    _start_scripted_stack(
        world,
        [
            "Can you share progress, blockers, and ETA changes?",
            (
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"Blocked on API schema review",'
                '"blockers":["API schema review"],"eta_change_days":null,'
                '"requests":[{"name":"Liam Chen","kind":"review",'
                '"note":"API schema review","email":null}]}}'
            )
        ],
    )


@given("the cross-person request stack is running with ambiguous Alex responses")
def _given_ambiguous_alex_stack(world: World) -> None:
    _start_scripted_stack(
        world,
        [
            "Can you share progress, blockers, and ETA changes?",
            (
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"Blocked on schema confirmation",'
                '"blockers":["schema confirmation"],"eta_change_days":null,'
                '"requests":[{"name":"Alex","kind":"input",'
                '"note":"schema confirmation","email":null}]}}'
            ),
            (
                '{"is_status_update":true,"sufficient":true,"question":null,'
                '"signals":{"progress_note":"Blocked on schema confirmation",'
                '"blockers":["schema confirmation"],"eta_change_days":null,'
                '"requests":[{"name":"Alex","kind":"input",'
                '"note":"schema confirmation","email":"alexa.roy@example.com"}]}}'
            ),
        ],
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


@when(
    parsers.parse('member "{member_id}" replies to the cross-person request with text "{text}"')
)
def _when_counterpart_replies(world: World, member_id: str, text: str) -> None:
    message = _latest_cross_person_bot_message(world, member_id)
    world.response = _submit_reply(world, str(message["message_id"]), text)


@when(parsers.parse('I reply to the latest bot message for "{member_id}" with text "{text}"'))
def _when_reply_to_latest_bot_message(world: World, member_id: str, text: str) -> None:
    message = _latest_bot_message_uncached(world, member_id)
    world.response = _submit_reply(world, str(message["message_id"]), text)


@then(
    parsers.parse(
        'a cross-person request should notify "{member_id}" with text containing "{fragment}"'
    )
)
def _then_counterpart_notified(world: World, member_id: str, fragment: str) -> None:
    message = _latest_cross_person_bot_message(world, member_id)
    assert fragment in message["text"]


@then(parsers.parse('the cross-person request for "{member_id}" should have status "{status}"'))
def _then_cross_person_request_status(world: World, member_id: str, status: str) -> None:
    requests = asyncio.run(
        world.registry().cross_person_request_repository().list_for_counterpart(
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


def _start_scripted_stack(world: World, texts: list[str]) -> None:
    settings = mock_slack_settings()
    world.start_app(settings=settings, registry=_ScriptedLlmRegistry(settings, texts))


def _latest_cross_person_bot_message(world: World, member_id: str) -> dict[str, Any]:
    messages = [
        item
        for item in _messages(world)
        if item["direction"] == "bot"
        and item["user_id"] == member_id
        and item["purpose"] == "cross_person_request"
    ]
    assert messages, f"no cross-person bot message found for {member_id}"
    return messages[-1]


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
    return response.json()["items"]
