from __future__ import annotations

from datetime import UTC, date, datetime, time

from core.domain.conversation import ConversationRole
from core.domain.graph import EntityRef, NodeKind
from core.domain.integrations import SyncCursor
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import (
    CheckInClarification,
    CheckInCorrelation,
    CheckInNudge,
    CheckInPreference,
    CheckInScheduleRun,
    CheckInSignals,
    DeveloperStatus,
    StatusSource,
)
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_status import (
    _checkin_clarification_from_row,
    _checkin_correlation_from_row,
    _checkin_from_row,
    _checkin_nudge_from_row,
    _checkin_preference_from_row,
    _checkin_schedule_run_from_row,
    _conversation_turn_from_row,
    _developer_status_from_row,
    _node_status_from_row,
    _sync_cursor_from_row,
)
from tests.contract.contracts import (
    assert_conversation_repository_contract,
    assert_rollup_repository_contract,
    assert_status_repository_contract,
    assert_sync_cursor_repository_contract,
)
from tests.fixtures.demo_graph import populate_demo_graph


async def test_in_memory_store_satisfies_phase_1_repository_contracts() -> None:
    store = InMemoryGraphStore()

    await assert_status_repository_contract(store)
    await assert_rollup_repository_contract(store)
    await assert_sync_cursor_repository_contract(store)
    await assert_conversation_repository_contract(store)


async def test_demo_graph_fixture_adds_memory_statuses_and_rollups() -> None:
    store = InMemoryGraphStore()
    await populate_demo_graph(store, store, "demo")

    confirmed = await store.latest_developer_status("demo", "dev-asha", date(2026, 6, 15))
    stale = await store.latest_developer_status("demo", "dev-liam", date(2026, 6, 15))
    missing = await store.developers_without_checkin("demo", date(2026, 6, 15))
    rollups = await store.list_node_statuses("demo", date(2026, 6, 15))

    assert confirmed is not None
    assert confirmed.source is StatusSource.CONFIRMED
    assert stale is not None
    assert stale.source is StatusSource.STALE
    assert "dev-liam" in missing
    assert any(
        factor.source_ref == EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-graph")
        for status in rollups
        for factor in status.factors
    )


def test_postgres_row_mappers_reconstruct_status_domain_types() -> None:
    asked_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    replied_at = datetime(2026, 1, 10, 9, 5, tzinfo=UTC)
    cursor_updated_at = datetime(2026, 1, 10, 10, 0, tzinfo=UTC)
    conversation_observed_at = datetime(2026, 1, 10, 9, 1, tzinfo=UTC)

    checkin = _checkin_from_row(
        {
            "tenant_id": "demo",
            "developer_id": "dev-1",
            "correlation_id": "corr-1",
            "asked_at": asked_at,
            "replied_at": replied_at,
            "raw_reply": "blocked on dependency",
            "signals": {
                "progress_note": "Graph sync",
                "blockers": ["dependency"],
                "eta_change_days": 1,
            },
        }
    )
    developer_status = _developer_status_from_row(
        {
            "tenant_id": "demo",
            "developer_id": "dev-1",
            "as_of": date(2026, 1, 10),
            "source": "confirmed",
            "blockers": {"items": ["dependency"]},
            "summary": "Graph sync is blocked.",
            "eta_change_days": 2,
        }
    )
    node_status = _node_status_from_row(
        {
            "tenant_id": "demo",
            "entity_kind": "pod",
            "entity_id": "pod-1",
            "as_of": date(2026, 1, 10),
            "rag": "amber",
            "source": "inferred",
            "factors": {
                "items": [
                    {
                        "description": "Blocked task",
                        "contributes": "amber",
                        "source_ref": {
                            "tenant_id": "demo",
                            "kind": "task",
                            "id": "task-1",
                        },
                    }
                ]
            },
        }
    )
    cursor = _sync_cursor_from_row(
        {
            "cursor_value": "cursor-1",
            "cursor_updated_at": cursor_updated_at,
            "metadata": {"page": 2, "nested": {"ignored": True}},
        }
    )
    conversation_turn = _conversation_turn_from_row(
        {
            "tenant_id": "demo",
            "developer_id": "dev-1",
            "conversation_id": "conv-1",
            "conversation_date": date(2026, 1, 10),
            "role": "user",
            "content": "blocked on dependency",
            "correlation_id": "corr-1",
            "chat_message_id": "msg-1",
            "observed_at": conversation_observed_at,
        }
    )
    correlation = _checkin_correlation_from_row(
        {
            "tenant_id": "demo",
            "developer_id": "dev-1",
            "correlation_id": "corr-1",
            "chat_user_ref": "U123",
            "chat_thread_ref": "thread-U123",
            "outbound_message_id": "msg-1",
            "asked_at": asked_at,
            "consumed_at": replied_at,
        }
    )
    preference = _checkin_preference_from_row(
        {
            "tenant_id": "demo",
            "developer_id": "dev-1",
            "local_time": time(9, 30),
            "timezone": "Europe/Berlin",
            "weekdays": {"items": [0, 1, 2, 3, 4]},
            "reply_wait_seconds": 60,
            "final_reply_wait_seconds": 120,
        }
    )
    schedule_run = _checkin_schedule_run_from_row(
        {
            "tenant_id": "demo",
            "developer_id": "dev-1",
            "checkin_date": date(2026, 1, 10),
            "correlation_id": "corr-1",
            "status": "sent",
            "scheduled_at": asked_at,
            "reason": None,
        }
    )
    nudge = _checkin_nudge_from_row(
        {
            "tenant_id": "demo",
            "correlation_id": "corr-1",
            "nudge_number": 1,
            "sent_at": replied_at,
            "outbound_message_id": "nudge-1",
        }
    )
    clarification = _checkin_clarification_from_row(
        {
            "tenant_id": "demo",
            "correlation_id": "corr-1",
            "clarification_number": 1,
            "question": "What is still blocked?",
            "sent_at": replied_at,
            "outbound_message_id": "clarify-1",
        }
    )

    assert checkin.signals == CheckInSignals(
        progress_note="Graph sync",
        blockers=("dependency",),
        eta_change_days=1,
    )
    assert developer_status == DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 10),
        source=StatusSource.CONFIRMED,
        blockers=("dependency",),
        summary="Graph sync is blocked.",
        eta_change_days=2,
    )
    assert node_status == NodeStatus(
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.POD, id="pod-1"),
        rag=Rag.AMBER,
        source=StatusSource.INFERRED,
        factors=(
            RollupFactor(
                description="Blocked task",
                contributes=Rag.AMBER,
                source_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-1"),
            ),
        ),
        as_of=date(2026, 1, 10),
    )
    assert cursor == SyncCursor(
        value="cursor-1",
        updated_at=cursor_updated_at,
        metadata={"page": 2},
    )
    assert conversation_turn.role is ConversationRole.USER
    assert conversation_turn.content == "blocked on dependency"
    assert conversation_turn.observed_at == conversation_observed_at
    assert correlation == CheckInCorrelation(
        tenant_id="demo",
        developer_id="dev-1",
        correlation_id="corr-1",
        chat_user_ref="U123",
        chat_thread_ref="thread-U123",
        outbound_message_id="msg-1",
        asked_at=asked_at,
        consumed_at=replied_at,
    )
    assert preference == CheckInPreference(
        tenant_id="demo",
        developer_id="dev-1",
        local_time=time(9, 30),
        timezone="Europe/Berlin",
        weekdays=(0, 1, 2, 3, 4),
        reply_wait_seconds=60,
        final_reply_wait_seconds=120,
    )
    assert schedule_run == CheckInScheduleRun(
        tenant_id="demo",
        developer_id="dev-1",
        checkin_date=date(2026, 1, 10),
        correlation_id="corr-1",
        status="sent",
        scheduled_at=asked_at,
    )
    assert nudge == CheckInNudge(
        tenant_id="demo",
        correlation_id="corr-1",
        nudge_number=1,
        sent_at=replied_at,
        outbound_message_id="nudge-1",
    )
    assert clarification == CheckInClarification(
        tenant_id="demo",
        correlation_id="corr-1",
        clarification_number=1,
        question="What is still blocked?",
        sent_at=replied_at,
        outbound_message_id="clarify-1",
    )
