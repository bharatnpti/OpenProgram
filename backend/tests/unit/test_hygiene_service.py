"""The Jira hygiene service: runs, owners, the staleness gate, a person's actions, the board,
"Try on a project", the day report's count line and asks, and their climb up the matrix."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from config.settings import Settings
from core.application.hygiene_service import HygieneConflict, HygieneService
from core.application.profile_service import ProfileService
from core.application.release_readiness_service import JiraData, Viewer
from core.domain.auth import Role
from core.domain.baseline import BaselineItem, BaselineKind, BaselineScopeKind, PlanningBaseline
from core.domain.delivery import DeliveryStage
from core.domain.errors import AuthorizationDenied
from core.domain.gates import GateItem, ItemSource, ItemStatus
from core.domain.graph import Developer, EdgeKind, GraphEdge, Pod, Project, Task
from core.domain.hygiene import (
    Condition,
    ConditionKind,
    HygieneRule,
    HygieneSettings,
    HygieneState,
    IssueField,
    Owner,
    OwnerKind,
    Resolution,
    RuleNeed,
    RuleScope,
    RuleSeverity,
    Select,
    StageFilter,
)
from core.domain.profiles import ProfileSource
from core.domain.release_readiness import RunStatus, ScopeKind, ScopeRef
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.in_memory_hygiene import InMemoryHygieneRepository
from infra.registry import ServiceRegistry

TENANT = "demo"
TODAY = date(2026, 10, 14)  # a Wednesday
NOW = datetime(2026, 10, 14, 9, 45, tzinfo=UTC)
SECRET = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="
PROJECT = ScopeRef(kind=ScopeKind.PROJECT, id="checkout")

ADMIN = Viewer(subject="U-ADMIN", roles=frozenset({Role.ADMIN}), may_act=True)
MANAGER = Viewer(subject="U-MGR", roles=frozenset({Role.MGR}), may_act=True)
SCRUM_MASTER = Viewer(
    subject="U-SM",
    roles=frozenset({Role.SM}),
    may_act=True,
    pods=frozenset({"pod-pay"}),
    project_reach=True,
)
EXECUTIVE = Viewer(
    subject="U-EX",
    roles=frozenset({Role.EXEC}),
    may_act=False,
    pods=frozenset(),
    project_reach=False,
    sees_drafts=False,
)


class _OnTheDay(ServiceRegistry):
    fixed_hygiene: HygieneService | None = None

    def _tenant_today(self) -> date:
        return TODAY

    def hygiene_service(self) -> HygieneService:
        assert self.fixed_hygiene is not None
        return self.fixed_hygiene


class _World:
    def __init__(self) -> None:
        self.store = InMemoryGraphStore()
        self.registry = _OnTheDay(
            Settings(_env_file=None, secret_key=SECRET, runtime_mode="memory", tenant_id=TENANT),
            graph_store=self.store,
        )
        self.repository = InMemoryHygieneRepository()
        self.jira = JiraData(as_of=NOW)
        self.now = NOW
        counter = iter(range(10_000))
        self.profiles = ProfileService(
            repository=self.registry.profile_repository(),
            graph_repository=self.store,
            directory_repository=self.registry.directory_user_repository(),
            time_series_repository=self.store,
            runs_pod=self.registry.forecast_service().runs_pod,
            today=lambda: TODAY,
        )
        self.service = HygieneService(
            repository=self.repository,
            readiness=self.registry.release_readiness_service(),
            graph_repository=self.store,
            time_series_repository=self.store,
            forecast_service=self.registry.forecast_service(),
            jira_health=self._jira,
            baselines=self.registry.baseline_service(),
            profiles=self.profiles,
            escalation=self.registry.escalation_matrix_service(),
            gates=self.registry.gate_service(),
            clock=lambda: self.now,
            today=lambda: TODAY,
            new_id=lambda: f"{next(counter):04d}",
        )
        self.registry.fixed_hygiene = self.service

    async def _jira(self, tenant_id: str) -> JiraData:
        return self.jira

    async def task(self, key: str, title: str, **meta: object) -> None:
        metadata = {
            "key": key,
            "updated_at": "2026-10-01T09:00:00+00:00",
            "issue_type": "Story",
            "status": "To Do",
            "state": "todo",
            **meta,
        }
        await self.store.upsert_node(
            Task(tenant_id=TENANT, id=key, name=title, metadata=metadata)  # type: ignore[arg-type]
        )
        await self.store.add_edge(_edge("checkout", key, EdgeKind.CONTAINS))

    async def assign(self, member: str, key: str) -> None:
        await self.store.add_edge(_edge(member, key, EdgeKind.ASSIGNED_TO))

    async def rule(self, rule: HygieneRule) -> HygieneRule:
        return await self.service.save_rule(rule, actor="U-ADMIN")

    async def run(self, slot: str = "2026-10-14T09:45:00+00:00") -> None:
        summary = await self.service.run_tenant(TENANT, slot=slot)
        assert summary is not None and summary.status is RunStatus.OK

    async def open_findings(self) -> list[tuple[str, str, str | None]]:
        return sorted(
            (item.rule_id, item.issue_key, item.owner_member_id)
            for item in await self.repository.list_findings(TENANT, states=(HygieneState.OPEN,))
        )


def _edge(source: str, target: str, kind: EdgeKind) -> GraphEdge:
    return GraphEdge(tenant_id=TENANT, from_node_id=source, to_node_id=target, kind=kind)


def _rule(rule_id: str, condition: Condition, **changes: object) -> HygieneRule:
    base = HygieneRule(
        tenant_id=TENANT,
        rule_id="",
        name=rule_id.replace("-", " ").capitalize(),
        scope=RuleScope.PROJECT,
        condition=condition,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


CHANGE_LABEL = _rule(
    "added-after-planning",
    Condition(kind=ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL),
    owner=Owner(kind=OwnerKind.DECISION_OWNER),
    need=RuleNeed.DECISION,
)
NO_POINTS = _rule(
    "no-points",
    Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.STORY_POINTS),
    owner=Owner(kind=OwnerKind.ASSIGNEE),
)


async def _world() -> _World:
    world = _World()
    store = world.store
    await store.upsert_node(
        Project(
            tenant_id=TENANT,
            id="checkout",
            name="Checkout Revamp",
            metadata={"jira_project_key": "CHK", "owner_id": "U-PO"},
        )
    )
    await store.upsert_node(
        Pod(
            tenant_id=TENANT,
            id="pod-pay",
            name="Payments Pod",
            metadata={
                "escalation_sm_chat_external_id": "C-SM",
                "escalation_sm_display_name": "Ira Novak",
                "escalation_sm_member_id": "U-SM",
                "escalation_manager_chat_external_id": "C-MGR",
                "escalation_manager_display_name": "Asha Rao",
                "escalation_manager_member_id": "U-MGR",
            },
        )
    )
    for member, name, roles in (
        ("U-PO", "Mina Patel", "po"),
        ("U-MGR", "Asha Rao", "mgr"),
        ("U-SM", "Ira Novak", "sm"),
        ("U-DEV", "Kai Lund", "dev"),
        ("U-QA", "Sofia Berg", "dev"),
    ):
        await store.upsert_node(
            Developer(tenant_id=TENANT, id=member, name=name, metadata={"app_roles": roles})
        )
    await store.add_edge(_edge("checkout", "pod-pay", EdgeKind.CONTAINS))
    for member in ("U-DEV", "U-QA", "U-SM"):
        await store.add_edge(_edge("pod-pay", member, EdgeKind.CONTAINS))
    await world.task("CHK-1", "Saved cards", status="In Progress", state="in_progress")
    await world.task("CHK-2", "Card art", story_points=3)
    await world.task(
        "CHK-20", "Wallet top-up", created_at="2026-10-08T10:00:00+00:00", story_points=2
    )
    await world.task(
        "CHK-21",
        "Gift cards",
        created_at="2026-10-09T10:00:00+00:00",
        labels="CR",
        story_points=1,
    )
    await world.assign("U-DEV", "CHK-1")
    await world.profiles.set_profile(
        TENANT, "pod-pay", "U-QA", "qa", source=ProfileSource.ADMIN, by="U-ADMIN"
    )
    await world.repository.save_settings(HygieneSettings(tenant_id=TENANT, enabled=True))
    return world


async def _planned(world: _World, *keys: str) -> None:
    """Planning complete on Thu 1 Oct with these requirements, grace 2 working days."""
    await world.registry.planning_baseline_repository().add(
        PlanningBaseline(
            tenant_id=TENANT,
            baseline_id="b1",
            scope_kind=BaselineScopeKind.PROJECT,
            scope_id="checkout",
            project_id="checkout",
            kind=BaselineKind.DATE,
            planned_on=date(2026, 10, 1),
            items={key: BaselineItem(stage=DeliveryStage.RAISED) for key in keys},
            reconstructed=False,
            snapshot_day=None,
            change_label="CR",
            grace_working_days=2,
            set_by="U-PO",
            set_at=NOW - timedelta(days=13),
        )
    )


# ---- runs ---------------------------------------------------------------------------------


async def test_a_tenant_that_never_set_hygiene_up_records_nothing() -> None:
    world = _World()

    assert await world.service.run_tenant(TENANT, slot="s") is None
    assert await world.repository.last_run(TENANT) is None


async def test_off_records_an_off_run_and_a_slot_runs_once() -> None:
    world = await _world()
    await world.repository.save_settings(HygieneSettings(tenant_id=TENANT, enabled=False))

    first = await world.service.run_tenant(TENANT, slot="s1")
    again = await world.service.run_tenant(TENANT, slot="s1")

    assert first is not None and first.status is RunStatus.OFF
    assert again is None


async def test_the_change_label_rule_finds_additions_after_planning_and_names_the_decider() -> None:
    world = await _world()
    await _planned(world, "CHK-1", "CHK-2")
    saved = await world.rule(CHANGE_LABEL)

    await world.run()

    assert await world.open_findings() == [(saved.rule_id, "CHK-20", "U-PO")]
    finding = (await world.repository.list_findings(TENANT))[0]
    assert (
        finding.what == "Joined the scope on 8 Oct, after planning on 1 Oct, without the CR label"
    )
    assert finding.detail["owner_via"] == "decision_owner"
    assert finding.waits_from == date(2026, 10, 8)
    # No Jira text: keys, field names, stages and dates only.
    assert "Wallet" not in str(finding.detail)


async def test_an_unchanged_run_writes_nothing_and_a_fix_closes_it() -> None:
    world = await _world()
    await _planned(world, "CHK-1", "CHK-2")
    await world.rule(CHANGE_LABEL)
    await world.run("s1")
    audit = len(world.repository.actions)

    await world.run("s2")
    assert len(world.repository.actions) == audit

    await world.task(
        "CHK-20", "Wallet top-up", created_at="2026-10-08T10:00:00+00:00", labels="CR, web"
    )
    await world.run("s3")

    closed = (await world.repository.list_findings(TENANT))[0]
    assert closed.state is HygieneState.RESOLVED
    assert closed.resolution is Resolution.FIXED
    assert [item.action for item in world.repository.actions][-1] == "resolved"


async def test_stale_jira_never_closes_a_finding_and_holds_a_new_ask() -> None:
    world = await _world()
    await world.rule(NO_POINTS)
    await world.rule(
        _rule(
            "no-due-date",
            Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.DUE_DATE),
            severity=RuleSeverity.ASK,
        )
    )
    await world.run("s1")
    assert ("no-points", "CHK-1", "U-DEV") in await world.open_findings()

    world.jira = JiraData(stale=True, as_of=NOW - timedelta(days=2))
    await world.task(
        "CHK-1", "Saved cards", status="In Progress", state="in_progress", story_points=5
    )
    await world.task("CHK-30", "Refunds")
    await world.run("s2")

    findings = await world.repository.list_findings(TENANT, states=(HygieneState.OPEN,))
    kept = next(
        item for item in findings if (item.rule_id, item.issue_key) == ("no-points", "CHK-1")
    )
    new_ask = next(
        item for item in findings if (item.rule_id, item.issue_key) == ("no-due-date", "CHK-30")
    )
    new_list = next(
        item for item in findings if (item.rule_id, item.issue_key) == ("no-points", "CHK-30")
    )
    assert kept.state is HygieneState.OPEN
    assert new_ask.held is True
    assert new_list.held is False

    world.jira = JiraData(as_of=NOW)
    await world.run("s3")
    by_key = {
        (item.rule_id, item.issue_key): item
        for item in await world.repository.list_findings(TENANT)
    }
    assert by_key[("no-points", "CHK-1")].state is HygieneState.RESOLVED
    assert by_key[("no-due-date", "CHK-30")].held is False


async def test_an_issue_that_left_the_scope_closes_as_left_the_scope() -> None:
    world = await _world()
    await world.rule(NO_POINTS)
    await world.run("s1")
    await world.store.delete_node(TENANT, "CHK-1", on=TODAY)

    await world.run("s2")

    closed = next(
        item for item in await world.repository.list_findings(TENANT) if item.issue_key == "CHK-1"
    )
    assert closed.resolution is Resolution.LEFT_SCOPE


async def test_owners_follow_the_rule_then_the_pods_scrum_master_then_the_decider() -> None:
    world = await _world()
    qa = await world.rule(
        _rule(
            "bugs-no-severity",
            Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.SEVERITY),
            owner=Owner(kind=OwnerKind.PROFILE, profile="qa"),
            select=Select(every_type=True),
        )
    )
    points = await world.rule(NO_POINTS)

    await world.run()

    found = await world.open_findings()
    # The pod's QA profile holder for every issue; the assignee where there is one, else
    # the pod's scrum master.
    assert (qa.rule_id, "CHK-1", "U-QA") in found
    assert (points.rule_id, "CHK-1", "U-DEV") in found
    finding = next(
        item
        for item in await world.repository.list_findings(TENANT)
        if item.rule_id == qa.rule_id and item.issue_key == "CHK-1"
    )
    assert finding.detail["owner_profile"] == "qa"
    assert finding.detail["owner_pod_id"] == "pod-pay"


async def test_an_unassigned_issue_falls_back_to_the_scrum_master() -> None:
    world = await _world()
    rule = await world.rule(
        _rule(
            "past-due",
            Condition(kind=ConditionKind.PAST_DUE_OPEN),
            owner=Owner(kind=OwnerKind.ASSIGNEE),
        )
    )
    await world.task("CHK-2", "Card art", story_points=3, due_date="2026-10-05")

    await world.run()

    assert (rule.rule_id, "CHK-2", "U-SM") in await world.open_findings()


async def test_the_acceptance_criteria_rule_reads_the_gates_scan() -> None:
    world = await _world()
    _templates, items, _questions, scans = world.registry.gate_repositories()
    for key in ("CHK-1", "CHK-2"):
        await scans.record(TENANT, key, NOW, NOW)
    await items.save(
        GateItem(
            tenant_id=TENANT,
            item_id="g1",
            issue_key="CHK-2",
            template_id="t",
            kind="acceptance",
            text="A shopper can save a card",
            status=ItemStatus.SUGGESTED,
            source=ItemSource.DESCRIPTION,
            source_ref="description",
            created_at=NOW,
            updated_at=NOW,
            created_by="scan",
        )
    )
    rule = await world.rule(
        _rule(
            "no-acceptance",
            Condition(kind=ConditionKind.MISSING_GATE_KIND, gate_kind="acceptance"),
            owner=Owner(kind=OwnerKind.PROFILE, profile="product"),
        )
    )

    await world.run()

    # CHK-2 has a suggested item; CHK-20 and CHK-21 were never read: neither breaks it.
    assert [
        key for rule_id, key, _owner in await world.open_findings() if rule_id == rule.rule_id
    ] == ["CHK-1"]


# ---- a person's actions -------------------------------------------------------------------


async def test_waive_stands_across_runs_and_reopen_judges_again() -> None:
    world = await _world()
    rule = await world.rule(NO_POINTS)
    await world.run("s1")
    finding = (await world.repository.list_findings(TENANT))[0]

    with pytest.raises(AuthorizationDenied):
        await world.service.waive(TENANT, finding.finding_id, EXECUTIVE, reason="Spike work")
    waived = await world.service.waive(
        TENANT, finding.finding_id, SCRUM_MASTER, reason="Spike work"
    )
    await world.run("s2")

    assert waived.state is HygieneState.WAIVED
    assert await world.open_findings() == []
    with pytest.raises(HygieneConflict):
        await world.service.waive(TENANT, finding.finding_id, MANAGER, reason="again")

    reopened = await world.service.reopen(TENANT, finding.finding_id, MANAGER)
    assert reopened.state is HygieneState.OPEN
    assert reopened.decision is None
    assert [
        item.action for item in world.repository.actions if item.finding_id == finding.finding_id
    ] == [
        "opened",
        "waived",
        "reopened",
        "changed",
    ]
    assert rule.rule_id == finding.rule_id


async def test_not_applicable_on_a_blocking_rule_is_a_managers_and_recheck_reads_a_fix() -> None:
    world = await _world()
    await world.rule(replace(NO_POINTS, blocking_before=DeliveryStage.IN_TESTING))
    await world.run("s1")
    finding = (await world.repository.list_findings(TENANT))[0]

    with pytest.raises(AuthorizationDenied):
        await world.service.not_applicable(TENANT, finding.finding_id, SCRUM_MASTER, reason="n/a")
    await world.task(
        "CHK-1", "Saved cards", status="In Progress", state="in_progress", story_points=5
    )
    rechecked = await world.service.recheck(TENANT, finding.finding_id, SCRUM_MASTER)

    assert rechecked.state is HygieneState.RESOLVED
    assert rechecked.resolution is Resolution.FIXED


# ---- the board and Try on a project -------------------------------------------------------


async def test_the_board_counts_per_rule_and_an_executive_reads_counts_only() -> None:
    world = await _world()
    await _planned(world, "CHK-1", "CHK-2")
    await world.rule(CHANGE_LABEL)
    await world.rule(NO_POINTS)
    await world.run()

    board = await world.service.project_board(TENANT, "checkout", MANAGER)
    counts = await world.service.project_board(TENANT, "checkout", EXECUTIVE)

    rows = {row.rule.name: (row.open, row.new_today, row.oldest_days) for row in board.rules}
    assert rows == {
        "Added after planning": (1, 1, 0),
        "No points": (1, 1, 0),
    }
    assert {(view.finding.issue_key, view.owner_name) for view in board.findings} == {
        ("CHK-20", "Mina Patel"),
        ("CHK-1", "Kai Lund"),
    }
    added = next(view for view in board.findings if view.finding.issue_key == "CHK-20")
    assert added.title == "Wallet top-up"
    assert added.suggested_fix == (
        "Label CHK-20 CR if it was agreed as a change, or take it out of Checkout Revamp."
    )
    assert added.actions.waive and added.actions.recheck
    assert counts.counts_only and counts.findings == ()
    assert [row.open for row in counts.rules] == [1, 1]
    assert not counts.can_run and board.can_run


async def test_try_on_a_project_counts_without_saving_and_says_when_a_baseline_is_missing() -> None:
    world = await _world()

    points = await world.service.preview(NO_POINTS, "checkout")
    added = await world.service.preview(CHANGE_LABEL, "checkout")

    project = next(row for row in points if row.scope == PROJECT)
    assert (project.looked_at, project.found) == (4, 1)
    assert [(item.key, item.owner_name) for item in project.issues] == [("CHK-1", "Kai Lund")]
    assert next(row for row in added if row.scope == PROJECT).needs_baseline is True
    assert await world.repository.list_findings(TENANT) == []
    assert world.repository.actions == []


async def test_check_now_needs_the_scan_on_and_a_reach_over_the_project() -> None:
    world = await _world()
    await world.rule(NO_POINTS)

    summary = await world.service.run_project(TENANT, "checkout", MANAGER)
    with pytest.raises(AuthorizationDenied):
        await world.service.run_project(TENANT, "checkout", EXECUTIVE)
    await world.repository.save_settings(HygieneSettings(tenant_id=TENANT, enabled=False))
    with pytest.raises(HygieneConflict):
        await world.service.run_project(TENANT, "checkout", MANAGER)

    assert summary.opened == 1 and summary.open == 1


# ---- the day report -----------------------------------------------------------------------


async def test_the_report_counts_everything_and_asks_only_for_asking_rules() -> None:
    world = await _world()
    await _planned(world, "CHK-1", "CHK-2")
    await world.rule(replace(CHANGE_LABEL, severity=RuleSeverity.ASK))
    await world.rule(NO_POINTS)
    await world.task(
        "CHK-22", "Split payments", created_at="2026-10-09T10:00:00+00:00", story_points=1
    )
    await world.run()

    report = await world.service.report(TENANT, "checkout", None, TODAY)

    assert report.line == "Jira hygiene: 3 open (3 new today)."
    assert [(ask.owner_member_id, ask.keys) for ask in report.asks] == [
        ("U-PO", ("CHK-20", "CHK-22"))
    ]
    assert report.asks[0].text == (
        "Checkout Revamp: label 2 additions after planning as changes or take them out: "
        "CHK-20, CHK-22"
    )
    assert report.asks[0].waits_from == date(2026, 10, 8)


async def test_the_day_report_says_the_count_line_and_the_ask_climbs_the_matrix() -> None:
    world = await _world()
    await world.rule(
        _rule(
            "no-due-date",
            Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.DUE_DATE),
            owner=Owner(kind=OwnerKind.ASSIGNEE),
            severity=RuleSeverity.ASK,
            select=Select(stage_filter=StageFilter.AT_OR_PAST, stage=DeliveryStage.IN_DEVELOPMENT),
        )
    )
    world.now = NOW - timedelta(days=3)
    await world.run("s1")
    world.now = NOW

    report = await world.registry.day_report_builder().build(TENANT, "checkout", TODAY)

    stand = next(section for section in report.sections if section.title == "Where we stand")
    assert [group.lines for group in stand.groups if group.heading == "Jira hygiene"] == [
        ("Jira hygiene: 1 open.",)
    ]
    needs = next(
        section for section in report.sections if section.title == "What we need, and from whom"
    )
    lines = [line for group in needs.groups for line in group.lines]
    assert lines == [
        "Fix: No due date in Checkout Revamp: CHK-1 (waiting 3 days). Escalated to Ira Novak "
        "(Scrum master)."
    ]
    assert report.facts is not None
    assert "Jira hygiene: 1 open." in report.facts.progress.notes


async def test_a_report_says_nothing_while_the_scan_is_off() -> None:
    world = await _world()
    await world.rule(NO_POINTS)
    await world.run()
    await world.repository.save_settings(HygieneSettings(tenant_id=TENANT, enabled=False))

    report = await world.service.report(TENANT, "checkout", None, TODAY)

    assert report.line is None and report.asks == ()
