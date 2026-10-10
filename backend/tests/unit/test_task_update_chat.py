"""Chat parity for the per-task update: a check-in states each issue on its task.

At finalize the collector records a ``task_update`` fact per issue a claim
names with a canonical state (``via: "chat"``), so the console's task row
shows what was last said in chat. The fact never carries the claim's words.
"""

from __future__ import annotations

from datetime import date

from core.application.task_update_service import TASK_UPDATE_FACT_SOURCE
from core.domain.graph import EntityRef, NodeKind
from core.domain.writeback import WriteBackTarget
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.unit.test_eta_disagreement import _LIAM, _LIAM_R3, _check_in, _evaluation, _seed
from tests.unit.test_task_update import _TENANT, _facts, _persona


async def test_a_chat_check_in_states_each_issue_on_its_task_without_the_claims_words() -> None:
    store = InMemoryGraphStore()
    await _seed(store)
    on_track = _evaluation(
        [{"issue_key": "CHK-4", "claimed_done": False, "claimed_state": "on track", "note": "ok"}],
        eta_change_days=None,
    )

    await _check_in(store, _LIAM, _LIAM_R3, 6)
    [fact] = _facts(store, TASK_UPDATE_FACT_SOURCE)

    assert fact.entity_ref == EntityRef(tenant_id=_TENANT, kind=NodeKind.TASK, id="CHK-4")
    assert {key: fact.payload[key] for key in ("state", "via", "note", "developer_id")} == {
        "state": "in_progress",
        "via": "chat",
        "note": None,
        "developer_id": _LIAM,
    }
    assert "Tuesday" not in str(fact.payload)
    view = await _persona(store).focus(_TENANT, _LIAM, date(2026, 10, 4))
    [task] = view.tasks
    assert task.last_update is not None
    assert (task.last_update.state, task.last_update.via, task.last_update.note) == (
        WriteBackTarget.IN_PROGRESS,
        "chat",
        None,
    )
    assert (task.my_eta, task.my_eta_label) == (date(2026, 10, 6), "Tuesday")
    assert "ready for review by Tuesday" not in str(view)

    # A claim that names no state records nothing.
    other = InMemoryGraphStore()
    await _seed(other)
    await _check_in(other, _LIAM, on_track, 6)
    assert _facts(other, TASK_UPDATE_FACT_SOURCE) == []
