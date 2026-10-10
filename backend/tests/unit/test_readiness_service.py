"""The release readiness service: runs, drafts, a person's actions and the Create guardrails."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from config.settings import Settings
from core.application.release_readiness_service import (
    BoardView,
    FindingView,
    JiraData,
    ReadinessConflict,
    ReleaseReadinessService,
    Viewer,
)
from core.domain.auth import Role
from core.domain.errors import AuthorizationDenied, IssueCreateFailed
from core.domain.forecast import CommitmentScope, CommitmentScopeKind, DateChange
from core.domain.graph import Developer, EdgeKind, GraphEdge, Pod, Project, Task
from core.domain.identity import IdentityLink
from core.domain.integrations import Issue, IssueState, NewIssue
from core.domain.release_readiness import (
    AppliesTo,
    Matcher,
    MatcherKind,
    ReadinessError,
    ReadinessSettings,
    ReleaseCriterion,
    ScopeKind,
    ScopeRef,
    Severity,
    Strength,
    Suggestion,
    SuggestionStatus,
    UrgencyKind,
)
from infra.adapters.integrations.fake import FakeIssueTracker
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.in_memory_readiness import InMemoryReleaseReadinessRepository
from infra.registry import ServiceRegistry

TENANT = "demo"
TODAY = date(2026, 10, 5)  # a Monday
NOW = datetime(2026, 10, 5, 9, 45, tzinfo=UTC)
SECRET = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="
PROJECT = ScopeRef(kind=ScopeKind.PROJECT, id="checkout")

MANAGER = Viewer(subject="U-MGR", roles=frozenset({Role.MGR}), may_act=True)
OWNER = Viewer(subject="U-PO", roles=frozenset({Role.PO}), may_act=True)
OUTSIDER = Viewer(
    subject="U-SM2", roles=frozenset({Role.SM}), may_act=True, pods=frozenset(), project_reach=False
)


class _OnTheDay(ServiceRegistry):
    def _tenant_today(self) -> date:
        return TODAY


class _Tracker(FakeIssueTracker):
    """The sample tracker, counting creates, and refusing them when told to."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.issues = []
        self.created: list[NewIssue] = []
        self.refuse: IssueCreateFailed | None = None

    async def create_issue(self, tenant_id: str, issue: NewIssue) -> str:
        if self.refuse is not None:
            raise self.refuse
        self.created.append(issue)
        return await super().create_issue(tenant_id, issue)


class _World:
    def __init__(self) -> None:
        self.store = InMemoryGraphStore()
        self.registry = _OnTheDay(
            Settings(_env_file=None, secret_key=SECRET, runtime_mode="memory", tenant_id=TENANT),
            graph_store=self.store,
        )
        self.repository = InMemoryReleaseReadinessRepository()
        self.tracker = _Tracker(tenant_id=TENANT)
        self.writeback = True
        self.jira = JiraData(as_of=NOW)
        counter = iter(range(10_000))
        self.service = ReleaseReadinessService(
            repository=self.repository,
            graph_repository=self.store,
            delivery_service=self.registry.delivery_service(),
            forecast_service=self.registry.forecast_service(),
            issue_tracker=self.tracker,
            writeback_gate=self._writeback,
            jira_health=self._jira,
            pod_task_ids=self.registry._pod_task_ids,
            identity_link_repository=self.store,
            time_series_repository=self.store,
            console_base_url="https://console.example",
            clock=lambda: NOW,
            today=lambda: TODAY,
            new_id=lambda: f"{next(counter):04d}",
        )

    async def _writeback(self, tenant_id: str) -> bool:
        return self.writeback

    async def _jira(self, tenant_id: str) -> JiraData:
        return self.jira

    async def task(self, key: str, title: str, *, parent: str = "checkout", **meta: object) -> None:
        await self.store.upsert_node(
            Task(
                tenant_id=TENANT,
                id=key,
                name=title,
                metadata={"key": key, "updated_at": "2026-10-01T09:00:00+00:00", **meta},  # type: ignore[dict-item]
            )
        )
        await self.store.add_edge(_contains(parent, key))


def _contains(parent: str, child: str) -> GraphEdge:
    return GraphEdge(
        tenant_id=TENANT, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
    )


async def _world(*, delivery: date | None = date(2026, 11, 4)) -> _World:
    world = _World()
    store = world.store
    await store.upsert_node(
        Project(
            tenant_id=TENANT,
            id="checkout",
            name="Checkout Revamp",
            metadata={"jira_project_key": "CHK"},
        )
    )
    await store.upsert_node(Pod(tenant_id=TENANT, id="pod-pay", name="Payments Pod"))
    for member, name in (("U-PO", "Mina Patel"), ("U-MGR", "Asha Rao"), ("U-DEV", "Kai")):
        await store.upsert_node(Developer(tenant_id=TENANT, id=member, name=name))
    await store.add_edge(_contains("checkout", "pod-pay"))
    await store.add_edge(_contains("pod-pay", "U-DEV"))
    await world.task("CHK-9", "Capture retry runbook", status="In Progress", state="in_progress")
    await world.task("CHK-13", "Storefront performance test budget", status="To Do", state="todo")
    await world.task(
        "CHK-20", "Checkout flow", status="In QA", state="in_progress", issue_type="Story"
    )
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT, from_node_id="U-DEV", to_node_id="CHK-20", kind=EdgeKind.ASSIGNED_TO
        )
    )
    await store.upsert_identity_link(
        IdentityLink(tenant_id=TENANT, developer_id="U-PO", jira_account_id="acct-mina")
    )
    if delivery is not None:
        await world.registry.commitment_repository().append(
            DateChange(
                tenant_id=TENANT,
                scope=CommitmentScope(
                    kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout"
                ),
                target_date=delivery,
                changed_at=NOW,
                changed_by="U-PO",
            )
        )
    await world.repository.save_settings(ReadinessSettings(tenant_id=TENANT, enabled=True))
    for criterion in _criteria():
        await world.service.save_criterion(criterion, actor="U-ADMIN")
    return world


def _criteria() -> tuple[ReleaseCriterion, ...]:
    def criterion(
        name: str, *matchers: tuple[MatcherKind, str, Strength], **changes: object
    ) -> ReleaseCriterion:
        base = ReleaseCriterion(
            tenant_id=TENANT,
            criterion_id="",
            name=name,
            evidence=f"A {name.lower()} for the release.",
            applies_to=AppliesTo.RELEASE,
            matchers=tuple(Matcher(kind=k, value=v, strength=s) for k, v, s in matchers),
            lead_working_days=10,
        )
        return replace(base, **changes)  # type: ignore[arg-type]

    evidence, candidate = Strength.EVIDENCE, Strength.CANDIDATE
    return (
        criterion(
            "Security review",
            (MatcherKind.LABEL, "security-review", evidence),
            (MatcherKind.TITLE_PHRASE, "security review", evidence),
        ),
        criterion(
            "Load test",
            (MatcherKind.TITLE_PHRASE, "load test", evidence),
            (MatcherKind.TITLE_WORDS, "performance test", candidate),
        ),
        criterion(
            "Runbook and handover",
            (MatcherKind.TITLE_PHRASE, "runbook", evidence),
            applies_to=AppliesTo.PROJECT,
        ),
        criterion(
            "Accessibility check",
            (MatcherKind.LABEL, "accessibility", evidence),
            severity=Severity.ADVISORY,
        ),
        criterion(
            "On-call handover",
            (MatcherKind.TITLE_PHRASE, "on-call", evidence),
            applies_to=AppliesTo.POD,
        ),
    )


async def _board(world: _World, viewer: Viewer = MANAGER) -> BoardView:
    return await world.service.project_board(TENANT, "checkout", viewer)


def _row(board: BoardView, name: str) -> FindingView:
    return next(view for view in board.findings if view.criterion.name == name)


# --- Runs ----------------------------------------------------------------------------------


async def test_a_run_finds_covered_missing_and_unsure_and_drafts_each_gap_once() -> None:
    world = await _world()

    first = await world.service.run_project(TENANT, "checkout", MANAGER)
    board = await _board(world)

    assert (first.scopes, first.changed) == (2, 5)
    states = {view.criterion.name: view.shown_state for view in board.findings}
    assert states == {
        "Security review": "missing",
        "Load test": "unsure",
        "Runbook and handover": "covered",
        "Accessibility check": "missing",
        "On-call handover": "missing",
    }
    runbook = _row(board, "Runbook and handover")
    assert [(item.issue_key, item.how) for item in runbook.finding.evidence] == [
        ("CHK-9", 'says "runbook"')
    ]
    assert _row(board, "Load test").finding.reason == "Only the title's words match: CHK-13."
    security = _row(board, "Security review")
    assert security.finding.scope == PROJECT  # the project has no releases
    assert security.suggestion is not None
    assert security.suggestion.draft.summary == "Security review for Checkout Revamp"
    assert security.suggestion.draft.project_key == "CHK"
    assert _row(board, "On-call handover").scope_name == "Payments Pod"

    again = await world.service.run_project(TENANT, "checkout", MANAGER)
    drafts = [a for a in world.repository.actions if a.action == "drafted"]
    changes = [a for a in world.repository.actions if a.action == "state_changed"]

    assert again.changed == 0
    assert len(drafts) == 3  # security review, accessibility check, on-call handover
    assert len(changes) == 5


async def test_the_agent_off_refuses_run_now_and_a_scheduled_tick_records_off() -> None:
    world = await _world()
    await world.repository.save_settings(ReadinessSettings(tenant_id=TENANT, enabled=False))

    with pytest.raises(ReadinessConflict) as off:
        await world.service.run_project(TENANT, "checkout", MANAGER)
    tick = await world.service.run_tenant(TENANT, slot="2026-10-05T09:45:00+00:00")
    twice = await world.service.run_tenant(TENANT, slot="2026-10-05T09:45:00+00:00")

    assert off.value.code == "agent_off"
    assert tick is not None and tick.status.value == "off"
    assert twice is None


async def test_a_scheduled_tick_runs_every_scope_once_per_slot() -> None:
    world = await _world()

    tick = await world.service.run_tenant(TENANT, slot="2026-10-05T09:45:00+00:00")
    doubled = await world.service.run_tenant(TENANT, slot="2026-10-05T09:45:00+00:00")
    run = await world.repository.last_run(TENANT)

    assert tick is not None and (tick.status.value, tick.scopes, tick.changed) == ("ok", 2, 5)
    assert doubled is None
    assert run is not None and run.counts["missing"] == 3


async def test_a_tenant_that_never_set_readiness_up_records_nothing() -> None:
    world = _World()

    assert await world.service.run_tenant(TENANT, slot="s") is None
    assert await world.repository.last_run(TENANT) is None


async def test_the_due_day_is_the_delivery_date_less_the_lead() -> None:
    world = await _world(delivery=date(2026, 11, 4))
    await world.service.run_project(TENANT, "checkout", MANAGER)

    security = _row(await _board(world), "Security review")

    assert security.finding.urgency.due_on == date(2026, 10, 21)
    assert security.finding.urgency.kind is UrgencyKind.LATER
    assert security.working_days_left == 12


async def test_a_criterion_that_applies_only_where_a_label_is_waits_for_it() -> None:
    world = await _world()
    privacy = replace(
        _criteria()[2],
        name="Data-protection impact assessment",
        matchers=(
            Matcher(kind=MatcherKind.TITLE_PHRASE, value="dpia", strength=Strength.EVIDENCE),
        ),
        when_labels=("personal-data",),
    )
    await world.service.save_criterion(privacy, actor="U-ADMIN")
    await world.service.run_project(TENANT, "checkout", MANAGER)
    assert "Data-protection impact assessment" not in {
        view.criterion.name for view in (await _board(world)).findings
    }

    await world.task("CHK-30", "Store the customer's address", labels="personal-data")
    await world.service.run_project(TENANT, "checkout", MANAGER)

    assert _row(await _board(world), "Data-protection impact assessment").shown_state == "missing"


# --- Stale data ----------------------------------------------------------------------------


async def test_stale_data_never_turns_covered_missing_and_holds_a_new_blocking_gap() -> None:
    world = await _world()
    await world.service.run_project(TENANT, "checkout", MANAGER)
    world.jira = JiraData(stale=True, as_of=NOW)
    await world.store.upsert_node(
        Task(tenant_id=TENANT, id="CHK-9", name="Retry notes", metadata={"key": "CHK-9"})
    )
    await world.service.save_criterion(
        replace(_criteria()[1], criterion_id="", name="Change approval"), actor="U-ADMIN"
    )

    await world.service.run_project(TENANT, "checkout", MANAGER)
    board = await _board(world)

    assert _row(board, "Runbook and handover").shown_state == "covered"
    assert _row(board, "Change approval").finding.held is True
    assert _row(board, "Security review").finding.held is False


# --- A person's actions --------------------------------------------------------------------


async def test_a_dismissed_draft_stays_dismissed_across_runs_and_edits_until_reopened() -> None:
    world = await _world()
    await world.service.run_project(TENANT, "checkout", MANAGER)
    security = _row(await _board(world), "Security review")
    assert security.suggestion is not None

    dismissed = await world.service.dismiss(
        TENANT, security.suggestion.suggestion_id, OWNER, reason="Done by the platform team"
    )
    criteria = await world.repository.list_criteria(TENANT)
    edited = next(item for item in criteria if item.name == "Security review")
    await world.service.save_criterion(replace(edited, lead_working_days=5), actor="U-ADMIN")
    await world.service.run_project(TENANT, "checkout", MANAGER)
    after = _row(await _board(world), "Security review")

    assert dismissed.suggestion is not None
    assert dismissed.suggestion.status is SuggestionStatus.DISMISSED
    assert after.suggestion is not None and after.suggestion.status is SuggestionStatus.DISMISSED
    assert after.shown_state == "missing"
    assert after.actions.reopen and not after.actions.create

    reopened = await world.service.reopen(TENANT, after.finding.finding_id, OWNER)

    assert reopened.suggestion is not None
    assert reopened.suggestion.status is SuggestionStatus.OPEN


async def test_link_not_applicable_and_reopen() -> None:
    world = await _world()
    await world.service.run_project(TENANT, "checkout", MANAGER)
    load = _row(await _board(world), "Load test")
    access = _row(await _board(world), "Accessibility check")
    security = _row(await _board(world), "Security review")

    linked = await world.service.link(TENANT, load.finding.finding_id, OWNER, issue_key="chk-13")
    with pytest.raises(ReadinessError, match="not among Jira's synced issues"):
        await world.service.link(TENANT, access.finding.finding_id, OWNER, issue_key="CHK-404")
    with pytest.raises(AuthorizationDenied, match="manager or an admin"):
        await world.service.not_applicable(
            TENANT, security.finding.finding_id, OWNER, reason="Internal API only"
        )
    waived = await world.service.not_applicable(
        TENANT, security.finding.finding_id, MANAGER, reason="Internal API only"
    )
    advisory = await world.service.not_applicable(
        TENANT, access.finding.finding_id, OWNER, reason="No user interface"
    )
    with pytest.raises(AuthorizationDenied):
        await world.service.link(TENANT, access.finding.finding_id, OUTSIDER, issue_key="CHK-13")
    reopened = await world.service.reopen(TENANT, security.finding.finding_id, MANAGER)

    assert (linked.shown_state, linked.finding.decided_by.value) == ("covered", "person")
    assert linked.finding.evidence[0].issue_key == "CHK-13"
    assert waived.shown_state == "not_applicable"
    assert advisory.shown_state == "not_applicable"
    assert reopened.shown_state == "missing"
    actions = [a.action for a in world.repository.actions if a.actor != "agent"]
    assert {"linked", "not_applicable", "reopened"} <= set(actions)


async def test_a_record_outside_jira_covers_it() -> None:
    world = await _world()
    await world.service.run_project(TENANT, "checkout", MANAGER)
    security = _row(await _board(world), "Security review")

    linked = await world.service.link(
        TENANT,
        security.finding.finding_id,
        OWNER,
        url="https://records.example/reviews/7",
        note="Signed off",
    )

    assert (linked.shown_state, linked.finding.done) == ("covered", True)
    assert linked.finding.evidence[0].url == "https://records.example/reviews/7"


async def test_a_draft_edit_needs_its_version() -> None:
    world = await _world()
    await world.service.run_project(TENANT, "checkout", MANAGER)
    suggestion = _row(await _board(world), "Security review").suggestion
    assert suggestion is not None

    edited = await world.service.edit_draft(
        TENANT,
        suggestion.suggestion_id,
        OWNER,
        version=suggestion.version,
        draft=replace(suggestion.draft, summary="Security review: checkout payments"),
    )
    with pytest.raises(ReadinessConflict) as stale:
        await world.service.edit_draft(
            TENANT,
            suggestion.suggestion_id,
            OWNER,
            version=suggestion.version,
            draft=suggestion.draft,
        )

    assert edited.suggestion is not None
    assert (edited.suggestion.version, edited.suggestion.draft.summary) == (
        2,
        "Security review: checkout payments",
    )
    assert stale.value.code == "stale_version"


# --- Create in Jira ------------------------------------------------------------------------


async def _open_draft(world: _World) -> Suggestion:
    await world.repository.save_settings(
        ReadinessSettings(tenant_id=TENANT, enabled=True, create_in_jira=True)
    )
    await world.service.run_project(TENANT, "checkout", MANAGER)
    view = _row(await _board(world), "Security review")
    assert view.suggestion is not None
    return view.suggestion


async def test_create_needs_both_switches() -> None:
    world = await _world()
    await world.service.run_project(TENANT, "checkout", MANAGER)
    off = _row(await _board(world), "Security review")
    assert off.suggestion is not None
    assert (
        off.actions.create_off_reason == "Creating issues from OpenProgram is off for this tenant."
    )

    with pytest.raises(ReadinessConflict, match="off for this tenant"):
        await world.service.create(TENANT, off.suggestion.suggestion_id, OWNER, version=1)
    suggestion = await _open_draft(world)
    world.writeback = False
    with pytest.raises(ReadinessConflict, match="needs Jira write-back on"):
        await world.service.create(TENANT, suggestion.suggestion_id, OWNER, version=1)

    assert world.tracker.created == []


async def test_create_posts_once_with_the_marker_and_the_approver_and_covers_the_gap() -> None:
    world = await _world()
    suggestion = await _open_draft(world)

    with pytest.raises(ReadinessConflict) as stale:
        await world.service.create(TENANT, suggestion.suggestion_id, OWNER, version=7)
    result = await world.service.create(TENANT, suggestion.suggestion_id, OWNER, version=1)
    again = await world.service.create(TENANT, suggestion.suggestion_id, OWNER, version=1)
    await world.service.run_project(TENANT, "checkout", MANAGER)
    row = _row(await _board(world), "Security review")

    assert stale.value.code == "stale_version"
    [posted] = world.tracker.created
    assert posted.project_key == "CHK"
    assert posted.reporter_account_id == "acct-mina"
    assert posted.labels[-1] == suggestion.marker_label
    assert "Approved and created in OpenProgram by Mina Patel." in posted.description
    assert "https://console.example/reports/checkout/overall#readiness" in posted.description
    assert (result.issue_key, result.created) == ("CHK-901", True)
    assert (again.issue_key, again.created) == ("CHK-901", False)
    assert row.shown_state == "covered"
    assert row.finding.evidence[0].how == "created"
    assert row.suggestion is not None and row.suggestion.status is SuggestionStatus.CREATED
    audit = [
        a.action for a in world.repository.actions if a.suggestion_id == suggestion.suggestion_id
    ]
    assert audit[-2:] == ["create_requested", "created"]


async def test_an_issue_an_earlier_try_made_is_adopted_by_its_marker_and_nothing_is_posted() -> (
    None
):
    world = await _world()
    suggestion = await _open_draft(world)
    world.tracker.issues.append(
        Issue(
            tenant_id=TENANT,
            key="CHK-77",
            title="Security review for Checkout Revamp",
            state=IssueState.TODO,
            metadata={
                "project_key": "CHK",
                "labels": f"release-readiness, {suggestion.marker_label}",
            },
        )
    )

    result = await world.service.create(TENANT, suggestion.suggestion_id, OWNER, version=1)

    assert (result.issue_key, result.created) == ("CHK-77", False)
    # Recorded as the sync would, so the adopted issue covers it at once.
    assert result.finding.shown_state == "covered"
    assert result.finding.finding.evidence[0].issue_key == "CHK-77"
    assert world.tracker.created == []
    assert any(a.action == "create_adopted" for a in world.repository.actions)


async def test_a_criterion_covered_since_the_draft_is_refused_and_says_what_covers_it() -> None:
    world = await _world()
    suggestion = await _open_draft(world)
    await world.task("CHK-33", "Release security review", status="To Do")

    with pytest.raises(ReadinessConflict) as covered:
        await world.service.create(TENANT, suggestion.suggestion_id, OWNER, version=1)

    assert covered.value.code == "not_missing"
    assert str(covered.value) == "CHK-33 now covers it; link it instead."
    assert world.tracker.created == []


async def test_a_refused_create_keeps_the_draft_open_and_is_audited() -> None:
    world = await _world()
    suggestion = await _open_draft(world)
    world.tracker.refuse = IssueCreateFailed("refused", ("components",))

    with pytest.raises(IssueCreateFailed):
        await world.service.create(TENANT, suggestion.suggestion_id, OWNER, version=1)
    kept = await world.repository.get_suggestion(TENANT, suggestion.suggestion_id)

    assert kept is not None and kept.status is SuggestionStatus.OPEN
    failed = [a for a in world.repository.actions if a.action == "create_failed"]
    assert failed and failed[0].after == {"category": "refused", "fields": ["components"]}


async def test_only_one_create_is_in_flight() -> None:
    world = await _world()
    suggestion = await _open_draft(world)
    assert await world.repository.claim_suggestion(
        TENANT, suggestion.suggestion_id, version=1, at=NOW
    )

    with pytest.raises(ReadinessConflict) as busy:
        await world.service.create(TENANT, suggestion.suggestion_id, OWNER, version=1)

    assert busy.value.code == "creating"


async def test_a_created_issue_closed_as_wont_do_reads_missing_and_is_not_drafted_again() -> None:
    world = await _world()
    suggestion = await _open_draft(world)
    result = await world.service.create(TENANT, suggestion.suggestion_id, OWNER, version=1)
    await world.store.upsert_node(
        Task(
            tenant_id=TENANT,
            id=result.issue_key,
            name="Security review for Checkout Revamp",
            metadata={"key": result.issue_key, "status": "Won't Do", "state": "done"},
        )
    )

    await world.service.run_project(TENANT, "checkout", MANAGER)
    row = _row(await _board(world), "Security review")

    assert row.shown_state == "missing"
    assert row.finding.reason == f"{result.issue_key} was closed as Won't Do."
    assert len([a for a in world.repository.actions if a.action == "drafted"]) == 3


# --- The day report ------------------------------------------------------------------------


async def test_the_day_report_raises_only_blocking_urgent_gaps_held_back_on_stale_data() -> None:
    world = await _world(delivery=date(2026, 10, 23))
    await world.service.run_project(TENANT, "checkout", MANAGER)

    gaps = await world.service.report_gaps(TENANT, "checkout", None)

    # Due 9 Oct, ten working days before 23 Oct: four working days from Monday 5 Oct.
    assert gaps.lines == (
        "Checkout Revamp needs a load test within 4 working days, and Jira has only a possible "
        "match: CHK-13.",
        "Checkout Revamp needs a security review within 4 working days, and none is in Jira.",
    )
    assert [gap.ask for gap in gaps.gaps] == [
        "Checkout Revamp: is CHK-13 the load test (due 9 Oct)? Link it, or create one",
        "Checkout Revamp: no security review in Jira yet (due 9 Oct); create it or link one",
    ]
    # The advisory accessibility check and the pod's undated handover are not raised.
    assert len(gaps.gaps) == 2
    await world.repository.save_settings(ReadinessSettings(tenant_id=TENANT, enabled=False))
    assert (await world.service.report_gaps(TENANT, "checkout", None)).gaps == ()


async def test_more_than_three_gaps_are_counted_after_the_third() -> None:
    world = await _world(delivery=date(2026, 10, 23))
    for name in ("Change approval", "Release notes", "Rollback plan"):
        await world.service.save_criterion(
            replace(
                _criteria()[0],
                name=name,
                matchers=(
                    Matcher(
                        kind=MatcherKind.LABEL,
                        value=name.lower().replace(" ", "-"),
                        strength=Strength.EVIDENCE,
                    ),
                ),
            ),
            actor="U-ADMIN",
        )
    await world.service.run_project(TENANT, "checkout", MANAGER)

    lines = (await world.service.report_gaps(TENANT, "checkout", None)).lines

    assert len(lines) == 4
    assert lines[-1] == "and 2 more in Release readiness."


async def test_a_scrum_master_sees_only_their_pods_rows_and_an_executive_no_drafts() -> None:
    world = await _world()
    await world.service.run_project(TENANT, "checkout", MANAGER)
    scrum_master = Viewer(
        subject="U-SM", roles=frozenset({Role.SM}), may_act=True, pods=frozenset({"pod-other"})
    )
    executive = Viewer(
        subject="U-EX",
        roles=frozenset({Role.EXEC}),
        may_act=False,
        pods=frozenset(),
        project_reach=False,
        sees_drafts=False,
    )

    theirs = await _board(world, scrum_master)
    read_only = await _board(world, executive)

    assert "On-call handover" not in {view.criterion.name for view in theirs.findings}
    assert all(view.actions.link for view in theirs.findings if view.shown_state != "covered")
    assert all(view.suggestion is None for view in read_only.findings)
    assert not any(view.actions.link for view in read_only.findings)
    assert "On-call handover" not in {view.criterion.name for view in read_only.findings}
