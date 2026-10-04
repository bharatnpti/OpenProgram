"""The follow-up limit is noted only when a required detail is still missing (N30).

R4 live: the stored summaries of Asha, Mina and Sofia ended with "Clarification
cap reached before all details were confirmed." although each had answered
both follow-ups and given their blockers and ETA.
"""

from __future__ import annotations

from core.application.status_collector import StatusCollector
from core.application.status_summaries import CLARIFICATION_CAP_LEAD
from core.domain.status import StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_answered_follow_ups import _evaluation, _message, _open_checkin
from tests.unit.test_status_collector import SequenceLlmProvider

_ASHA = "U-asha"
_CHK_16 = [{"issue_key": "CHK-16", "claimed_state": "on track", "note": "Agenda drafted."}]


def _collector(
    store: InMemoryGraphStore, chat: FakeChatProvider, texts: list[str], *, limit: int
) -> StatusCollector:
    return StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=SequenceLlmProvider(texts=texts),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
        checkin_ack_enabled=False,
        checkin_max_clarifications=limit,
    )


async def test_both_follow_ups_answered_records_no_cap_note() -> None:
    store = InMemoryGraphStore()
    await _open_checkin(store, _ASHA)
    chat = FakeChatProvider()
    collector = _collector(
        store,
        chat,
        [
            # 1: no ETA yet, so the collector asks for it.
            _evaluation(
                sufficient=True,
                question=None,
                claims=_CHK_16,
                blockers_answered=True,
                eta_answered=False,
                note="CHK-16 on track, agenda and numbers drafted, no blockers.",
            ),
            # 2: the ETA is given; the model asks one more thing.
            _evaluation(
                sufficient=False,
                question="Which parts of the CHK-16 agenda are left?",
                claims=[{"issue_key": "CHK-16", "note": "Final pass this week."}],
                blockers_answered=False,
                eta_answered=True,
                note="CHK-16 ETA unchanged, final pass this week.",
            ),
            # 3: at the limit the model still wants more, but nothing required is missing.
            _evaluation(
                sufficient=False,
                question="Anything else on CHK-16?",
                claims=[{"issue_key": "CHK-16", "note": "Just the final pass."}],
                blockers_answered=True,
                eta_answered=False,
                note="Agenda and Q4 numbers drafted, just the final pass. Nothing blocking.",
            ),
        ],
        limit=2,
    )

    await collector.handle_reply(_message(_ASHA, "CHK-16 on track, no blockers.", "m1", 4))
    await collector.handle_reply(_message(_ASHA, "ETA unchanged, final pass this week.", "m2", 5))
    outcome = await collector.handle_reply(
        _message(_ASHA, "Agenda and Q4 numbers drafted, just a final pass.", "m3", 6)
    )

    assert len(chat.sent) == 2
    assert outcome.kind == "processed"
    assert outcome.status is not None
    assert outcome.status.source is StatusSource.CONFIRMED
    assert "Clarification cap reached" not in outcome.status.summary
    checkin = await store.checkin_by_correlation("demo", f"corr-{_ASHA}")
    assert checkin is not None and checkin.signals is not None
    assert "Clarification cap reached" not in checkin.signals.progress_note


async def test_cap_note_names_the_detail_still_missing_once() -> None:
    store = InMemoryGraphStore()
    await _open_checkin(store, _ASHA)
    collector = _collector(
        store,
        FakeChatProvider(),
        [
            _evaluation(
                sufficient=False,
                question="What is your ETA for CHK-16?",
                claims=_CHK_16,
                blockers_answered=True,
                eta_answered=False,
                note="CHK-16 on track, no blockers.",
            )
        ],
        limit=0,
    )

    outcome = await collector.handle_reply(_message(_ASHA, "CHK-16 on track.", "m1", 4))

    assert outcome.status is not None
    assert outcome.status.source is StatusSource.PARTIAL
    summary = outcome.status.summary
    assert summary.endswith(f"{CLARIFICATION_CAP_LEAD} ETA was not provided.")
    assert summary.count("ETA was not provided.") == 1
