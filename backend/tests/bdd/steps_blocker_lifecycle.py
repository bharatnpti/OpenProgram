"""Step definitions for blocker lifecycle and pod attribution scenarios.

The scripted LLM provider supplies deterministic clarification-evaluator JSON
(one text per inbound reply), so the scenarios assert the deterministic
lifecycle outcomes: which rows exist, their attribution, and that non-response
finalization never touches them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

from pytest_bdd import given, parsers, then, when

from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.graph import EdgeKind, GraphEdge, Pod
from core.domain.status import DeveloperStatus, StatusSource
from tests.bdd.fixtures import World, mock_slack_settings
from tests.bdd.steps_reply_parsing import _ScriptedLlmRegistry

_MIXED_BLOCKER_REPLY_JSON = (
    '{"is_status_update":true,"sufficient":true,"question":null,'
    '"signals":{"progress_note":"Working PAY-7, blocked twice",'
    '"blockers":["vendor API","staging DB access"],'
    '"blocker_details":[{"description":"vendor API","issue_key":"PAY-7","pod":null},'
    '{"description":"staging DB access","issue_key":null,"pod":null}],'
    '"resolved_blocker_ids":[],"eta_change_days":0,'
    '"blockers_answered":true,"eta_answered":true}}'
)

_ATTRIBUTION_ANSWER_JSON = (
    '{"is_status_update":true,"sufficient":true,"question":null,'
    '"signals":{"progress_note":"Attribution answer",'
    '"blockers":["staging DB access"],'
    '"blocker_details":[{"description":"staging DB access","issue_key":null,'
    '"pod":"Checkout"}],'
    '"resolved_blocker_ids":[],"eta_change_days":0,'
    '"blockers_answered":true,"eta_answered":true}}'
)


@given("the LLM provider is scripted for a mixed attributed and unattributed blocker conversation")
def _given_mixed_blocker_scripts(world: World) -> None:
    settings = mock_slack_settings()
    world.start_app(
        settings=settings,
        registry=_ScriptedLlmRegistry(
            settings,
            [
                # Text 1 composes the outbound check-in DM itself.
                "Can you share progress, blockers, and ETA changes?",
                _MIXED_BLOCKER_REPLY_JSON,
                _ATTRIBUTION_ANSWER_JSON,
            ],
        ),
    )


@given(parsers.parse('developer "{member_id}" belongs to pods "{first_pod}" and "{second_pod}"'))
def _given_developer_in_two_pods(
    world: World, member_id: str, first_pod: str, second_pod: str
) -> None:
    graph = world.registry().graph_repository()

    async def _seed() -> None:
        for name in (first_pod, second_pod):
            pod_id = f"pod-{name.lower()}"
            await graph.upsert_node(Pod(tenant_id="demo", id=pod_id, name=name))
            await graph.add_edge(
                GraphEdge(
                    tenant_id="demo",
                    from_node_id=pod_id,
                    to_node_id=member_id,
                    kind=EdgeKind.CONTAINS,
                )
            )

    asyncio.run(_seed())


@given(
    parsers.parse(
        'developer "{member_id}" has an open blocker "{description}" first seen {days:d} days ago'
    )
)
def _given_open_blocker(world: World, member_id: str, description: str, days: int) -> None:
    registry = world.registry()
    first_seen = date.today() - timedelta(days=days)

    async def _seed() -> None:
        await registry.status_repository().record_developer_blockers(
            "demo",
            (
                DeveloperBlocker(
                    tenant_id="demo",
                    blocker_id="blk-bdd-1",
                    developer_id=member_id,
                    description=description,
                    normalized_key=normalize_blocker_key(description),
                    source=BlockerSource.CHECKIN,
                    first_seen_on=first_seen,
                    last_seen_on=first_seen,
                ),
            ),
        )
        await registry.status_repository().record_developer_status(
            DeveloperStatus(
                tenant_id="demo",
                developer_id=member_id,
                as_of=first_seen,
                source=StatusSource.CONFIRMED,
                blockers=(description,),
                summary="Blocked.",
            )
        )

    asyncio.run(_seed())
    world.stash["blocker_first_seen"] = first_seen


@when(parsers.parse('the check-in for "{member_id}" is closed as a non-response'))
def _when_checkin_closed_as_non_response(world: World, member_id: str) -> None:
    registry = world.registry()
    message = world.stash[f"bot_message:{member_id}"]

    async def _close() -> DeveloperStatus:
        return await registry.status_collector().record_non_response(
            tenant_id="demo",
            developer_id=member_id,
            as_of=datetime.now(tz=UTC).date(),
            correlation_id=str(message["correlation_id"]),
        )

    world.stash["non_response_status"] = asyncio.run(_close())


@then(parsers.parse('the latest outbound DM to "{member_id}" should mention "{fragment}"'))
def _then_latest_outbound_dm_mentions(world: World, member_id: str, fragment: str) -> None:
    assert world.client is not None
    chat_ids = world.stash.get("chat_ids", {})
    chat_id = chat_ids.get(member_id, member_id)
    items = world.client.get("/test/chat-simulator/messages").json()["items"]
    bot_messages = [
        item for item in items if item["direction"] == "bot" and item["user_id"] == chat_id
    ]
    assert bot_messages, f"no bot message found for {member_id}"
    assert fragment in bot_messages[-1]["text"]


@then(parsers.parse('developer "{member_id}" should have {count:d} open blockers'))
def _then_open_blocker_count(world: World, member_id: str, count: int) -> None:
    rows = _open_blockers(world, member_id)
    assert len(rows) == count, [row.description for row in rows]


@then(
    parsers.parse(
        'blocker "{description}" for "{member_id}" should be attributed to work item "{item}"'
    )
)
def _then_blocker_attributed_to_work_item(
    world: World, description: str, member_id: str, item: str
) -> None:
    row = _blocker_by_description(world, member_id, description)
    assert row.work_item_id == item


@then(
    parsers.parse('blocker "{description}" for "{member_id}" should be attributed to pod "{pod}"')
)
def _then_blocker_attributed_to_pod(
    world: World, description: str, member_id: str, pod: str
) -> None:
    row = _blocker_by_description(world, member_id, description)
    assert row.pod_id == f"pod-{pod.lower()}"


@then(parsers.parse('the developer status source for "{member_id}" should be "{source}"'))
def _then_status_source(world: World, member_id: str, source: str) -> None:
    status = world.stash.get("non_response_status")
    assert isinstance(status, DeveloperStatus)
    assert status.developer_id == member_id
    assert status.source.value == source


@then(
    parsers.parse(
        'blocker "{description}" for "{member_id}" should remain open with unchanged last_seen'
    )
)
def _then_blocker_untouched(world: World, description: str, member_id: str) -> None:
    row = _blocker_by_description(world, member_id, description)
    first_seen = world.stash["blocker_first_seen"]
    assert row.resolved_on is None
    assert row.last_seen_on == first_seen


def _open_blockers(world: World, member_id: str) -> list[DeveloperBlocker]:
    registry = world.registry()
    return asyncio.run(
        registry.status_repository().open_blockers("demo", member_id, datetime.now(tz=UTC).date())
    )


def _blocker_by_description(world: World, member_id: str, description: str) -> DeveloperBlocker:
    key = normalize_blocker_key(description)
    rows = [row for row in _open_blockers(world, member_id) if row.normalized_key == key]
    assert rows, f"no open blocker {description!r} for {member_id}"
    return rows[0]
