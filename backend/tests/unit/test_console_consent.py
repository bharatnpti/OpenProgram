"""Jira changes answered from the console: one resolve path with the chat, one write.

Liam (always_ask) said CHK-3 is in review and CHK-4 started; the check-in is
held open for his yes or no. He may answer in the DM or in the console's
"Jira changes waiting for your yes". Both go through
``WriteBackService.resolve_proposal`` under the correlation's lock, so the issue
moves once whoever answers first; the console's answer finishes the held
check-in and says so in the DM in one line.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from core.application.delivery_scope import MemberScope
from core.application.jira_changes_service import (
    DUE_DATE_ANSWER,
    JiraChangeNotAnswerable,
    JiraChangeNotFound,
    JiraChangesService,
    answer_detail,
    proposal_why,
)
from core.application.persona_views import FocusTaskView, FocusView
from core.application.status_collector import (
    CONSOLE_ANSWER_PURPOSE,
    StatusCollector,
    console_answer_line,
)
from core.application.writeback_service import (
    CONSOLE_CONSENT_SOURCE,
    CONSOLE_SOURCE,
    CONSOLE_SUGGESTION_SOURCE,
    ProposalAnswer,
    WriteBackService,
    suggestion_correlation,
)
from core.domain.errors import GraphNotFound
from core.domain.graph import EntityRef, NodeKind
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.jira_writes import JiraWriteKind
from core.domain.risk import DriftFinding, DriftFindingKind, RiskEvidence
from core.domain.rollup import Rag
from core.domain.status import (
    CheckIn,
    CheckInCorrelation,
    CheckInPreference,
    CheckInSignals,
    DeveloperStatus,
    IssueClaim,
    StatusSource,
    WriteBackConsent,
)
from core.domain.writeback import WriteBackAudit, WriteBackStatus
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_writeback_reply import (
    _LIAM,
    _LIAM_CORRELATION,
    _LIAM_EVALUATION,
    _LIAM_STATUS,
    _LIAM_STATUS_TEXT,
    _TENANT,
    _liam_collector,
    _liam_rows,
    _liam_says,
    _sent,
)

_NOW = datetime(2026, 10, 3, 12, 20, tzinfo=UTC)
_TODAY = _NOW.date()
_STATUS_ACK = "status_ack"


class _SlowTracker(FakeIssueTracker):
    """A tracker whose transition takes a while: the window two answers race in."""

    async def transition(self, tenant_id: str, key: str, to_state: str) -> None:
        await asyncio.sleep(0.05)
        await super().transition(tenant_id, key, to_state)


class _UnlockedStore(InMemoryGraphStore):
    """The store without the per-correlation lock: what the lock keeps from happening."""

    @asynccontextmanager
    async def exclusive_correlation(
        self, tenant_id: str, correlation_id: str
    ) -> AsyncIterator[None]:
        yield


def _write_back(collector: StatusCollector) -> WriteBackService:
    service = collector._write_back_service
    assert service is not None
    return service


def _changes(
    collector: StatusCollector,
    store: InMemoryGraphStore,
    *,
    drift: tuple[DriftFinding, ...] = (),
    focus: FocusView | None = None,
    write_back: WriteBackService | None = None,
) -> JiraChangesService:
    async def project_drift(tenant_id: str, project_id: str, day: date) -> list[DriftFinding]:
        return list(drift)

    async def scope_of(tenant_id: str, subject: str) -> MemberScope:
        return MemberScope(projects=frozenset({"checkout"}))

    async def focus_of(tenant_id: str, subject: str, day: date) -> FocusView:
        if focus is None:
            raise GraphNotFound("no tasks")
        return focus

    return JiraChangesService(
        write_back=write_back or _write_back(collector),
        status_repository=store,
        graph_repository=store,
        identity_link_repository=store,
        project_drift=project_drift,
        scope_of=scope_of,
        focus_of=focus_of,
        finisher=lambda: collector,
        tracker_name="the issue tracker",
        clock=lambda: _NOW,
    )


async def _held_liam(
    tracker_type: type[FakeIssueTracker] = FakeIssueTracker,
) -> tuple[StatusCollector, FakeIssueTracker, FakeChatProvider, InMemoryGraphStore]:
    """Liam's check-in held for his answer: CHK-3 -> In Review and CHK-4 -> In Progress."""
    collector, tracker, chat, store = await _liam_collector([_LIAM_EVALUATION])
    if tracker_type is not FakeIssueTracker:
        slow = tracker_type(issues=dict(tracker.issues))
        collector._issue_tracker = slow
        _write_back(collector)._issue_tracker = slow
        tracker = slow
    first = await collector.handle_reply(_LIAM_STATUS)
    assert first.kind == "clarifying"
    return collector, tracker, chat, store


def _item_id(view_items: object, key: str) -> str:
    for item in view_items:  # type: ignore[attr-defined]
        if item.issue_key == key:
            return str(item.id)
    raise AssertionError(f"no item for {key}")


async def _checkin(store: InMemoryGraphStore) -> CheckIn:
    checkin = await store.checkin_by_correlation(_TENANT, _LIAM_CORRELATION)
    assert checkin is not None
    return checkin


# ---- the list ------------------------------------------------------------------------


async def test_the_held_check_ins_proposals_are_listed_with_an_open_gate() -> None:
    collector, _, _, store = await _held_liam()

    view = await _changes(collector, store).changes(_TENANT, _LIAM)

    assert [(item.kind, item.issue_key, item.target_label) for item in view.items] == [
        ("proposal", "CHK-3", "In Review"),
        ("proposal", "CHK-4", "In Progress"),
    ]
    first = view.items[0]
    assert first.id.startswith("proposal:")
    assert first.holds_checkin is True
    assert first.gate is not None and first.gate.state == "open" and first.gate.yes_allowed
    assert first.why == "You said it is in review in today's check-in."
    assert first.issue_title is None  # the title is the synced task's; this harness has none
    assert first.asked_on == _TODAY


async def test_nobody_else_sees_or_answers_liams_changes() -> None:
    collector, tracker, _, store = await _held_liam()
    service = _changes(collector, store)
    view = await service.changes(_TENANT, _LIAM)

    assert (await service.changes(_TENANT, "U-NOAH")).items == ()
    with pytest.raises(JiraChangeNotFound):
        await service.answer(_TENANT, "U-NOAH", _item_id(view.items, "CHK-3"), "yes")
    with pytest.raises(JiraChangeNotFound):
        await service.answer(_TENANT, _LIAM, "proposal:no-such-row", "yes")
    assert tracker.transitions == []


# ---- a held check-in ---------------------------------------------------------------


async def test_a_console_yes_and_no_finish_the_held_check_in_and_say_so_in_the_dm() -> None:
    collector, tracker, chat, store = await _held_liam()
    service = _changes(collector, store)
    items = (await service.changes(_TENANT, _LIAM)).items

    yes = await service.answer(_TENANT, _LIAM, _item_id(items, "CHK-3"), "yes")

    assert (yes.outcome, yes.detail) == (
        "applied",
        "Moved CHK-3 to In Review in the issue tracker.",
    )
    assert yes.checkin_finished is False  # CHK-4 still waits
    assert yes.line_sent is True
    assert tracker.transitions == [(_TENANT, "CHK-3", "in_review")]
    assert _sent(chat, CONSOLE_ANSWER_PURPOSE) == [
        "Answered in the console: CHK-3 moved to In Review in the issue tracker. "
        "CHK-4 still waits for your yes or no."
    ]
    assert (await _checkin(store)).replied_at is None
    assert _sent(chat, _STATUS_ACK) == []

    no = await service.answer(_TENANT, _LIAM, _item_id(items, "CHK-4"), "no")

    assert (no.outcome, no.checkin_finished, no.line_sent) == ("declined", True, True)
    assert tracker.transitions == [(_TENANT, "CHK-3", "in_review")]
    rows = await _liam_rows(store)
    assert (rows["CHK-3"][-1].status, rows["CHK-3"][-1].source) == (
        WriteBackStatus.APPLIED,
        CONSOLE_CONSENT_SOURCE,
    )
    assert (rows["CHK-4"][-1].status, rows["CHK-4"][-1].source) == (
        WriteBackStatus.DECLINED,
        CONSOLE_CONSENT_SOURCE,
    )
    assert _sent(chat, CONSOLE_ANSWER_PURPOSE)[-1] == (
        "Answered in the console: CHK-4 left as is, as you asked."
    )
    # The check-in is recorded once, from what Liam said, with the usual ack.
    final = await _checkin(store)
    assert final.replied_at == _NOW
    assert final.raw_reply == _LIAM_STATUS_TEXT
    [ack] = _sent(chat, _STATUS_ACK)
    assert "your update is recorded" in ack
    assert "I updated CHK-3 to In Review in the issue tracker." in ack
    assert "I left CHK-4 as is in the issue tracker, as you asked." in ack
    assert (await service.changes(_TENANT, _LIAM)).items == ()

    # A yes in the DM afterwards finds nothing pending and writes nothing.
    late = await collector.handle_reply(
        _liam_says("yes", "msg-liam-late", datetime(2026, 10, 3, 12, 25, tzinfo=UTC))
    )
    assert late.kind == "processed"
    assert tracker.transitions == [(_TENANT, "CHK-3", "in_review")]
    assert len(_sent(chat, _STATUS_ACK)) == 1


async def test_a_dm_answer_after_the_console_one_meets_the_resolved_proposal() -> None:
    collector, tracker, chat, store = await _held_liam()
    service = _changes(collector, store)
    items = (await service.changes(_TENANT, _LIAM)).items
    await service.answer(_TENANT, _LIAM, _item_id(items, "CHK-3"), "yes")

    # "yes" in the DM now answers only CHK-4, the one still pending.
    outcome = await collector.handle_reply(
        _liam_says("yes", "msg-liam-yes", datetime(2026, 10, 3, 12, 22, tzinfo=UTC))
    )

    assert outcome.kind == "processed"
    assert tracker.transitions == [
        (_TENANT, "CHK-3", "in_review"),
        (_TENANT, "CHK-4", "in_progress"),
    ]
    rows = await _liam_rows(store)
    assert [row.status for row in rows["CHK-3"]].count(WriteBackStatus.APPLIED) == 1
    assert rows["CHK-4"][-1].source == "consent_reply"
    assert len(_sent(chat, _STATUS_ACK)) == 1


# ---- an already-finalized check-in -------------------------------------------------


async def _finalized_with_a_pending_proposal() -> tuple[
    StatusCollector, FakeIssueTracker, FakeChatProvider, InMemoryGraphStore, WriteBackAudit
]:
    """Liam's check-in was recorded, and his CHK-3 proposal still waits (a later yes)."""
    collector, tracker, chat, store = await _liam_collector([])
    replied_at = datetime(2026, 10, 3, 12, 15, tzinfo=UTC)
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_LIAM,
            correlation_id=_LIAM_CORRELATION,
            asked_at=datetime(2026, 10, 3, 12, 0, 15, tzinfo=UTC),
            replied_at=replied_at,
            raw_reply=_LIAM_STATUS_TEXT,
            signals=CheckInSignals(
                progress_note="CHK-3 in review on checkout-api !1",
                issue_updates=(IssueClaim(issue_key="CHK-3", claimed_state="in review"),),
            ),
            checkin_date=_TODAY,
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id=_TENANT,
            developer_id=_LIAM,
            as_of=_TODAY,
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="CHK-3 is in review on checkout-api !1.",
        )
    )
    proposal = WriteBackAudit(
        id="proposal-chk3",
        tenant_id=_TENANT,
        developer_id=_LIAM,
        issue_key="CHK-3",
        correlation_id=_LIAM_CORRELATION,
        status=WriteBackStatus.PROPOSED,
        target_state="in_review",
        before_state="todo",
        after_state="in_review",
        source="checkin",
        created_at=replied_at,
    )
    await store.record(proposal)
    return collector, tracker, chat, store, proposal


async def test_a_console_yes_on_a_finalized_check_in_marks_its_status_and_sends_no_ack() -> None:
    collector, tracker, chat, store, proposal = await _finalized_with_a_pending_proposal()
    service = _changes(collector, store)
    [item] = (await service.changes(_TENANT, _LIAM)).items
    assert (item.id, item.holds_checkin) == (f"proposal:{proposal.id}", False)

    answer = await service.answer(_TENANT, _LIAM, item.id, "yes")

    assert (answer.outcome, answer.checkin_finished, answer.line_sent) == ("applied", False, True)
    assert tracker.transitions == [(_TENANT, "CHK-3", "in_review")]
    assert _sent(chat, CONSOLE_ANSWER_PURPOSE) == [
        "Answered in the console: CHK-3 moved to In Review in the issue tracker."
    ]
    assert _sent(chat, _STATUS_ACK) == []
    status = await store.latest_developer_status(_TENANT, _LIAM, _TODAY)
    assert status is not None
    assert status.summary == (
        "CHK-3 is in review on checkout-api !1 (moved to In Review in the issue tracker by "
        "OpenProgram after confirmation)."
    )
    assert (await _checkin(store)).replied_at == datetime(2026, 10, 3, 12, 15, tzinfo=UTC)


# ---- gates closing between the proposal and the yes --------------------------------


@pytest.mark.parametrize(
    "close",
    ["master", "checkin_updates", "never"],
)
async def test_a_switch_turned_off_after_the_proposal_refuses_the_yes(close: str) -> None:
    collector, tracker, chat, store = await _held_liam()
    if close == "master":
        await store.set_writeback_enabled(_TENANT, False)
    elif close == "never":
        await store.record_checkin_preference(
            CheckInPreference(
                tenant_id=_TENANT, developer_id=_LIAM, write_back_consent=WriteBackConsent.NEVER
            )
        )
    else:

        async def kind_on(tenant_id: str, kind: JiraWriteKind) -> bool:
            return kind is not JiraWriteKind.CHECKIN_UPDATES

        _write_back(collector)._kind_switch = kind_on
    service = _changes(collector, store)
    items = (await service.changes(_TENANT, _LIAM)).items
    assert {item.gate.state for item in items if item.gate} == {"off"}
    assert not any(item.gate.yes_allowed for item in items if item.gate)

    answer = await service.answer(_TENANT, _LIAM, _item_id(items, "CHK-3"), "yes")

    assert answer.outcome == "off"
    assert answer.detail == "Not moved: updating the issue tracker from check-ins is off."
    assert (answer.checkin_finished, answer.line_sent) == (False, False)
    assert tracker.transitions == []
    assert _sent(chat, CONSOLE_ANSWER_PURPOSE) == []
    # Still pending: a no closes it, whatever the switches say.
    no = await service.answer(_TENANT, _LIAM, _item_id(items, "CHK-3"), "no")
    assert no.outcome == "declined"
    assert tracker.transitions == []


async def test_an_issue_reassigned_after_the_proposal_is_never_written() -> None:
    collector, tracker, _, store = await _held_liam()
    tracker.issues["CHK-3"] = replace(
        tracker.issues["CHK-3"], assignee=UserRef(tenant_id=_TENANT, external_id="acct-noah")
    )
    service = _changes(collector, store)
    items = (await service.changes(_TENANT, _LIAM)).items
    chk3 = next(item for item in items if item.issue_key == "CHK-3")
    assert chk3.gate is not None and (chk3.gate.state, chk3.gate.yes_allowed) == (
        "not_owner",
        False,
    )

    answer = await service.answer(_TENANT, _LIAM, chk3.id, "yes")

    assert answer.outcome == "not_owner"
    assert answer.detail == "Not moved: CHK-3 is not assigned to you in the issue tracker."
    assert tracker.transitions == []
    rows = await _liam_rows(store)
    assert (rows["CHK-3"][-1].status, rows["CHK-3"][-1].source) == (
        WriteBackStatus.DECLINED,
        "not_owner",
    )


async def test_a_done_with_a_merge_request_opened_since_is_held() -> None:
    collector, tracker, _, store = await _held_liam()
    write_back = _write_back(collector)
    proposal = WriteBackAudit(
        id="proposal-chk4-done",
        tenant_id=_TENANT,
        developer_id=_LIAM,
        issue_key="CHK-4",
        correlation_id=_LIAM_CORRELATION,
        status=WriteBackStatus.PROPOSED,
        target_state="done",
        before_state="todo",
        after_state="done",
        source="checkin",
        created_at=_NOW,
    )
    await store.record(proposal)

    async def open_requests(tenant_id: str, issue_key: str) -> tuple[str, ...]:
        return ("checkout-api !7",) if issue_key == "CHK-4" else ()

    write_back._open_merge_requests_for = open_requests  # type: ignore[method-assign]
    gate = await write_back.answer_gate(
        _TENANT, _LIAM, "CHK-4", "done", JiraWriteKind.CHECKIN_UPDATES
    )
    assert (gate.state, gate.merge_requests) == ("held_open_mr", ("checkout-api !7",))

    answer = await write_back.resolve_proposal(
        tenant_id=_TENANT, developer_id=_LIAM, audit_id=proposal.id, decision="yes"
    )

    assert (answer.outcome, answer.merge_requests) == ("held_open_mr", ("checkout-api !7",))
    assert answer_detail(answer, tracker="Jira") == "Not moved: checkout-api !7 is still open."
    assert tracker.transitions == []


# ---- the race: the DM and the console at the same moment -----------------------------


async def test_a_dm_yes_and_a_console_yes_at_once_write_the_issue_once() -> None:
    collector, tracker, chat, store = await _held_liam(_SlowTracker)
    service = _changes(collector, store)
    items = (await service.changes(_TENANT, _LIAM)).items

    dm, console_chk3, console_chk4 = await asyncio.gather(
        collector.handle_reply(
            _liam_says("yes", "msg-liam-yes", datetime(2026, 10, 3, 12, 21, tzinfo=UTC))
        ),
        service.answer(_TENANT, _LIAM, _item_id(items, "CHK-3"), "yes"),
        service.answer(_TENANT, _LIAM, _item_id(items, "CHK-4"), "yes"),
    )

    # Each issue moved exactly once, whoever answered it first.
    assert sorted(tracker.transitions) == [
        (_TENANT, "CHK-3", "in_review"),
        (_TENANT, "CHK-4", "in_progress"),
    ]
    assert len(tracker.comments) == 2
    rows = await _liam_rows(store)
    for key in ("CHK-3", "CHK-4"):
        assert [row.status for row in rows[key]].count(WriteBackStatus.APPLIED) == 1
    outcomes = {console_chk3.outcome, console_chk4.outcome}
    assert outcomes <= {"applied", "already_answered"}
    assert dm.kind == "processed"
    # The check-in is recorded once, with one ack.
    assert (await _checkin(store)).replied_at is not None
    assert len(_sent(chat, _STATUS_ACK)) == 1


async def test_without_the_lock_the_same_race_writes_twice() -> None:
    """What the per-correlation lock is for: the same two answers, no lock, two writes."""
    store = _UnlockedStore()
    tracker = _SlowTracker(
        issues={
            "CHK-3": Issue(
                tenant_id=_TENANT,
                key="CHK-3",
                title="Payment intent API",
                state=IssueState.TODO,
                assignee=UserRef(tenant_id=_TENANT, external_id=_LIAM),
            )
        }
    )
    await store.set_writeback_enabled(_TENANT, True)
    service = WriteBackService(
        issue_tracker=tracker,
        audit_repository=store,
        config_repository=store,
        status_repository=store,
    )
    await store.record(
        WriteBackAudit(
            id="p1",
            tenant_id=_TENANT,
            developer_id=_LIAM,
            issue_key="CHK-3",
            correlation_id="corr",
            status=WriteBackStatus.PROPOSED,
            target_state="in_review",
            before_state="todo",
            created_at=_NOW,
        )
    )

    await asyncio.gather(
        *(
            service.resolve_proposal(
                tenant_id=_TENANT, developer_id=_LIAM, audit_id="p1", decision="yes"
            )
            for _ in range(2)
        )
    )

    assert len(tracker.transitions) == 2

    # With the lock (the real store), the same two answers write once.
    locked = InMemoryGraphStore()
    tracker.transitions.clear()
    await locked.set_writeback_enabled(_TENANT, True)
    service = WriteBackService(
        issue_tracker=tracker,
        audit_repository=locked,
        config_repository=locked,
        status_repository=locked,
    )
    await locked.record(
        WriteBackAudit(
            id="p1",
            tenant_id=_TENANT,
            developer_id=_LIAM,
            issue_key="CHK-3",
            correlation_id="corr",
            status=WriteBackStatus.PROPOSED,
            target_state="in_review",
            before_state="todo",
            created_at=_NOW,
        )
    )
    answers = await asyncio.gather(
        *(
            service.resolve_proposal(
                tenant_id=_TENANT, developer_id=_LIAM, audit_id="p1", decision="yes"
            )
            for _ in range(2)
        )
    )
    assert len(tracker.transitions) == 1
    assert sorted(answer.outcome for answer in answers) == ["already_answered", "applied"]


async def test_the_close_out_and_a_console_yes_at_once_never_both_land() -> None:
    collector, tracker, _, store = await _held_liam(_SlowTracker)
    write_back = _write_back(collector)
    [chk3, _chk4] = await write_back.list_pending_proposals(_TENANT, _LIAM_CORRELATION)

    answer, expired = await asyncio.gather(
        write_back.resolve_proposal(
            tenant_id=_TENANT, developer_id=_LIAM, audit_id=chk3.id, decision="yes"
        ),
        write_back.expire_pending_proposals(
            tenant_id=_TENANT, developer_id=_LIAM, correlation_id=_LIAM_CORRELATION
        ),
    )

    rows = await _liam_rows(store)
    terminal = [row for row in rows["CHK-3"] if row.status is not WriteBackStatus.PROPOSED]
    assert len(terminal) == 1  # applied, or expired: never both
    if answer.outcome == "applied":
        assert tracker.transitions == [(_TENANT, "CHK-3", "in_review")]
        assert {row.issue_key for row in expired} == {"CHK-4"}
    else:
        assert answer.outcome == "already_answered"
        assert tracker.transitions == []


# ---- a revert -------------------------------------------------------------------------


async def test_a_console_yes_can_be_reverted_once_and_is_not_offered_again() -> None:
    collector, tracker, _, store = await _held_liam()
    service = _changes(collector, store)
    items = (await service.changes(_TENANT, _LIAM)).items
    await service.answer(_TENANT, _LIAM, _item_id(items, "CHK-3"), "yes")
    applied = (await _liam_rows(store))["CHK-3"][-1]
    write_back = _write_back(collector)

    reverted = await write_back.revert(applied)
    again = await write_back.revert(applied)

    assert reverted is not None and reverted.status is WriteBackStatus.REVERTED
    assert again is None  # a second revert never moves it twice
    assert tracker.transitions == [
        (_TENANT, "CHK-3", "in_review"),
        (_TENANT, "CHK-3", "todo"),
    ]
    keys = {item.issue_key for item in (await service.changes(_TENANT, _LIAM)).items}
    assert "CHK-3" not in keys


# ---- suggestions: a merged merge request, the issue still open -----------------------


def _merged_drift(key: str = "CHK-3", owner: str = _LIAM) -> DriftFinding:
    return DriftFinding(
        tenant_id=_TENANT,
        kind=DriftFindingKind.MERGED_ISSUE_OPEN,
        severity=Rag.AMBER,
        entity_ref=EntityRef(tenant_id=_TENANT, kind=NodeKind.TASK, id=key),
        workstream_id=None,
        reason=(
            f"'Payment intent API' in checkout-api was merged 2 days ago, but {key} is "
            "still 'To Do' in the issue tracker."
        ),
        detected_at=_NOW,
        owner_id=owner,
        evidence=RiskEvidence(identifier="checkout-api#1"),
    )


async def _liam_with_a_suggestion() -> tuple[
    StatusCollector, FakeIssueTracker, InMemoryGraphStore, JiraChangesService
]:
    collector, tracker, _, store = await _liam_collector([])
    service = _changes(
        collector, store, drift=(_merged_drift(), _merged_drift("CHK-9", owner="U-NOAH"))
    )
    return collector, tracker, store, service


async def test_a_merged_issue_of_his_own_is_suggested_and_a_yes_moves_it_once() -> None:
    _, tracker, store, service = await _liam_with_a_suggestion()
    [item] = (await service.changes(_TENANT, _LIAM)).items
    assert (item.id, item.kind, item.target_label) == ("suggestion:CHK-3", "suggestion", "Done")
    assert item.why.startswith("'Payment intent API' in checkout-api was merged 2 days ago")
    assert item.gate is not None and item.gate.state == "open"

    answers = await asyncio.gather(
        service.answer(_TENANT, _LIAM, item.id, "yes"),
        service.answer(_TENANT, _LIAM, item.id, "yes"),
    )

    assert sorted(answer.outcome for answer in answers) == ["already_answered", "applied"]
    assert tracker.transitions == [(_TENANT, "CHK-3", "done")]
    [row] = await store.list_writeback_by_correlation(
        _TENANT, suggestion_correlation("CHK-3", _TODAY)
    )
    assert (row.status, row.source) == (WriteBackStatus.APPLIED, CONSOLE_SOURCE)
    assert (await service.changes(_TENANT, _LIAM)).items == ()


async def test_a_no_to_a_suggestion_keeps_it_away_for_the_day() -> None:
    _, tracker, store, service = await _liam_with_a_suggestion()

    answer = await service.answer(_TENANT, _LIAM, "suggestion:CHK-3", "no")

    assert (answer.outcome, answer.detail) == (
        "declined",
        "Left CHK-3 as it is in the issue tracker.",
    )
    assert tracker.transitions == []
    [row] = await store.list_writeback_by_correlation(
        _TENANT, suggestion_correlation("CHK-3", _TODAY)
    )
    assert (row.status, row.source) == (WriteBackStatus.DECLINED, CONSOLE_SUGGESTION_SOURCE)
    assert (await service.changes(_TENANT, _LIAM)).items == ()
    # Someone else's merged issue is never his to answer.
    with pytest.raises(JiraChangeNotFound):
        await service.answer(_TENANT, _LIAM, "suggestion:CHK-9", "yes")


async def test_console_moves_off_closes_the_suggestions_yes_but_not_the_proposals() -> None:
    collector, tracker, store, service = await _liam_with_a_suggestion()

    async def kind_on(tenant_id: str, kind: JiraWriteKind) -> bool:
        return kind is not JiraWriteKind.CONSOLE_MOVES

    _write_back(collector)._kind_switch = kind_on

    [item] = (await service.changes(_TENANT, _LIAM)).items
    assert item.gate is not None and (item.gate.state, item.gate.yes_allowed) == ("off", False)
    answer = await service.answer(_TENANT, _LIAM, item.id, "yes")

    assert answer.outcome == "off"
    assert answer.detail == "Not moved: updating the issue tracker from the console is off."
    assert tracker.transitions == []
    assert (
        await _write_back(collector).answer_gate(
            _TENANT, _LIAM, "CHK-3", "in_review", JiraWriteKind.CHECKIN_UPDATES
        )
    ).state == "open"


async def test_a_suggestion_already_proposed_by_the_check_in_is_listed_once() -> None:
    collector, _, _, store = await _held_liam()
    service = _changes(collector, store, drift=(_merged_drift("CHK-3"),))

    items = (await service.changes(_TENANT, _LIAM)).items

    assert [(item.kind, item.issue_key) for item in items] == [
        ("proposal", "CHK-3"),
        ("proposal", "CHK-4"),
    ]


# ---- due dates: never written, "change it in Jira" ----------------------------------


def _focus(*tasks: FocusTaskView) -> FocusView:
    return FocusView(
        developer_id=_LIAM,
        developer_name="Liam Chen",
        as_of=_TODAY,
        status_source=StatusSource.CONFIRMED,
        developer_confirmed=False,
        status_as_of=_TODAY,
        summary="",
        blockers=(),
        blocker_details=(),
        tasks=tasks,
        focus=(),
    )


def _task(
    key: str, *, eta: date | None, due: date | None, status: str = "In Progress"
) -> FocusTaskView:
    return FocusTaskView(
        id=key,
        name=f"Work on {key}",
        rag=Rag.GREEN,
        source=StatusSource.CONFIRMED,
        confidence=None,
        deadline=due,
        tracker_status=status,
        my_eta=eta,
    )


async def test_an_eta_past_the_due_date_reads_change_it_in_jira_and_takes_no_answer() -> None:
    collector, tracker, _, store = await _liam_collector([])
    service = _changes(
        collector,
        store,
        focus=_focus(
            _task("CHK-3", eta=date(2026, 10, 16), due=date(2026, 10, 10)),
            _task("CHK-4", eta=date(2026, 10, 8), due=date(2026, 10, 10)),
            _task("CHK-5", eta=date(2026, 10, 16), due=None),
            _task("CHK-6", eta=date(2026, 10, 16), due=date(2026, 10, 10), status="Done"),
        ),
    )

    [item] = (await service.changes(_TENANT, _LIAM)).items

    assert (item.id, item.kind, item.gate) == ("due:CHK-3", "due_date", None)
    assert item.why == "You gave Oct 16 as its ETA; its due date in the issue tracker is Oct 10."
    assert (item.due_on, item.eta_on) == (date(2026, 10, 10), date(2026, 10, 16))
    with pytest.raises(JiraChangeNotAnswerable, match="Change the due date in Jira"):
        await service.answer(_TENANT, _LIAM, item.id, "yes")
    assert DUE_DATE_ANSWER.startswith("Change the due date in Jira")
    assert tracker.transitions == []


# ---- words ----------------------------------------------------------------------------


def test_the_dm_line_names_each_outcome_and_what_still_waits() -> None:
    def answer(outcome: str, key: str = "CHK-3", **extra: object) -> ProposalAnswer:
        return ProposalAnswer(
            outcome=outcome,  # type: ignore[arg-type]
            issue_key=key,
            target_state="done",
            **extra,  # type: ignore[arg-type]
        )

    assert console_answer_line([answer("applied")], tracker="Jira") == (
        "Answered in the console: CHK-3 moved to Done in Jira."
    )
    assert console_answer_line(
        [answer("held_open_mr", merge_requests=("api !2", "web !5"))], tracker="Jira"
    ) == ("Answered in the console: CHK-3 not moved, api !2, web !5 are still open.")
    assert console_answer_line([answer("already_answered"), answer("off")]) is None
    pending = WriteBackAudit(
        id="x",
        tenant_id=_TENANT,
        developer_id=_LIAM,
        issue_key="CHK-4",
        correlation_id="c",
        status=WriteBackStatus.PROPOSED,
        target_state="done",
        created_at=_NOW,
    )
    assert console_answer_line([answer("declined")], [pending]) == (
        "Answered in the console: CHK-3 left as is, as you asked. "
        "CHK-4 still waits for your yes or no."
    )


def test_why_a_proposal_is_asked_names_the_state_and_the_day_never_the_words() -> None:
    proposal = WriteBackAudit(
        id="x",
        tenant_id=_TENANT,
        developer_id=_LIAM,
        issue_key="CHK-3",
        correlation_id="c",
        status=WriteBackStatus.PROPOSED,
        target_state="done",
        created_at=datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
    )

    assert proposal_why(proposal, None, _TODAY) == "You said it is done in your check-in of Oct 2."
    assert proposal_why(proposal, None, date(2026, 10, 2)) == (
        "You said it is done in today's check-in."
    )


async def test_a_held_check_in_another_member_shares_a_chat_id_with_is_not_theirs() -> None:
    collector, _, _, store = await _held_liam()
    await store.record_checkin_correlation(
        CheckInCorrelation(
            tenant_id=_TENANT,
            developer_id="U-OTHER",
            correlation_id="corr-other",
            chat_user_ref=_LIAM,
            chat_thread_ref="dm-liam",
            outbound_message_id="msg-other",
            asked_at=_NOW,
        )
    )

    view = await _changes(collector, store).changes(_TENANT, "U-OTHER")

    assert view.items == ()
