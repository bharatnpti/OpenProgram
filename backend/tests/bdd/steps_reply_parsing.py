"""Step definitions for reply-parsing scenarios (MS-E2E-026/027/028/029/042).

The fake LLM provider used elsewhere in the suite always returns a fixed
progress note, so these steps assert on the properties that are guaranteed
regardless of the LLM's output: the raw reply text is always persisted
verbatim on the ``CheckIn`` record, and whether a confirmed developer status
gets written depends only on deterministic, LLM-independent logic (trivial
acknowledgement detection, JSON-decode fallbacks).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pytest_bdd import given, parsers, then, when

from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.status import CheckIn
from core.ports.llm import LlmProvider
from tests.bdd.fixtures import World, WorldRegistry, mock_slack_settings


@dataclass
class _MalformedJsonLlmProvider:
    requests: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            tenant_id=request.tenant_id,
            text="this is not valid JSON at all",
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost_usd=0.0,
                latency_ms=1.0,
            ),
            trace_id="trace-malformed",
        )


class _MalformedJsonRegistry(WorldRegistry):
    def __init__(self, settings: object) -> None:
        super().__init__(settings)  # type: ignore[arg-type]
        self._scripted_llm = _MalformedJsonLlmProvider()

    def llm_provider(self) -> LlmProvider:
        return self._scripted_llm


@given("the LLM provider returns malformed JSON for every call")
def _given_malformed_json_llm(world: World) -> None:
    settings = mock_slack_settings()
    world.start_app(settings=settings, registry=_MalformedJsonRegistry(settings))


_MULTILINE_REPLY = (
    "Progress: finished the migration script and ran it against staging.\n"
    "Blockers: waiting on DBA sign-off before touching production.\n"
    "ETA: two days once sign-off lands."
)


@when(parsers.parse('I submit a multiline reply to the bot message for "{member_id}"'))
def _when_submit_multiline_reply(world: World, member_id: str) -> None:
    from tests.bdd.steps_common import _latest_bot_message, _submit_reply

    message = _latest_bot_message(world, member_id)
    world.response = _submit_reply(world, message["message_id"], _MULTILINE_REPLY)


@then(parsers.parse('the check-in raw reply for "{member_id}" should equal "{expected_text}"'))
def _then_checkin_raw_reply_equals(world: World, member_id: str, expected_text: str) -> None:
    checkin = _checkin_for_member(world, member_id)
    assert checkin is not None
    assert checkin.raw_reply == expected_text


@then(parsers.parse('the check-in raw reply for "{member_id}" should contain "{fragment}"'))
def _then_checkin_raw_reply_contains(world: World, member_id: str, fragment: str) -> None:
    checkin = _checkin_for_member(world, member_id)
    assert checkin is not None
    assert checkin.raw_reply is not None
    assert fragment in checkin.raw_reply


@then(parsers.parse('the check-in raw reply for "{member_id}" should preserve line breaks'))
def _then_checkin_raw_reply_preserves_lines(world: World, member_id: str) -> None:
    checkin = _checkin_for_member(world, member_id)
    assert checkin is not None
    assert checkin.raw_reply is not None
    assert "\n" in checkin.raw_reply


@then(parsers.parse('the check-in raw reply for "{member_id}" should be null'))
def _then_checkin_raw_reply_is_null(world: World, member_id: str) -> None:
    checkin = _checkin_for_member(world, member_id)
    assert checkin is not None
    assert checkin.raw_reply is None
    assert checkin.replied_at is None


@then(parsers.parse('developer "{member_id}" should have a confirmed status'))
def _then_developer_has_confirmed_status(world: World, member_id: str) -> None:
    from datetime import UTC, datetime

    from core.domain.status import StatusSource

    registry = world.registry()
    as_of = datetime.now(tz=UTC).date()
    cached_message = world.stash.get(f"bot_message:{member_id}")
    if cached_message is not None:
        checkin = asyncio.run(
            registry.status_repository().checkin_by_correlation(
                "demo", cached_message["correlation_id"]
            )
        )
        if checkin is not None:
            as_of = (checkin.replied_at or checkin.asked_at).date()
    status = asyncio.run(
        registry.status_repository().latest_developer_status("demo", member_id, as_of)
    )
    assert status is not None
    assert status.source is StatusSource.CONFIRMED


@then(parsers.parse('developer "{member_id}" should not have a confirmed status'))
def _then_developer_has_no_confirmed_status(world: World, member_id: str) -> None:
    from datetime import UTC, datetime

    from core.domain.status import StatusSource

    registry = world.registry()
    status = asyncio.run(
        registry.status_repository().latest_developer_status(
            "demo", member_id, datetime.now(tz=UTC).date()
        )
    )
    assert status is None or status.source is not StatusSource.CONFIRMED


def _checkin_for_member(world: World, member_id: str) -> CheckIn | None:
    message = world.stash[f"bot_message:{member_id}"]
    registry = world.registry()
    return asyncio.run(
        registry.status_repository().checkin_by_correlation(
            "demo",
            message["correlation_id"],
        )
    )
