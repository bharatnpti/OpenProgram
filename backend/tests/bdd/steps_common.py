"""Shared step definitions for the backend-testable Mock Slack BDD scenarios.

Step phrasing intentionally mirrors the Preconditions/Steps/Expected Result
columns of ``mock-slack-e2e-testing.md`` so each Gherkin scenario stays
traceable to its ``MS-E2E-XXX`` row (see the matching ``@ms_e2e_xxx`` tag).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pytest_bdd import given, parsers, then, when

from infra.adapters.integrations.fake import (
    FakeCalendarProvider,
    FakeIssueTracker,
    FakeVcsProvider,
)
from tests.bdd.fixtures import (
    NON_LOCAL_SETTINGS,
    World,
    authenticate_as_admin,
    mock_slack_settings,
)

# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given("the mock Slack simulator stack is running")
def _given_stack_running(world: World) -> None:
    world.start_app()


@given(
    parsers.parse(
        'the mock Slack simulator stack is running with the chat provider set to "{provider}"'
    )
)
def _given_stack_running_with_chat_provider(world: World, provider: str) -> None:
    world.start_app(chat_provider=provider)


@given("the mock Slack simulator is disabled")
def _given_simulator_disabled(world: World) -> None:
    world.start_app(chat_simulator_enabled=False)


@given("the backend environment is not local")
def _given_non_local_environment(world: World) -> None:
    # The §4f boot guard refuses dev auth and the default Fernet key outside
    # `local`, so a staging app can only be constructed with real OIDC config.
    # Authenticate as admin so the simulator's own environment guard (404) is
    # what the scenario observes, rather than the BFF's 401 for no session.
    world.start_app(environment="staging", **NON_LOCAL_SETTINGS)
    authenticate_as_admin(world)


@given("the caller is a non-admin principal")
def _given_non_admin_principal(world: World) -> None:
    world.start_app(dev_principal_roles="dev")


@given("the mock Slack simulator stack is running with a non-admin caller")
def _given_stack_running_non_admin(world: World) -> None:
    world.start_app(dev_principal_roles="dev")


@given(parsers.parse('a configured member "{member_id}" named "{name}"'))
def _given_configured_member(world: World, member_id: str, name: str) -> None:
    _create_member(world, member_id, name, member_id)


@given(parsers.parse('a configured member "{member_id}" named "{name}" with chat id "{chat_id}"'))
def _given_configured_member_with_chat_id(
    world: World, member_id: str, name: str, chat_id: str
) -> None:
    _create_member(world, member_id, name, chat_id)


@given(parsers.parse('member "{member_id}" has a bot check-in message'))
def _given_member_has_bot_message(world: World, member_id: str) -> None:
    _dispatch_checkin(world, member_id)
    _stash_latest_bot_message(world, member_id)


@given(parsers.parse('member "{member_id}" has a bot check-in message with chat id "{chat_id}"'))
def _given_member_has_bot_message_with_chat_id(world: World, member_id: str, chat_id: str) -> None:
    _dispatch_checkin(world, member_id, chat_external_id=chat_id)
    _stash_latest_bot_message(world, member_id)


@given(parsers.parse('member "{member_id}" has a confirmed reply "{text}"'))
def _given_member_has_confirmed_reply(world: World, member_id: str, text: str) -> None:
    _dispatch_checkin(world, member_id)
    message = _latest_bot_message(world, member_id)
    world.response = _submit_reply(world, message["message_id"], text)
    assert world.response.status_code == 200, world.response.text
    world.stash[f"user_message:{member_id}"] = world.response.json()["message_id"]


@given("the simulator has been reset")
def _given_simulator_reset(world: World) -> None:
    assert world.client is not None
    response = world.client.delete("/test/chat-simulator/state")
    assert response.status_code == 204, response.text


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when("I request the health and readiness endpoints")
def _when_health_and_ready(world: World) -> None:
    assert world.client is not None
    world.stash["health"] = world.client.get("/health")
    world.stash["ready"] = world.client.get("/ready")


@when("I request the simulator status")
def _when_request_status(world: World) -> None:
    assert world.client is not None
    world.response = world.client.get("/test/chat-simulator/status")
    world.stash["status_response"] = world.response


@when("I request the simulator messages")
def _when_request_messages(world: World) -> None:
    assert world.client is not None
    world.response = world.client.get("/test/chat-simulator/messages")
    world.stash["messages_response"] = world.response


@when(parsers.parse('I dispatch a check-in for member "{member_id}"'))
def _when_dispatch_checkin(world: World, member_id: str) -> None:
    world.response = _dispatch_checkin(world, member_id)


@when(parsers.parse('I dispatch a check-in for member "{member_id}" with chat id "{chat_id}"'))
def _when_dispatch_checkin_with_chat_id(world: World, member_id: str, chat_id: str) -> None:
    world.response = _dispatch_checkin(world, member_id, chat_external_id=chat_id)


@when(parsers.parse('I dispatch a check-in for member "{member_id}" on date "{checkin_date}"'))
def _when_dispatch_checkin_on_date(world: World, member_id: str, checkin_date: str) -> None:
    world.response = _dispatch_checkin(world, member_id, checkin_date=checkin_date)


@when(parsers.parse('I dispatch a check-in for unknown developer "{member_id}"'))
def _when_dispatch_unknown_checkin(world: World, member_id: str) -> None:
    world.response = _dispatch_checkin(world, member_id)


@when("I reset the simulator")
def _when_reset_simulator(world: World) -> None:
    assert world.client is not None
    world.response = world.client.delete("/test/chat-simulator/state")


@when(parsers.parse('I submit a reply to the bot message for "{member_id}" with text "{text}"'))
def _when_submit_reply_for_member(world: World, member_id: str, text: str) -> None:
    message = _latest_bot_message(world, member_id)
    world.response = _submit_reply(world, message["message_id"], text)


@when(
    parsers.parse('I submit another reply to the bot message for "{member_id}" with text "{text}"')
)
def _when_submit_second_reply_for_member(world: World, member_id: str, text: str) -> None:
    message = world.stash[f"bot_message:{member_id}"]
    world.response = _submit_reply(world, message["message_id"], text)


@when(parsers.parse('I submit a reply to message id "{message_id}" with text "{text}"'))
def _when_submit_reply_to_message_id(world: World, message_id: str, text: str) -> None:
    world.response = _submit_reply(world, message_id, text)


@when(
    parsers.parse(
        'I submit a reply to the most recent user message for "{member_id}" with text "{text}"'
    )
)
def _when_submit_reply_to_user_message(world: World, member_id: str, text: str) -> None:
    user_message_id = world.stash[f"user_message:{member_id}"]
    world.response = _submit_reply(world, user_message_id, text)


@when(
    parsers.parse(
        'I submit two concurrent replies to the bot message for "{member_id}" '
        'with texts "{text_a}" and "{text_b}"'
    )
)
def _when_submit_two_concurrent_replies(
    world: World, member_id: str, text_a: str, text_b: str
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    message = _latest_bot_message(world, member_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(_submit_reply, world, message["message_id"], text_a)
        future_b = executor.submit(_submit_reply, world, message["message_id"], text_b)
        responses = [future_a.result(), future_b.result()]
    world.stash["concurrent_replies"] = responses
    world.stash["concurrent_reply_texts"] = (text_a, text_b)


@when(parsers.parse('I submit a reply to message id "{message_id}" with an empty payload'))
def _when_submit_reply_empty_payload(world: World, message_id: str) -> None:
    assert world.client is not None
    world.response = world.client.post(
        f"/test/chat-simulator/messages/{message_id}/reply",
        json={"text": "", "received_at": "2026-07-02T09:35:00Z"},
    )


@when(parsers.parse('I submit a reply to the bot message for "{member_id}" with an empty payload'))
def _when_submit_reply_for_member_empty_payload(world: World, member_id: str) -> None:
    assert world.client is not None
    message = _latest_bot_message(world, member_id)
    world.response = world.client.post(
        f"/test/chat-simulator/messages/{message['message_id']}/reply",
        json={"text": "", "received_at": "2026-07-02T09:35:00Z"},
    )


@when(parsers.parse('I submit a reply to message id "{message_id}" with an invalid received_at'))
def _when_submit_reply_invalid_received_at(world: World, message_id: str) -> None:
    assert world.client is not None
    world.response = world.client.post(
        f"/test/chat-simulator/messages/{message_id}/reply",
        json={"text": "Still working on it.", "received_at": "not-a-date"},
    )


@when(
    parsers.parse(
        'I submit a reply to the bot message for "{member_id}" with an invalid received_at'
    )
)
def _when_submit_reply_for_member_invalid_received_at(world: World, member_id: str) -> None:
    assert world.client is not None
    message = _latest_bot_message(world, member_id)
    world.response = world.client.post(
        f"/test/chat-simulator/messages/{message['message_id']}/reply",
        json={"text": "Still working on it.", "received_at": "not-a-date"},
    )


@when("I sync the directory")
def _when_sync_directory(world: World) -> None:
    assert world.client is not None
    world.response = world.client.post("/config/directory/sync")
    world.stash.setdefault("sync_responses", []).append(world.response)


@when(parsers.parse('I post a Slack-shaped webhook reply for "{member_id}" with text "{text}"'))
def _when_post_slack_shaped_webhook(world: World, member_id: str, text: str) -> None:
    assert world.client is not None
    message = _latest_bot_message(world, member_id)
    world.response = world.client.post(
        "/webhooks/chat/mock_slack",
        json={
            "event": {
                "user": message["user_id"],
                "channel": message["channel_id"],
                "text": text,
                "ts": f"{message['message_id']}-reply",
            }
        },
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse('the health endpoint reports status "{status}"'))
def _then_health_status(world: World, status: str) -> None:
    response = world.stash["health"]
    assert response.status_code == 200, response.text
    assert response.json()["status"] == status


@then(parsers.parse('the readiness endpoint reports status "{status}"'))
def _then_ready_status(world: World, status: str) -> None:
    response = world.stash["ready"]
    assert response.status_code == 200, response.text
    assert response.json()["status"] == status


@then(parsers.parse("the simulator status response has status code {code:d}"))
def _then_status_response_code(world: World, code: int) -> None:
    response = world.stash["status_response"]
    assert response.status_code == code, response.text


@then(parsers.parse("the simulator messages response has status code {code:d}"))
def _then_messages_response_code(world: World, code: int) -> None:
    response = world.stash["messages_response"]
    assert response.status_code == code, response.text


@then(parsers.parse('the simulator status shows enabled "{enabled}" and provider "{provider}"'))
def _then_status_shows(world: World, enabled: str, provider: str) -> None:
    body = world.stash["status_response"].json()
    assert body["enabled"] is (enabled == "true")
    assert body["provider"] == provider


@then(parsers.parse("the dispatch response has status code {code:d}"))
def _then_dispatch_response_code(world: World, code: int) -> None:
    assert world.response is not None
    assert world.response.status_code == code, world.response.text


@then("the issue tracker, VCS, and calendar providers are the local fake adapters")
def _then_local_fake_adapters(world: World) -> None:
    registry = world.registry()
    assert isinstance(registry.issue_tracker(), FakeIssueTracker)
    assert isinstance(registry.vcs_provider(), FakeVcsProvider)
    assert isinstance(registry.calendar_provider(), FakeCalendarProvider)


@then(parsers.parse("the simulator has at least {count:d} message"))
@then(parsers.parse("the simulator has at least {count:d} messages"))
def _then_simulator_has_at_least(world: World, count: int) -> None:
    assert world.client is not None
    response = world.client.get("/test/chat-simulator/messages")
    assert len(response.json()["items"]) >= count


@then(parsers.parse('a bot message should be recorded for member "{member_id}"'))
def _then_bot_message_recorded(world: World, member_id: str) -> None:
    message = _latest_bot_message(world, member_id)
    assert message["direction"] == "bot"


@then(parsers.parse('a bot ack message should be recorded for member "{member_id}"'))
def _then_bot_ack_recorded(world: World, member_id: str) -> None:
    assert world.client is not None
    chat_id = world.stash.get("chat_ids", {}).get(member_id, member_id)
    items = world.client.get("/test/chat-simulator/messages").json()["items"]
    acks = [
        item
        for item in items
        if item["direction"] == "bot"
        and item["user_id"] == chat_id
        and item["purpose"] == "status_ack"
    ]
    assert acks, f"no status_ack message found for {member_id} ({chat_id})"


@then(parsers.parse("the simulator message count is {count:d}"))
def _then_simulator_message_count(world: World, count: int) -> None:
    assert world.client is not None
    response = world.client.get("/test/chat-simulator/status")
    assert response.json()["message_count"] == count


@then("the simulator is reported unavailable")
def _then_simulator_unavailable(world: World) -> None:
    assert world.registry().chat_simulator_available() is False


@then("requesting the simulator status returns 404")
def _then_requesting_status_404(world: World) -> None:
    assert world.client is not None
    response = world.client.get("/test/chat-simulator/status")
    assert response.status_code == 404, response.text


@then("requesting the simulator messages returns 404")
def _then_requesting_messages_404(world: World) -> None:
    assert world.client is not None
    response = world.client.get("/test/chat-simulator/messages")
    assert response.status_code == 404, response.text


@then(parsers.parse('building settings with chat provider "{value}" raises a validation error'))
def _then_invalid_chat_provider(value: str) -> None:
    with pytest.raises(ValidationError):
        mock_slack_settings(chat_provider=value)


@then(
    parsers.parse('building settings with directory provider "{value}" raises a validation error')
)
def _then_invalid_directory_provider(value: str) -> None:
    with pytest.raises(ValidationError):
        mock_slack_settings(directory_provider=value)


@then(
    parsers.parse(
        'building settings with issue tracker provider "{value}" raises a validation error'
    )
)
def _then_invalid_issue_tracker_provider(value: str) -> None:
    with pytest.raises(ValidationError):
        mock_slack_settings(issue_tracker_provider=value)


@then(parsers.parse('building settings with vcs provider "{value}" raises a validation error'))
def _then_invalid_vcs_provider(value: str) -> None:
    with pytest.raises(ValidationError):
        mock_slack_settings(vcs_provider=value)


@then(parsers.parse('building settings with calendar provider "{value}" raises a validation error'))
def _then_invalid_calendar_provider(value: str) -> None:
    with pytest.raises(ValidationError):
        mock_slack_settings(calendar_provider=value)


@then("both concurrent replies returned status code 200")
def _then_both_concurrent_replies_ok(world: World) -> None:
    responses = world.stash["concurrent_replies"]
    assert len(responses) == 2
    for response in responses:
        assert response.status_code == 200, response.text


@then(
    parsers.parse(
        'the check-in raw reply for "{member_id}" should equal one of "{text_a}" or "{text_b}"'
    )
)
def _then_checkin_raw_reply_one_of(world: World, member_id: str, text_a: str, text_b: str) -> None:
    import asyncio

    registry = world.registry()
    message = world.stash[f"bot_message:{member_id}"]
    checkin = asyncio.run(
        registry.status_repository().checkin_by_correlation("demo", message["correlation_id"])
    )
    assert checkin is not None
    assert checkin.raw_reply in {text_a, text_b}, checkin.raw_reply


@then("every simulator bot message has purpose and correlation metadata")
def _then_every_bot_message_has_metadata(world: World) -> None:
    assert world.client is not None
    items = world.client.get("/test/chat-simulator/messages").json()["items"]
    bot_messages = [item for item in items if item["direction"] == "bot"]
    assert bot_messages
    for message in bot_messages:
        assert message["purpose"], message
        assert message["correlation_id"], message


@then("every simulator user message has reply_to_message_id and mock_slack source metadata")
def _then_every_user_message_has_metadata(world: World) -> None:
    assert world.client is not None
    items = world.client.get("/test/chat-simulator/messages").json()["items"]
    user_messages = [item for item in items if item["direction"] == "user"]
    assert user_messages
    for message in user_messages:
        assert message["reply_to_message_id"], message
        assert message["metadata"].get("source") == "mock_slack", message
        assert message["correlation_id"], message


@then(parsers.parse("the response status code should be {code:d}"))
def _then_response_status_code(world: World, code: int) -> None:
    assert world.response is not None
    assert world.response.status_code == code, world.response.text


@then(parsers.parse("the directory sync reports {count:d} synced users"))
def _then_directory_sync_reports(world: World, count: int) -> None:
    assert world.response is not None
    body = world.response.json()
    assert body["synced_count"] == count, body


@then(
    parsers.parse(
        "both directory syncs reported {count:d} synced users and {deactivated:d} deactivated"
    )
)
def _then_both_directory_syncs(world: World, count: int, deactivated: int) -> None:
    responses = world.stash["sync_responses"]
    assert len(responses) == 2
    for response in responses:
        body = response.json()
        assert body["synced_count"] == count, body
        assert body["deactivated_count"] == deactivated, body


@then(parsers.parse("the config directory has {count:d} total users"))
def _then_config_directory_total(world: World, count: int) -> None:
    assert world.client is not None
    response = world.client.get("/config/directory/users")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == count, response.json()


@then(parsers.parse('the response status should be "{status}"'))
def _then_response_status_field(world: World, status: str) -> None:
    assert world.response is not None
    assert world.response.json()["status"] == status, world.response.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_member(world: World, member_id: str, name: str, chat_id: str) -> None:
    assert world.client is not None
    response = world.client.post(
        "/config/members",
        json={"id": member_id, "name": name},
    )
    assert response.status_code == 201, response.text
    world.stash.setdefault("chat_ids", {})[member_id] = chat_id


def _dispatch_checkin(
    world: World,
    member_id: str,
    *,
    chat_external_id: str | None = None,
    checkin_date: str | None = None,
) -> object:
    assert world.client is not None
    _pin_checkin_time_to_start_of_day(world, member_id)
    chat_ids = world.stash.get("chat_ids", {})
    payload = {
        "tenant_id": "demo",
        "developer_id": member_id,
        "developer_name": member_id,
        "chat_external_id": chat_external_id or chat_ids.get(member_id, member_id),
    }
    if checkin_date is not None:
        payload["checkin_date"] = checkin_date
    return world.client.post("/admin/workflows/checkin/dispatch", json=payload)


def _pin_checkin_time_to_start_of_day(world: World, member_id: str) -> None:
    """Make the dispatched check-in's ``asked_at`` land in the past.

    ``asked_at`` is derived from the developer's check-in preference
    ``local_time``, which defaults to 09:30 -- not from the wall clock. Reply
    resolution requires ``asked_at <= received_at`` (status_collector
    ``_local_date_matches``), so a suite run before 09:30 UTC would produce a
    check-in "asked" in the future and any reply routed by thread/user instead
    of by explicit correlation id would resolve to nothing and be ignored.
    Pinning the preference to 00:00 keeps these scenarios independent of the
    time of day the suite happens to run.
    """
    assert world.client is not None
    response = world.client.put(
        f"/config/members/{member_id}/checkin-preference",
        json={"local_time": "00:00:00"},
    )
    # 404 means the member does not exist -- that is the subject of the
    # unknown-member scenarios, which assert on the dispatch failing. Leave the
    # failure to the dispatch itself rather than masking it here.
    if response.status_code == 404:
        return
    assert response.status_code == 200, response.text


def _stash_latest_bot_message(world: World, member_id: str) -> None:
    world.stash[f"bot_message:{member_id}"] = _latest_bot_message(world, member_id)


def _latest_bot_message(world: World, member_id: str) -> dict[str, object]:
    cached = world.stash.get(f"bot_message:{member_id}")
    if cached is not None:
        return cached
    assert world.client is not None
    chat_ids = world.stash.get("chat_ids", {})
    chat_id = chat_ids.get(member_id, member_id)
    items = world.client.get("/test/chat-simulator/messages").json()["items"]
    bot_messages = [
        item for item in items if item["direction"] == "bot" and item["user_id"] == chat_id
    ]
    assert bot_messages, f"no bot message found for {member_id} ({chat_id})"
    latest = bot_messages[-1]
    world.stash[f"bot_message:{member_id}"] = latest
    return latest


def _submit_reply(
    world: World,
    message_id: str,
    text: str,
    *,
    received_at: str | None = None,
) -> object:
    assert world.client is not None
    payload: dict[str, object] = {"text": text}
    if received_at is not None:
        payload["received_at"] = received_at
    return world.client.post(
        f"/test/chat-simulator/messages/{message_id}/reply",
        json=payload,
    )
