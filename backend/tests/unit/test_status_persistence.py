from __future__ import annotations

from datetime import UTC, date, datetime

from core.domain.graph import EntityRef, NodeKind
from core.domain.integrations import SyncCursor
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import CheckInSignals, DeveloperStatus, Mood, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_status import (
    _checkin_from_row,
    _developer_status_from_row,
    _node_status_from_row,
    _sync_cursor_from_row,
)
from infra.persistence.seed_data import seed_demo_graph
from tests.contract.contracts import (
    assert_rollup_repository_contract,
    assert_status_repository_contract,
    assert_sync_cursor_repository_contract,
)


async def test_in_memory_store_satisfies_phase_1_repository_contracts() -> None:
    store = InMemoryGraphStore()

    await assert_status_repository_contract(store)
    await assert_rollup_repository_contract(store)
    await assert_sync_cursor_repository_contract(store)


async def test_seed_demo_graph_adds_memory_statuses_and_rollups() -> None:
    store = InMemoryGraphStore()
    await seed_demo_graph(store, store, "demo")

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
                "mood": "negative",
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

    assert checkin.signals == CheckInSignals(
        progress_note="Graph sync",
        blockers=("dependency",),
        eta_change_days=1,
        mood=Mood.NEGATIVE,
    )
    assert developer_status == DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 10),
        source=StatusSource.CONFIRMED,
        blockers=("dependency",),
        summary="Graph sync is blocked.",
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
