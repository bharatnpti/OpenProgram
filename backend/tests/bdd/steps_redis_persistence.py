"""Steps for MS-E2E-044: simulator state survives a backend restart.

Exercises ``RedisMockSlackStore`` directly against a real, disposable Redis
container. A "backend restart" is modeled the same way the real backend
would observe it: a brand new store/client instance reconnecting to the same
Redis instance, since the store itself (not any in-process cache) is the
durable source of truth.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from pytest_bdd import given, parsers, then, when

from infra.adapters.chat.mock_slack import RedisMockSlackStore
from tests.bdd.fixtures import World


def _new_store(redis_url: str) -> RedisMockSlackStore:
    from redis.asyncio import Redis

    return RedisMockSlackStore(client=Redis.from_url(redis_url, decode_responses=True))


@given("a Redis-backed mock Slack simulator store", target_fixture="redis_url")
def _given_redis_backed_store(redis_container: str) -> str:
    return redis_container


@given(parsers.parse('the simulator has a bot message for user "{user_id}"'))
def _given_simulator_bot_message(world: World, redis_url: str, user_id: str) -> None:
    async def _seed() -> None:
        store = _new_store(redis_url)
        channel_id = await store.open_channel("demo", user_id)
        await store.record_bot_message(
            tenant_id="demo",
            channel_id=channel_id,
            text="status?",
            correlation_id="restart-corr-1",
            metadata={"purpose": "status_checkin"},
            created_at=datetime(2026, 7, 6, 9, 30, tzinfo=UTC),
        )

    asyncio.run(_seed())
    world.stash["redis_user_id"] = user_id


@when("the backend process is recreated against the same Redis instance")
def _when_backend_recreated(world: World, redis_url: str) -> None:
    # No-op: a fresh RedisMockSlackStore/client is created for every
    # subsequent assertion below, standing in for a recreated backend
    # process reconnecting to the same durable Redis instance.
    world.stash["redis_restarted"] = True


@then(parsers.parse("the persisted simulator message count is {count:d}"))
def _then_redis_simulator_message_count(world: World, redis_url: str, count: int) -> None:
    async def _count() -> int:
        store = _new_store(redis_url)
        return len(await store.list_messages("demo"))

    assert asyncio.run(_count()) == count


@then(parsers.parse('the simulator still has the bot message for user "{user_id}"'))
def _then_redis_simulator_still_has_message(world: World, redis_url: str, user_id: str) -> None:
    async def _check() -> bool:
        store = _new_store(redis_url)
        messages = await store.list_messages("demo")
        return any(
            message.user_id == user_id and message.direction == "bot" for message in messages
        )

    assert asyncio.run(_check())
