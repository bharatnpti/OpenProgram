from __future__ import annotations

from datetime import UTC, date, datetime, time

from core.domain.blockers import (
    BlockerReport,
    BlockerResolutionReason,
    BlockerSource,
    DeveloperBlocker,
)
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.graph import EntityRef, NodeKind
from core.domain.integrations import SyncCursor
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from core.domain.status import (
    CheckIn,
    CheckInClarification,
    CheckInCorrelation,
    CheckInNudge,
    CheckInPreference,
    CheckInScheduleRun,
    CheckInSignals,
    DeveloperStatus,
    IssueClaim,
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
    _developer_blocker_from_row,
    _developer_status_from_row,
    _factor_from_json,
    _factor_to_json,
    _node_status_from_row,
    _signals_from_json,
    _signals_to_json,
    _sync_cursor_from_row,
)
from tests.contract.contracts import (
    assert_conversation_repository_contract,
    assert_graph_repository_contract,
    assert_inbound_chat_event_repository_contract,
    assert_rollup_repository_contract,
    assert_status_repository_contract,
    assert_sync_cursor_repository_contract,
)
from tests.fixtures.demo_graph import populate_demo_graph


async def test_in_memory_store_satisfies_phase_1_repository_contracts() -> None:
    store = InMemoryGraphStore()

    await assert_status_repository_contract(store)
    await assert_graph_repository_contract(store)
    await assert_rollup_repository_contract(store)
    await assert_sync_cursor_repository_contract(store)
    await assert_conversation_repository_contract(store)
    await assert_inbound_chat_event_repository_contract(store)


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


async def test_in_memory_raw_retention_uses_last_accessed_at() -> None:
    store = InMemoryGraphStore()
    old_access = datetime(2026, 1, 1, tzinfo=UTC)
    old_accessed_turn_observed = datetime(2026, 1, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 2, 1, tzinfo=UTC)
    accessed_turn = ConversationTurn(
        tenant_id="demo",
        developer_id="dev-1",
        conversation_id="conv-accessed",
        conversation_date=date(2026, 1, 1),
        role=ConversationRole.USER,
        content="accessed old turn",
        correlation_id="corr-accessed",
        chat_message_id="msg-accessed",
        observed_at=old_accessed_turn_observed,
        last_accessed_at=old_access,
    )
    idle_turn = ConversationTurn(
        tenant_id="demo",
        developer_id="dev-1",
        conversation_id="conv-idle",
        conversation_date=date(2026, 1, 1),
        role=ConversationRole.USER,
        content="idle old turn",
        correlation_id="corr-idle",
        chat_message_id="msg-idle",
        observed_at=old_access,
        last_accessed_at=old_access,
    )
    await store.append_turn(accessed_turn)
    await store.append_turn(idle_turn)

    accessed = await store.list_recent_turns("demo", "dev-1", limit=1)
    assert accessed[0].content == "accessed old turn"
    assert accessed[0].last_accessed_at == old_access

    purged_turns = await store.purge_turns_older_than("demo", cutoff)
    remaining = await store.list_recent_turns("demo", "dev-1", limit=10)
    assert purged_turns == 1
    assert [turn.content for turn in remaining] == ["accessed old turn"]

    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-accessed",
            asked_at=old_access,
            replied_at=old_access,
            raw_reply="accessed raw reply",
            signals=CheckInSignals(progress_note="accessed"),
            last_accessed_at=old_access,
        )
    )
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-idle",
            asked_at=old_access,
            replied_at=old_access,
            raw_reply="idle raw reply",
            signals=CheckInSignals(progress_note="idle"),
            last_accessed_at=old_access,
        )
    )
    touched = await store.checkin_by_correlation("demo", "corr-accessed")
    assert touched is not None
    assert touched.raw_reply == "accessed raw reply"
    assert touched.last_accessed_at is not None and touched.last_accessed_at > old_access

    purged_replies = await store.purge_checkin_raw_replies_older_than("demo", cutoff)
    retained_checkin = await store.checkin_by_correlation("demo", "corr-accessed")
    purged_checkin = await store.checkin_by_correlation("demo", "corr-idle")

    assert purged_replies == 1
    assert retained_checkin is not None
    assert retained_checkin.raw_reply == "accessed raw reply"
    assert purged_checkin is not None
    assert purged_checkin.raw_reply is None
    assert purged_checkin.signals == CheckInSignals(progress_note="idle")


def test_postgres_row_mappers_reconstruct_status_domain_types() -> None:
    asked_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    replied_at = datetime(2026, 1, 10, 9, 5, tzinfo=UTC)
    cursor_updated_at = datetime(2026, 1, 10, 10, 0, tzinfo=UTC)
    conversation_observed_at = datetime(2026, 1, 10, 9, 1, tzinfo=UTC)
    last_accessed_at = datetime(2026, 1, 11, 9, 1, tzinfo=UTC)

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
                "blockers_answered": True,
                "eta_answered": True,
                "issue_updates": [
                    {
                        "issue_key": "PO-1",
                        "claimed_done": True,
                        "claimed_state": "done",
                        "note": "Graph sync is done",
                    }
                ],
            },
            "last_accessed_at": last_accessed_at,
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
            "developer_confirmed": True,
            "confirmed_at": replied_at,
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
            "last_accessed_at": last_accessed_at,
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
        blockers_answered=True,
        eta_answered=True,
        issue_updates=(
            IssueClaim(
                issue_key="PO-1",
                claimed_done=True,
                claimed_state="done",
                note="Graph sync is done",
            ),
        ),
    )
    assert checkin.last_accessed_at == last_accessed_at
    assert developer_status == DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 10),
        source=StatusSource.CONFIRMED,
        blockers=("dependency",),
        summary="Graph sync is blocked.",
        eta_change_days=2,
        developer_confirmed=True,
        confirmed_at=replied_at,
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
    assert conversation_turn.last_accessed_at == last_accessed_at
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


def test_developer_blocker_row_mapper_reconstructs_full_row() -> None:
    attribution_asked_at = datetime(2026, 1, 10, 9, 30, tzinfo=UTC)
    blocker = _developer_blocker_from_row(
        {
            "tenant_id": "demo",
            "blocker_id": "blk-1",
            "developer_id": "dev-1",
            "description": "Waiting on API keys.",
            "normalized_key": "waiting on api keys",
            "work_item_id": "PO-1",
            "pod_id": "pod-1",
            "source": "carry_forward",
            "source_correlation_id": "corr-1",
            "attribution_asked_at": attribution_asked_at,
            # first/last_seen_on arrive as date objects from psycopg; the
            # nullable resolved_on tolerates ISO strings as well.
            "first_seen_on": date(2026, 1, 8),
            "last_seen_on": date(2026, 1, 10),
            "resolved_on": "2026-01-12",
            "resolved_reason": "omitted_in_correction",
        }
    )

    assert blocker == DeveloperBlocker(
        tenant_id="demo",
        blocker_id="blk-1",
        developer_id="dev-1",
        description="Waiting on API keys.",
        normalized_key="waiting on api keys",
        work_item_id="PO-1",
        pod_id="pod-1",
        source=BlockerSource.CARRY_FORWARD,
        source_correlation_id="corr-1",
        attribution_asked_at=attribution_asked_at,
        first_seen_on=date(2026, 1, 8),
        last_seen_on=date(2026, 1, 10),
        resolved_on=date(2026, 1, 12),
        resolved_reason=BlockerResolutionReason.OMITTED_IN_CORRECTION,
    )
    assert blocker.source is BlockerSource.CARRY_FORWARD
    assert blocker.resolved_reason is BlockerResolutionReason.OMITTED_IN_CORRECTION
    assert blocker.is_attributed
    assert blocker.is_open_on(date(2026, 1, 11))
    assert not blocker.is_open_on(date(2026, 1, 12))


def test_developer_blocker_row_mapper_defaults_missing_optionals() -> None:
    blocker = _developer_blocker_from_row(
        {
            "tenant_id": "demo",
            "blocker_id": "blk-2",
            "developer_id": "dev-1",
            "description": "Flaky CI pipeline",
            "normalized_key": "flaky ci pipeline",
            "work_item_id": None,
            "pod_id": None,
            "source": "backfill",
            "source_correlation_id": None,
            "attribution_asked_at": None,
            "first_seen_on": date(2026, 1, 8),
            "last_seen_on": date(2026, 1, 8),
            "resolved_on": None,
            "resolved_reason": None,
        }
    )

    assert blocker.source is BlockerSource.BACKFILL
    assert blocker.work_item_id is None
    assert blocker.pod_id is None
    assert blocker.source_correlation_id is None
    assert blocker.attribution_asked_at is None
    assert blocker.resolved_on is None
    assert blocker.resolved_reason is None
    assert not blocker.is_attributed
    assert blocker.is_open_on(date(2026, 1, 8))
    assert not blocker.is_open_on(date(2026, 1, 7))


def test_signals_json_round_trip_preserves_blocker_reports_and_resolved_ids() -> None:
    signals = CheckInSignals(
        progress_note="Graph sync in progress",
        blockers=("Waiting on API keys", "Flaky CI pipeline"),
        blockers_answered=True,
        blocker_reports=(
            BlockerReport(description="Waiting on API keys", issue_key="PO-1"),
            BlockerReport(description="Flaky CI pipeline", pod_id="pod-1", resolved=True),
        ),
        resolved_blocker_ids=("blk-9", "blk-10"),
    )

    assert _signals_from_json(_signals_to_json(signals)) == signals
    assert _signals_to_json(None) is None
    assert _signals_from_json(None) is None


def test_signals_from_json_defaults_blocker_fields_on_legacy_rows() -> None:
    legacy = {
        "progress_note": "Graph sync",
        "blockers": ["dependency"],
        "eta_change_days": 1,
        "blockers_answered": True,
        "eta_answered": True,
    }

    parsed = _signals_from_json(legacy)

    assert parsed is not None
    assert parsed.blockers == ("dependency",)
    assert parsed.blocker_reports == ()
    assert parsed.resolved_blocker_ids == ()


def test_factor_json_round_trips_attribution_fields() -> None:
    factor = RollupFactor(
        description="Blocker: waiting on API keys",
        contributes=Rag.AMBER,
        source_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="task-1"),
        kind=FactorKind.BLOCKER,
        blocker_id="blk-1",
        work_item_ref=EntityRef(tenant_id="demo", kind=NodeKind.WORK_ITEM, id="wi-1"),
        applies_to_pod_ids=("pod-1", "pod-2"),
        unattributed=True,
    )

    assert _factor_from_json(_factor_to_json(factor)) == factor


def test_factor_from_json_tolerates_legacy_rows() -> None:
    source_ref = {"tenant_id": "demo", "kind": "developer", "id": "dev-1"}
    legacy_blocker = _factor_from_json(
        {
            "description": "Blocker: waiting on API keys",
            "contributes": "amber",
            "source_ref": source_ref,
        }
    )
    legacy_status = _factor_from_json(
        {
            "description": "Developer status is stale and needs confirmation.",
            "contributes": "amber",
            "source_ref": source_ref,
        }
    )
    unknown_kind = _factor_from_json(
        {
            "description": "Blocker: waiting on API keys",
            "contributes": "amber",
            "source_ref": source_ref,
            "kind": "not-a-kind",
        }
    )

    # Legacy blocker rows predate attribution: inferred BLOCKER kind, globally
    # visible (unattributed, no pod scoping).
    assert legacy_blocker is not None
    assert legacy_blocker.kind is FactorKind.BLOCKER
    assert legacy_blocker.unattributed is True
    assert legacy_blocker.applies_to_pod_ids == ()
    assert legacy_blocker.blocker_id is None
    assert legacy_status is not None
    assert legacy_status.kind is FactorKind.STATUS
    assert legacy_status.unattributed is False
    assert unknown_kind is not None
    assert unknown_kind.kind is FactorKind.STATUS
