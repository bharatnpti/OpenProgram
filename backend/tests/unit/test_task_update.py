"""A developer updates one task at a time (frontend-v3 step 7).

``POST /me/tasks/{task_id}/update``: the state and a note on the task, the
person's own ETA, a blocker added or resolved, and optionally a move of the
tracker issue. Each lands where its kind already lives, today only, and the
person's trees are rolled up again so their pod reads it at once.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from config.settings import Settings
from core.application.blocker_resolution import BlockerResolutionService
from core.application.checkin_drift import CHECKIN_DRIFT_FACT_SOURCE, ETA_STATED
from core.application.merge_request_links import MERGE_REQUEST_FACT_SOURCE
from core.application.persona_views import PersonaViewService
from core.application.rollup_service import PersonRollups, RollupService
from core.application.self_status_service import SelfStatusService
from core.application.status_summaries import TASK_UPDATE_LEAD
from core.application.sync_services import ISSUE_FACT_SOURCE
from core.application.task_update_service import (
    TASK_NOT_ASSIGNED,
    TASK_UPDATE_FACT_SOURCE,
    TaskNotAssigned,
    TaskUpdate,
    TaskUpdateRejected,
    TaskUpdateService,
    largest_slip,
)
from core.application.writeback_service import CONSOLE_SOURCE, WriteBackService
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.errors import ProviderUnavailable
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
)
from core.domain.identity import IdentityLink
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.rollup import FactorKind, Rag
from core.domain.status import CheckInPreference, DeveloperStatus, StatusSource, WriteBackConsent
from core.domain.writeback import WriteBackStatus, WriteBackTarget
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from tests.contract.fakes import FakeIssueTracker

_TENANT = "demo"
_DEV = "U-kai"
_OTHER = "U-liam"
_JIRA_ACCOUNT = "jira-kai"
_TODAY = date(2026, 10, 8)
_NOW = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
_POD = EntityRef(tenant_id=_TENANT, kind=NodeKind.POD, id="pod-pay")


def _ticket(key: str, *, status: str = "In Progress", state: str = "in_progress") -> Task:
    return Task(
        tenant_id=_TENANT,
        id=key,
        name=f"Work on {key}",
        metadata={"key": key, "status": status, "state": state, "project_key": "CHK"},
    )


async def _team(store: InMemoryGraphStore) -> None:
    """Program > project > pod > Kai and Liam; Kai has CHK-4, CHK-5 and a seeded task."""
    nodes = (
        Program(tenant_id=_TENANT, id="prog", name="Commerce"),
        Project(tenant_id=_TENANT, id="checkout", name="Checkout"),
        Pod(tenant_id=_TENANT, id="pod-pay", name="Payments"),
        Developer(tenant_id=_TENANT, id=_DEV, name="Kai Thompson"),
        Developer(tenant_id=_TENANT, id=_OTHER, name="Liam Chen"),
        _ticket("CHK-4"),
        _ticket("CHK-5", status="To Do", state="todo"),
        _ticket("CHK-9"),
        Task(tenant_id=_TENANT, id="task-seed", name="Capture retry runbook"),
    )
    for node in nodes:
        await store.upsert_node(node)
    for parent, child in (
        ("prog", "checkout"),
        ("checkout", "pod-pay"),
        ("pod-pay", _DEV),
        ("pod-pay", _OTHER),
        ("checkout", "CHK-4"),
        ("checkout", "CHK-5"),
        ("checkout", "CHK-9"),
        ("checkout", "task-seed"),
    ):
        await store.add_edge(_edge(parent, child, EdgeKind.CONTAINS))
    for person, task in (
        (_DEV, "CHK-4"),
        (_DEV, "CHK-5"),
        (_DEV, "task-seed"),
        (_OTHER, "CHK-9"),
    ):
        await store.add_edge(_edge(person, task, EdgeKind.ASSIGNED_TO))


def _edge(parent: str, child: str, kind: EdgeKind) -> GraphEdge:
    return GraphEdge(tenant_id=_TENANT, from_node_id=parent, to_node_id=child, kind=kind)


def _blocker(
    blocker_id: str,
    description: str,
    *,
    work_item_id: str | None,
    first_seen_on: date = date(2026, 10, 1),
    last_seen_on: date = date(2026, 10, 5),
) -> DeveloperBlocker:
    return DeveloperBlocker(
        tenant_id=_TENANT,
        blocker_id=blocker_id,
        developer_id=_DEV,
        description=description,
        normalized_key=normalize_blocker_key(description),
        work_item_id=work_item_id,
        source=BlockerSource.CHECKIN,
        first_seen_on=first_seen_on,
        last_seen_on=last_seen_on,
    )


async def _status(
    store: InMemoryGraphStore,
    *,
    as_of: date = date(2026, 10, 7),
    source: StatusSource = StatusSource.CONFIRMED,
    summary: str = "Reviewed the payment intent API.",
    blockers: tuple[DeveloperBlocker, ...] = (),
    eta_change_days: int | None = None,
) -> None:
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id=_TENANT,
            developer_id=_DEV,
            as_of=as_of,
            source=source,
            blockers=tuple(blocker.description for blocker in blockers),
            summary=summary,
            eta_change_days=eta_change_days,
            developer_confirmed=source is StatusSource.CONFIRMED,
        ),
        blockers,
    )


def _rollups(store: InMemoryGraphStore) -> PersonRollups:
    return PersonRollups(
        graph_repository=store,
        rollup_repository=store,
        service_factory=lambda: RollupService(store, store, BlockerResolutionService(store, store)),
    )


def _write_back(
    store: InMemoryGraphStore, tracker: FakeIssueTracker, *, enabled: bool = True
) -> WriteBackService:
    return WriteBackService(
        issue_tracker=tracker,
        audit_repository=store,
        config_repository=store,
        status_repository=store,
        identity_link_repository=store,
        time_series_repository=store,
        graph_repository=store,
        writeback_enabled_default=enabled,
        clock=lambda: _NOW,
    )


def _service(
    store: InMemoryGraphStore,
    *,
    write_back: WriteBackService | None = None,
    rollups: PersonRollups | None = None,
) -> TaskUpdateService:
    return TaskUpdateService(
        graph_repository=store,
        status_repository=store,
        time_series_repository=store,
        write_back=write_back,
        rollups=rollups,
        tracker_name="Jira",
        clock=lambda: _NOW,
        today=lambda: _TODAY,
    )


async def _update(
    store: InMemoryGraphStore,
    task_id: str = "CHK-4",
    *,
    service: TaskUpdateService | None = None,
    as_of: date = _TODAY,
    **fields: object,
) -> DeveloperStatus:
    result = await (service or _service(store)).update(
        _TENANT,
        _DEV,
        task_id,
        as_of,
        TaskUpdate(**fields),  # type: ignore[arg-type]
    )
    return result.status


def _facts(store: InMemoryGraphStore, source: str) -> list[FactEvent]:
    return [fact for fact in store._facts if fact.source == source]  # noqa: SLF001


# Blockers ----------------------------------------------------------------------------


async def test_adding_a_blocker_carries_the_persons_other_blockers_forward_untouched() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    own = _blocker("b-own", "Waiting on code review", work_item_id="CHK-4")
    other = _blocker("b-other", "Staging is down", work_item_id="CHK-5")
    loose = _blocker("b-loose", "Need design sign-off", work_item_id=None)
    await _status(store, blockers=(own, other, loose))

    status = await _update(store, add_blocker="Waiting on sandbox credentials")

    open_now = {
        blocker.blocker_id: blocker for blocker in await store.open_blockers(_TENANT, _DEV, _TODAY)
    }
    # The other task's and the unattributed blocker are exactly as they were:
    # not restated, so their last_seen_on keeps their age honest.
    assert open_now["b-other"] == other
    assert open_now["b-loose"] == loose
    # A second blocker on CHK-4 is a new one: the first keeps its own words.
    assert open_now["b-own"] == own
    added = [
        blocker
        for blocker in open_now.values()
        if blocker.blocker_id not in {"b-own", "b-other", "b-loose"}
    ]
    assert len(added) == 1
    assert added[0].description == "Waiting on sandbox credentials"
    assert added[0].work_item_id == "CHK-4"
    assert added[0].source is BlockerSource.CORRECTION
    assert (added[0].first_seen_on, added[0].last_seen_on) == (_TODAY, _TODAY)
    assert set(status.blockers) == {
        "Waiting on code review",
        "Staging is down",
        "Need design sign-off",
        "Waiting on sandbox credentials",
    }


async def test_an_added_blocker_worded_like_an_unattributed_one_is_that_blocker() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    loose = _blocker("b-loose", "Need design sign-off", work_item_id=None)
    await _status(store, blockers=(loose,))

    await _update(store, add_blocker="need design sign-off.")

    [only] = await store.open_blockers(_TENANT, _DEV, _TODAY)
    assert only.blocker_id == "b-loose"
    assert only.work_item_id == "CHK-4"
    assert only.first_seen_on == loose.first_seen_on


async def test_resolving_a_blocker_by_id_closes_only_that_one() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    first = _blocker("b-1", "Waiting on code review", work_item_id="CHK-4")
    second = _blocker("b-2", "Waiting on sandbox credentials", work_item_id="CHK-4")
    other = _blocker("b-other", "Staging is down", work_item_id="CHK-5")
    await _status(store, blockers=(first, second, other))

    status = await _update(store, resolve_blocker_ids=("b-1",))

    assert sorted(
        blocker.blocker_id for blocker in await store.open_blockers(_TENANT, _DEV, _TODAY)
    ) == ["b-2", "b-other"]
    assert set(status.blockers) == {"Waiting on sandbox credentials", "Staging is down"}


async def test_resolving_a_blocker_that_is_not_open_on_this_task_is_refused() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    other = _blocker("b-other", "Staging is down", work_item_id="CHK-5")
    await _status(store, blockers=(other,))

    with pytest.raises(TaskUpdateRejected, match="Not an open blocker of this task: b-other"):
        await _update(store, resolve_blocker_ids=("b-other",))
    with pytest.raises(TaskUpdateRejected, match="nope"):
        await _update(store, resolve_blocker_ids=("nope",))

    assert await store.open_blockers(_TENANT, _DEV, _TODAY) == [other]


async def test_blocked_needs_a_blocker() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    other = _blocker("b-other", "Staging is down", work_item_id="CHK-5")
    own = _blocker("b-own", "Waiting on code review", work_item_id="CHK-4")
    await _status(store, blockers=(other, own))
    blocked = WriteBackTarget.BLOCKED

    # CHK-5's only blocker resolved in the same update: none would be left.
    with pytest.raises(TaskUpdateRejected, match="Blocked needs a blocker"):
        await _update(store, "CHK-5", state=blocked, resolve_blocker_ids=("b-other",))
    # The seeded task has none at all.
    with pytest.raises(TaskUpdateRejected, match="Blocked needs a blocker"):
        await _update(store, "task-seed", state=blocked)

    # An open one on the task will do, and so will one added here.
    await _update(store, "CHK-4", state=blocked)
    await _update(store, "task-seed", state=blocked, add_blocker="Runbook owner is out")


# Refusals ----------------------------------------------------------------------------


async def test_an_eta_before_today_is_refused_and_nothing_is_written() -> None:
    store = InMemoryGraphStore()
    await _team(store)

    with pytest.raises(TaskUpdateRejected, match="ETA can't be before today"):
        await _update(store, eta=date(2026, 10, 7), eta_sent=True, note="Late")

    assert store._facts == []  # noqa: SLF001
    assert await store.latest_developer_status(_TENANT, _DEV, _TODAY) is None


async def test_a_past_day_is_refused() -> None:
    store = InMemoryGraphStore()
    await _team(store)

    with pytest.raises(TaskUpdateRejected, match="today only"):
        await _update(store, as_of=date(2026, 10, 7), state=WriteBackTarget.IN_PROGRESS)


async def test_an_update_that_changes_nothing_is_refused() -> None:
    store = InMemoryGraphStore()
    await _team(store)

    with pytest.raises(TaskUpdateRejected, match="Nothing to update"):
        await _update(store, move_in_tracker=True)


async def test_someone_elses_task_is_not_assigned_to_you() -> None:
    store = InMemoryGraphStore()
    await _team(store)

    with pytest.raises(TaskNotAssigned, match=TASK_NOT_ASSIGNED):
        await _update(store, "CHK-9", state=WriteBackTarget.DONE)
    with pytest.raises(TaskNotAssigned):
        await _update(store, "CHK-404", state=WriteBackTarget.DONE)


# ETA ---------------------------------------------------------------------------------


async def test_an_eta_is_a_stated_eta_and_a_clear_leaves_the_team_date_without_it() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    registry = ServiceRegistry(
        Settings(
            _env_file=None,
            secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
            runtime_mode="memory",
        ),
        graph_store=store,
    )
    forecast = registry.forecast_service()

    await _update(store, eta=date(2026, 10, 9), eta_sent=True)
    [stated] = _facts(store, CHECKIN_DRIFT_FACT_SOURCE)
    assert stated.entity_ref == EntityRef(tenant_id=_TENANT, kind=NodeKind.TASK, id="CHK-4")
    assert stated.correlation_id.startswith("console:")
    assert {key: stated.payload[key] for key in ("kind", "issue_key", "developer_id")} == {
        "kind": ETA_STATED,
        "issue_key": "CHK-4",
        "developer_id": _DEV,
    }
    assert (stated.payload["eta_date"], stated.payload["eta_label"]) == ("2026-10-09", "Oct 9")
    assert await forecast._etas(_TENANT, {"CHK-4"}) == {"CHK-4": date(2026, 10, 9)}  # noqa: SLF001

    later = _service(store)
    later._clock = lambda: datetime(2026, 10, 8, 11, 0, tzinfo=UTC)  # noqa: SLF001
    await _update(store, service=later, eta=None, eta_sent=True)

    cleared = _facts(store, CHECKIN_DRIFT_FACT_SOURCE)[-1]
    assert (cleared.payload["eta_date"], cleared.payload["eta_start"]) == (None, None)
    assert await forecast._etas(_TENANT, {"CHK-4"}) == {}  # noqa: SLF001


async def test_eta_change_days_is_the_largest_slip_of_the_days_updates() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    for key, day in (("CHK-4", "2026-10-09"), ("CHK-5", "2026-10-10")):
        await store.append_fact(
            FactEvent(
                tenant_id=_TENANT,
                source=CHECKIN_DRIFT_FACT_SOURCE,
                entity_ref=EntityRef(tenant_id=_TENANT, kind=NodeKind.TASK, id=key),
                payload={
                    "kind": ETA_STATED,
                    "issue_key": key,
                    "developer_id": _DEV,
                    "as_of": "2026-10-06",
                    "eta_label": "Friday",
                    "eta_date": day,
                },
                observed_at=datetime(2026, 10, 6, 9, 0, tzinfo=UTC),
                correlation_id=f"chat-{key}",
            )
        )

    first = await _update(store, eta=date(2026, 10, 12), eta_sent=True)
    second = await _update(store, "CHK-5", eta=date(2026, 10, 11), eta_sent=True)

    assert first.eta_change_days == 3
    assert second.eta_change_days == 3
    assert largest_slip([None, -2, 0, -5]) == -5
    assert largest_slip([None, 0]) is None
    assert largest_slip([-5, 1]) == 1


# Today's status ----------------------------------------------------------------------


async def test_with_no_reply_today_the_update_is_a_partial_status_rebuilt_as_it_grows() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    await _status(store)  # yesterday's reply

    first = await _update(
        store,
        state=WriteBackTarget.IN_REVIEW,
        eta=date(2026, 10, 9),
        eta_sent=True,
        note="MR !3 open, waiting on Liam",
    )
    assert first.as_of == _TODAY
    assert first.source is StatusSource.PARTIAL
    assert not first.developer_confirmed
    assert first.summary == f"{TASK_UPDATE_LEAD} CHK-4 in review, ETA Oct 9."

    second = await _update(store, "CHK-5", state=WriteBackTarget.DONE)
    third = await _update(store, "task-seed", note="Drafted the outline")
    assert second.summary == f"{TASK_UPDATE_LEAD} CHK-4 in review, ETA Oct 9; CHK-5 done."
    assert third.summary == (
        f"{TASK_UPDATE_LEAD} CHK-4 in review, ETA Oct 9; CHK-5 done; Capture retry runbook updated."
    )
    assert await store.latest_developer_status(_TENANT, _DEV, _TODAY) == third


async def test_a_reply_on_record_today_keeps_its_source_and_summary() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    await _status(store, as_of=_TODAY, summary="Shipped the refund flow.", eta_change_days=1)

    status = await _update(store, add_blocker="Waiting on sandbox credentials")

    assert status.source is StatusSource.CONFIRMED
    assert status.developer_confirmed
    assert status.summary == "Shipped the refund flow."
    assert status.blockers == ("Waiting on sandbox credentials",)
    assert status.eta_change_days == 1


async def test_a_non_response_status_today_gives_way_to_the_partial_update() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    await _status(
        store,
        as_of=_TODAY,
        source=StatusSource.UNKNOWN,
        summary="No confirmed check-in after a nudge. Current status is unknown.",
    )

    status = await _update(store, state=WriteBackTarget.IN_PROGRESS)

    assert status.source is StatusSource.PARTIAL
    assert status.summary == f"{TASK_UPDATE_LEAD} CHK-4 in progress."
    assert status.blockers == ()


# Rollup ------------------------------------------------------------------------------


async def test_the_pod_reads_the_update_at_once() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    await _status(store)
    # Today's rollup as the hourly run left it, before the update.
    await _rollups(store).record(_TENANT, _DEV, _TODAY)
    before = await store.latest_node_status(_TENANT, _POD, _TODAY)
    assert before is not None
    assert not any(factor.kind is FactorKind.BLOCKER for factor in before.factors)

    await _update(
        store, service=_service(store, rollups=_rollups(store)), add_blocker="Staging is down"
    )

    pod = await store.latest_node_status(_TENANT, _POD, _TODAY)
    assert pod is not None and pod.as_of == _TODAY
    assert pod != before
    # A blocker that is not on the critical path turns the pod amber.
    assert pod.rag is Rag.AMBER
    assert any(
        factor.kind is FactorKind.BLOCKER and "Staging is down" in factor.description
        for factor in pod.factors
    )
    kai = await store.latest_node_status(
        _TENANT, EntityRef(tenant_id=_TENANT, kind=NodeKind.DEVELOPER, id=_DEV), _TODAY
    )
    assert kai is not None and kai.source is StatusSource.PARTIAL
    program = await store.latest_node_status(
        _TENANT, EntityRef(tenant_id=_TENANT, kind=NodeKind.PROGRAM, id="prog"), _TODAY
    )
    assert program is not None and program.as_of == _TODAY


async def test_confirm_and_correct_roll_the_pod_up_at_once() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    await _status(store, as_of=_TODAY, source=StatusSource.PARTIAL, summary="Halfway.")
    service = SelfStatusService(store, rollups=_rollups(store))

    await service.confirm(_TENANT, _DEV, _TODAY)
    confirmed = await store.latest_node_status(
        _TENANT, EntityRef(tenant_id=_TENANT, kind=NodeKind.DEVELOPER, id=_DEV), _TODAY
    )
    assert confirmed is not None and confirmed.source is StatusSource.CONFIRMED

    await service.correct(
        _TENANT,
        _DEV,
        _TODAY,
        summary="Blocked on staging.",
        blockers=("Staging is down",),
        eta_change_days=None,
    )
    pod = await store.latest_node_status(_TENANT, _POD, _TODAY)
    assert pod is not None and pod.rag is Rag.AMBER
    assert any(
        factor.kind is FactorKind.BLOCKER and "Staging is down" in factor.description
        for factor in pod.factors
    )


class _BrokenRollups(PersonRollups):
    async def record(self, tenant_id: str, developer_id: str, as_of: date) -> tuple[()]:
        raise RuntimeError("lock timeout")


async def test_a_failed_rollup_leaves_the_update_recorded() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    broken = _BrokenRollups(
        graph_repository=store,
        rollup_repository=store,
        service_factory=lambda: None,  # type: ignore[arg-type,return-value]
    )

    status = await _update(
        store, service=_service(store, rollups=broken), state=WriteBackTarget.IN_PROGRESS
    )

    assert await store.latest_developer_status(_TENANT, _DEV, _TODAY) == status


async def test_a_person_in_no_team_gets_their_own_cell() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    await store.upsert_node(Developer(tenant_id=_TENANT, id="U-elena", name="Elena"))
    await store.add_edge(_edge("U-elena", "CHK-9", EdgeKind.ASSIGNED_TO))
    service = TaskUpdateService(
        graph_repository=store,
        status_repository=store,
        time_series_repository=store,
        rollups=_rollups(store),
        today=lambda: _TODAY,
    )

    await service.update(
        _TENANT, "U-elena", "CHK-9", _TODAY, TaskUpdate(state=WriteBackTarget.IN_PROGRESS)
    )

    elena = await store.latest_node_status(
        _TENANT, EntityRef(tenant_id=_TENANT, kind=NodeKind.DEVELOPER, id="U-elena"), _TODAY
    )
    assert elena is not None
    assert any(factor.kind is FactorKind.NO_POD for factor in elena.factors)


# Tracker -----------------------------------------------------------------------------


def _issue(key: str, state: IssueState, *, assignee: str | None = _DEV) -> Issue:
    return Issue(
        tenant_id=_TENANT,
        key=key,
        title=f"Work on {key}",
        state=state,
        assignee=UserRef(tenant_id=_TENANT, external_id=assignee) if assignee else None,
        metadata={"status": {IssueState.IN_PROGRESS: "In Progress"}.get(state, state.value)},
    )


async def _tracked(
    store: InMemoryGraphStore,
    issue: Issue,
    *,
    consent: WriteBackConsent = WriteBackConsent.ALWAYS_ASK,
    enabled: bool = True,
) -> tuple[TaskUpdateService, FakeIssueTracker]:
    await _team(store)
    await store.record_checkin_preference(
        CheckInPreference(tenant_id=_TENANT, developer_id=_DEV, write_back_consent=consent)
    )
    tracker = FakeIssueTracker(issues={issue.key: issue})
    return _service(store, write_back=_write_back(store, tracker, enabled=enabled)), tracker


async def _move(service: TaskUpdateService, state: WriteBackTarget, **fields: object) -> object:
    result = await service.update(
        _TENANT,
        _DEV,
        "CHK-4",
        _TODAY,
        TaskUpdate(state=state, move_in_tracker=True, **fields),  # type: ignore[arg-type]
    )
    return result.tracker


async def test_the_tick_is_the_yes_of_always_ask_and_never_writes_the_eta() -> None:
    store = InMemoryGraphStore()
    service, tracker = await _tracked(store, _issue("CHK-4", IssueState.IN_PROGRESS))

    outcome = await _move(service, WriteBackTarget.DONE, eta=date(2026, 10, 9), eta_sent=True)

    assert outcome is not None
    assert (outcome.outcome, outcome.detail) == ("applied", "Moved to Done in Jira.")  # type: ignore[attr-defined]
    assert tracker.transitions == [(_TENANT, "CHK-4", "done")]
    [(_, _, comment)] = tracker.comments
    assert comment == (
        "Moved to Done by OpenProgram: Kai Thompson reported it done in OpenProgram on 2026-10-08."
    )
    rows = await store.list_for_issue(_TENANT, "CHK-4")
    assert [(row.status, row.source) for row in rows] == [(WriteBackStatus.APPLIED, CONSOLE_SOURCE)]


async def test_never_is_refused() -> None:
    store = InMemoryGraphStore()
    service, tracker = await _tracked(
        store, _issue("CHK-4", IssueState.IN_PROGRESS), consent=WriteBackConsent.NEVER
    )

    outcome = await _move(service, WriteBackTarget.DONE)

    assert outcome.outcome == "off"  # type: ignore[attr-defined]
    assert outcome.detail == "Not moved in Jira: write-back is off."  # type: ignore[attr-defined]
    assert tracker.transitions == []
    assert await store.list_for_issue(_TENANT, "CHK-4") == []


async def test_a_closed_system_gate_is_off() -> None:
    store = InMemoryGraphStore()
    service, tracker = await _tracked(store, _issue("CHK-4", IssueState.IN_PROGRESS), enabled=False)

    assert (await _move(service, WriteBackTarget.DONE)).outcome == "off"  # type: ignore[attr-defined]
    assert tracker.transitions == []


async def test_an_issue_assigned_to_someone_else_is_not_moved() -> None:
    store = InMemoryGraphStore()
    service, tracker = await _tracked(
        store, _issue("CHK-4", IssueState.IN_PROGRESS, assignee=_OTHER)
    )

    outcome = await _move(service, WriteBackTarget.DONE)

    assert outcome.outcome == "not_owner"  # type: ignore[attr-defined]
    assert outcome.detail == "Not moved in Jira: CHK-4 is assigned to someone else."  # type: ignore[attr-defined]
    assert tracker.transitions == []
    [row] = await store.list_for_issue(_TENANT, "CHK-4")
    assert (row.status, row.source) == (WriteBackStatus.DECLINED, "not_owner")


async def test_an_unassigned_issue_is_not_moved() -> None:
    store = InMemoryGraphStore()
    service, tracker = await _tracked(store, _issue("CHK-4", IssueState.IN_PROGRESS, assignee=None))

    outcome = await _move(service, WriteBackTarget.DONE)

    assert (outcome.outcome, outcome.detail) == (  # type: ignore[attr-defined]
        "not_owner",
        "Not moved in Jira: CHK-4 is not assigned to anyone.",
    )
    assert tracker.transitions == []


async def test_done_is_held_while_a_merge_request_is_open() -> None:
    store = InMemoryGraphStore()
    service, tracker = await _tracked(store, _issue("CHK-4", IssueState.IN_PROGRESS))
    await store.append_fact(
        FactEvent(
            tenant_id=_TENANT,
            source=MERGE_REQUEST_FACT_SOURCE,
            entity_ref=EntityRef(tenant_id=_TENANT, kind=NodeKind.DEVELOPER, id=_DEV),
            payload={
                "repo": "acme/insights-pipeline",
                "id": "1",
                "title": "CHK-4 step-up flow",
                "state": "opened",
                "merged": False,
                "source_branch": "feature/CHK-4",
                "web_url": "https://gitlab.example/acme/insights-pipeline/-/merge_requests/1",
            },
            observed_at=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
            correlation_id="mr-1",
        )
    )

    outcome = await _move(service, WriteBackTarget.DONE)

    assert outcome.outcome == "held_open_mr"  # type: ignore[attr-defined]
    assert outcome.merge_requests == ("insights-pipeline !1",)  # type: ignore[attr-defined]
    assert outcome.detail == "Not moved in Jira: insights-pipeline !1 is still open."  # type: ignore[attr-defined]
    assert tracker.transitions == []
    [row] = await store.list_for_issue(_TENANT, "CHK-4")
    assert (row.status, row.source) == (WriteBackStatus.DECLINED, "open_mr")


async def test_a_state_the_tracker_already_shows_is_no_change() -> None:
    store = InMemoryGraphStore()
    service, tracker = await _tracked(store, _issue("CHK-4", IssueState.IN_PROGRESS))

    outcome = await _move(service, WriteBackTarget.IN_REVIEW)

    assert outcome.outcome == "no_change"  # type: ignore[attr-defined]
    assert outcome.detail == "Already In Progress in Jira."  # type: ignore[attr-defined]
    assert tracker.transitions == []
    assert await store.list_for_issue(_TENANT, "CHK-4") == []


async def test_an_unreadable_issue_is_a_failed_move() -> None:
    store = InMemoryGraphStore()
    service, tracker = await _tracked(store, _issue("CHK-77", IssueState.IN_PROGRESS))

    outcome = await _move(service, WriteBackTarget.DONE)

    assert outcome.outcome == "failed"  # type: ignore[attr-defined]
    assert tracker.transitions == []
    [row] = await store.list_for_issue(_TENANT, "CHK-4")
    assert (row.status, row.source) == (WriteBackStatus.FAILED, CONSOLE_SOURCE)


async def test_an_unreadable_issue_whose_synced_copy_shows_the_state_is_no_change() -> None:
    store = InMemoryGraphStore()
    service, tracker = await _tracked(store, _issue("CHK-77", IssueState.IN_PROGRESS))

    outcome = await _move(service, WriteBackTarget.IN_PROGRESS)

    assert (outcome.outcome, outcome.detail) == ("no_change", "Already In Progress in Jira.")  # type: ignore[attr-defined]
    assert await store.list_for_issue(_TENANT, "CHK-4") == []


class _RejectingTracker(FakeIssueTracker):
    async def transition(self, tenant_id: str, key: str, to_state: str) -> None:
        raise ProviderUnavailable("no such transition")


async def test_a_rejected_transition_is_a_failed_move() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    tracker = _RejectingTracker(issues={"CHK-4": _issue("CHK-4", IssueState.IN_PROGRESS)})
    service = _service(store, write_back=_write_back(store, tracker))

    outcome = await _move(service, WriteBackTarget.DONE)

    assert (outcome.outcome, outcome.detail) == (  # type: ignore[attr-defined]
        "failed",
        "Not moved in Jira: the move to Done failed.",
    )
    [row] = await store.list_for_issue(_TENANT, "CHK-4")
    assert (row.status, row.source) == (WriteBackStatus.FAILED, CONSOLE_SOURCE)


@pytest.mark.parametrize(
    ("consent", "enabled", "mode"),
    [
        (WriteBackConsent.AUTO_APPLY, True, "auto"),
        (WriteBackConsent.ALWAYS_ASK, True, "ask"),
        (WriteBackConsent.NEVER, True, "off"),
        (WriteBackConsent.AUTO_APPLY, False, "off"),
    ],
)
async def test_the_write_back_mode_follows_the_gates_and_consent(
    consent: WriteBackConsent, enabled: bool, mode: str
) -> None:
    store = InMemoryGraphStore()
    await store.record_checkin_preference(
        CheckInPreference(tenant_id=_TENANT, developer_id=_DEV, write_back_consent=consent)
    )
    service = _write_back(store, FakeIssueTracker(), enabled=enabled)

    assert await service.write_back_mode(_TENANT, _DEV) == mode


async def test_no_tick_or_no_state_moves_nothing() -> None:
    store = InMemoryGraphStore()
    service, tracker = await _tracked(store, _issue("CHK-4", IssueState.IN_PROGRESS))

    without_tick = await service.update(
        _TENANT, _DEV, "CHK-4", _TODAY, TaskUpdate(state=WriteBackTarget.DONE)
    )
    without_state = await service.update(
        _TENANT, _DEV, "CHK-4", _TODAY, TaskUpdate(note="Almost", move_in_tracker=True)
    )

    assert (without_tick.tracker, without_state.tracker) == (None, None)
    assert tracker.transitions == []


# Focus -------------------------------------------------------------------------------


def _persona(store: InMemoryGraphStore) -> PersonaViewService:
    return PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
        identity_link_repository=store,
    )


async def _issue_fact(store: InMemoryGraphStore, key: str, assignee: str | None) -> None:
    await store.append_fact(
        FactEvent(
            tenant_id=_TENANT,
            source=ISSUE_FACT_SOURCE,
            entity_ref=EntityRef(tenant_id=_TENANT, kind=NodeKind.TASK, id=key),
            payload={"key": key, "state": "in_progress", "status": "red", "assignee_id": assignee},
            observed_at=datetime(2026, 10, 6, 9, 0, tzinfo=UTC),
            correlation_id=f"issue-{key}",
        )
    )


async def test_a_task_row_carries_the_persons_statement_eta_blockers_and_tracker_status() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    await store.upsert_identity_link(
        IdentityLink(tenant_id=_TENANT, developer_id=_DEV, jira_account_id=_JIRA_ACCOUNT)
    )
    await _issue_fact(store, "CHK-4", _JIRA_ACCOUNT)
    await _issue_fact(store, "CHK-5", "jira-someone-else")
    await _update(
        store,
        state=WriteBackTarget.IN_REVIEW,
        note="MR !3 open, waiting on Liam",
        eta=date(2026, 10, 9),
        eta_sent=True,
        add_blocker="Waiting on sandbox credentials",
    )
    # An ETA-only update later states neither a state nor a note.
    later = _service(store)
    later._clock = lambda: datetime(2026, 10, 8, 12, 0, tzinfo=UTC)  # noqa: SLF001
    await _update(store, service=later, eta=date(2026, 10, 12), eta_sent=True)

    view = await _persona(store).focus(_TENANT, _DEV, _TODAY)
    rows = {task.id: task for task in view.tasks}
    chk4 = rows["CHK-4"]

    assert chk4.tracker_status == "In Progress"
    assert (chk4.my_eta, chk4.my_eta_label) == (date(2026, 10, 12), "Oct 12")
    assert chk4.last_update is not None
    assert (chk4.last_update.state, chk4.last_update.note, chk4.last_update.via) == (
        WriteBackTarget.IN_REVIEW,
        "MR !3 open, waiting on Liam",
        "console",
    )
    assert chk4.last_update.at == _NOW
    [blocker] = view.blocker_details
    assert chk4.blocker_ids == (blocker.blocker_id,)
    assert chk4.can_move_in_tracker
    # The tracker's own fact gives the colour; the statements never repaint it.
    assert chk4.rag is Rag.RED
    assert chk4.source is StatusSource.INFERRED
    assert not rows["CHK-5"].can_move_in_tracker
    seed = rows["task-seed"]
    assert (seed.tracker_status, seed.can_move_in_tracker, seed.last_update) == (None, False, None)
    assert await _persona(store).focus_task(_TENANT, _DEV, "CHK-4", _TODAY) == chk4
    assert await _persona(store).focus_task(_TENANT, _DEV, "CHK-9", _TODAY) is None


async def test_statements_never_mask_the_trackers_colour() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    await _issue_fact(store, "CHK-4", _DEV)
    for source, kind in (
        (TASK_UPDATE_FACT_SOURCE, "task_update"),
        (CHECKIN_DRIFT_FACT_SOURCE, ETA_STATED),
    ):
        await store.append_fact(
            FactEvent(
                tenant_id=_TENANT,
                source=source,
                entity_ref=EntityRef(tenant_id=_TENANT, kind=NodeKind.TASK, id="CHK-4"),
                payload={"kind": kind, "status": "green", "source": "confirmed"},
                observed_at=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
                correlation_id=f"{source}-1",
            )
        )

    view = await _persona(store).focus(_TENANT, _DEV, _TODAY)

    chk4 = next(task for task in view.tasks if task.id == "CHK-4")
    assert (chk4.rag, chk4.source) == (Rag.RED, StatusSource.INFERRED)
    # With no synced issue fact in the window, the person's own assignment stands in.
    assert next(task for task in view.tasks if task.id == "CHK-5").can_move_in_tracker
