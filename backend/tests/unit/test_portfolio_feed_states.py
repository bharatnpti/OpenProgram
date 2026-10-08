from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.application.portfolio_feed_service import PortfolioFeedService, state_label
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from infra.persistence.in_memory_graph import InMemoryGraphStore

TENANT = "demo"


async def _feed_summaries(
    source: str, kind: NodeKind, entity_id: str, payload: dict[str, JsonScalar]
) -> tuple[str, dict[str, JsonScalar]]:
    store = InMemoryGraphStore()
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source=source,
            entity_ref=EntityRef(tenant_id=TENANT, kind=kind, id=entity_id),
            payload=payload,
            observed_at=datetime.now(tz=UTC),
            correlation_id=f"{source}:{entity_id}",
        )
    )
    feed = await PortfolioFeedService(store).feed(TENANT, sources=(source,))
    [item] = feed.items
    return item.summary, dict(item.details)


@pytest.mark.parametrize(
    ("state", "words"),
    [
        ("todo", "To do"),
        ("in_progress", "In progress"),
        ("in_review", "In review"),
        ("blocked", "Blocked"),
        ("done", "Done"),
        # Written like a key, but not one the console has words for: still words.
        ("proposed", "Proposed"),
        ("on_hold", "On hold"),
        # A name the tracker or a person chose is left as it was written.
        ("Code review", "Code review"),
        ("QA", "QA"),
    ],
)
def test_a_state_reads_in_words(state: str, words: str) -> None:
    assert state_label(state) == words


@pytest.mark.parametrize(
    ("state", "words"), [("in_progress", "In progress"), ("done", "Done"), ("todo", "To do")]
)
async def test_an_issue_move_names_the_state_in_words(state: str, words: str) -> None:
    summary, details = await _feed_summaries(
        "issue",
        NodeKind.TASK,
        "CHK-12",
        {"key": "CHK-12", "title": "Cart totals", "state": state},
    )

    assert summary == f"Issue CHK-12 moved to {words}: Cart totals"
    # The fact keeps the raw state: the briefs and the Ask tools compare it.
    assert details["state"] == state


async def test_an_issue_with_no_state_still_reads_as_an_update() -> None:
    summary, _ = await _feed_summaries(
        "issue", NodeKind.TASK, "CHK-9", {"key": "CHK-9", "title": "Totals"}
    )

    assert summary == "Issue CHK-9 moved to updated: Totals"


async def test_a_work_item_move_names_both_states_in_words() -> None:
    summary, details = await _feed_summaries(
        "work_item",
        NodeKind.WORK_ITEM,
        "wi-1",
        {"name": "Refund flow", "from_state": "proposed", "to_state": "in_progress"},
    )

    assert summary == "Refund flow moved from Proposed to In progress"
    assert details["to_state"] == "in_progress"
