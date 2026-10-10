from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import pytest

from core.application.blocker_resolution import BlockerResolutionService
from core.application.risk_service import RiskService
from core.domain.errors import GraphNotFound
from core.domain.graph import EntityRef, FactEvent, NodeKind
from core.domain.risk import RiskFinding, RiskFindingStatus, RiskProviderConfig, RiskRuleId
from core.domain.rollup import Rag
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore

TENANT = "demo"
AS_OF = date(2026, 7, 1)


def _iso_days_ago(as_of: date, days: int) -> str:
    reference_date = as_of - timedelta(days=days)
    return datetime.combine(reference_date, datetime.min.time(), tzinfo=UTC).isoformat()


async def _setup_project_with_workstream(
    store: InMemoryGraphStore,
    *,
    project_metadata: dict | None = None,
    workstream_metadata: dict | None = None,
) -> tuple[str, str]:
    from core.domain.graph import EdgeKind, GraphEdge, Project, Workstream

    await store.upsert_node(
        Project(tenant_id=TENANT, id="proj-1", name="Project One", metadata=project_metadata or {})
    )
    await store.upsert_node(
        Workstream(
            tenant_id=TENANT,
            id="ws-1",
            name="Workstream One",
            metadata=workstream_metadata or {},
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT, from_node_id="proj-1", to_node_id="ws-1", kind=EdgeKind.CONTAINS
        )
    )
    return "proj-1", "ws-1"


def _service(store: InMemoryGraphStore, **overrides: object) -> RiskService:
    return RiskService(
        graph_repository=store,
        time_series_repository=store,
        status_repository=store,
        blocker_resolution=BlockerResolutionService(store, store),
        provider_config=RiskProviderConfig(**overrides),
    )


async def _add_work_item(
    store: InMemoryGraphStore,
    workstream_id: str,
    *,
    item_id: str,
    metadata: dict,
) -> None:
    from core.domain.graph import EdgeKind, GraphEdge, WorkItem

    await store.upsert_node(WorkItem(tenant_id=TENANT, id=item_id, name=item_id, metadata=metadata))
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT,
            from_node_id=workstream_id,
            to_node_id=item_id,
            kind=EdgeKind.CONTAINS,
        )
    )


async def test_feature_without_pr_past_threshold_is_flagged() -> None:
    store = InMemoryGraphStore()
    project_id, workstream_id = await _setup_project_with_workstream(store)
    await _add_work_item(
        store,
        workstream_id,
        item_id="wi-1",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=30)

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert len(delta.newly_opened) == 1
    finding = delta.newly_opened[0]
    assert finding.rule_id is RiskRuleId.FEATURE_NO_PR
    assert finding.entity_ref == EntityRef(tenant_id=TENANT, kind=NodeKind.WORK_ITEM, id="wi-1")
    assert finding.age_days == 5
    assert finding.status is RiskFindingStatus.OPEN
    assert finding.reason == "wi-1 has been active for 5 days with no linked pull request."


async def test_workstream_threshold_override_suppresses_finding() -> None:
    store = InMemoryGraphStore()
    project_id, workstream_id = await _setup_project_with_workstream(
        store, workstream_metadata={"risk_no_pr_days": 10}
    )
    await _add_work_item(
        store,
        workstream_id,
        item_id="wi-1",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=30)

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert delta.newly_opened == ()


async def test_stale_work_item_is_flagged_independent_of_item_type() -> None:
    store = InMemoryGraphStore()
    project_id, workstream_id = await _setup_project_with_workstream(store)
    await _add_work_item(
        store,
        workstream_id,
        item_id="wi-chore",
        metadata={
            "item_type": "chore",
            "state": "in_progress",
            "pr_id": "99",
            "last_transition_at": _iso_days_ago(AS_OF, 10),
        },
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=7)

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert len(delta.newly_opened) == 1
    assert delta.newly_opened[0].rule_id is RiskRuleId.STALE_WORK_ITEM
    assert delta.newly_opened[0].age_days == 10
    assert delta.newly_opened[0].reason == "wi-chore has had no state change in 10 days."


async def test_pr_age_finding_uses_pr_author_as_owner_when_unmatched() -> None:
    store = InMemoryGraphStore()
    project_id, _workstream_id = await _setup_project_with_workstream(
        store, project_metadata={"github_repos": "acme/api"}
    )
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="dev-author"),
            payload={
                "repo": "acme/api",
                "id": "42",
                "title": "Add feature",
                "merged": False,
                "opened_at": _iso_days_ago(AS_OF, 5),
            },
            observed_at=datetime.now(tz=UTC),
            correlation_id="pr-42-observed",
        )
    )
    service = _service(store, default_pr_age_days=3)

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert len(delta.newly_opened) == 1
    finding = delta.newly_opened[0]
    assert finding.rule_id is RiskRuleId.PR_AGE
    assert finding.owner_id == "dev-author"
    assert finding.evidence.identifier == "acme/api#42"
    assert finding.age_days == 5
    assert finding.reason == "Pull request 'Add feature' in acme/api has been open for 5 days."


async def test_a_reason_counts_one_day_in_the_singular() -> None:
    store = InMemoryGraphStore()
    project_id, _workstream_id = await _setup_project_with_workstream(
        store, project_metadata={"github_repos": "acme/api"}
    )
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="dev-author"),
            payload={
                "repo": "acme/api",
                "id": "43",
                "title": "Fix typo",
                "merged": False,
                "opened_at": _iso_days_ago(AS_OF, 1),
            },
            observed_at=datetime.now(tz=UTC),
            correlation_id="pr-43-observed",
        )
    )
    service = _service(store, default_pr_age_days=1)

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert [finding.reason for finding in delta.newly_opened] == [
        "Pull request 'Fix typo' in acme/api has been open for 1 day."
    ]


@pytest.mark.parametrize(
    ("entity", "state", "flagged", "owner"),
    [
        pytest.param(NodeKind.DEVELOPER, "closed", False, None, id="closed-unmerged"),
        pytest.param(NodeKind.DEVELOPER, "open", True, "dev-author", id="open"),
        # An author no member is linked to is recorded on the repo: no owner.
        pytest.param(NodeKind.REPO, "open", True, None, id="unlinked-author"),
    ],
)
async def test_pr_age_skips_closed_requests_and_owns_only_member_authored_ones(
    entity: NodeKind, state: str, flagged: bool, owner: str | None
) -> None:
    store = InMemoryGraphStore()
    project_id, _workstream_id = await _setup_project_with_workstream(
        store, project_metadata={"github_repos": "acme/api"}
    )
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="vcs_pull_request",
            entity_ref=EntityRef(
                tenant_id=TENANT,
                kind=entity,
                id="dev-author" if entity is NodeKind.DEVELOPER else "acme/api",
            ),
            payload={
                "repo": "acme/api",
                "id": "42",
                "title": "Add feature",
                "merged": False,
                "state": state,
                "opened_at": _iso_days_ago(AS_OF, 5),
            },
            observed_at=datetime.now(tz=UTC),
            correlation_id="pr-42-observed",
        )
    )
    service = _service(store, default_pr_age_days=3)

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert [f.rule_id for f in delta.newly_opened] == ([RiskRuleId.PR_AGE] if flagged else [])
    if flagged:
        assert delta.newly_opened[0].owner_id == owner


async def test_pr_age_ignores_prs_outside_project_repo_scope() -> None:
    store = InMemoryGraphStore()
    project_id, _workstream_id = await _setup_project_with_workstream(
        store, project_metadata={"github_repos": "acme/other"}
    )
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="dev-author"),
            payload={
                "repo": "acme/api",
                "id": "42",
                "title": "Add feature",
                "merged": False,
                "opened_at": _iso_days_ago(AS_OF, 10),
            },
            observed_at=datetime.now(tz=UTC),
            correlation_id="pr-42-observed",
        )
    )
    service = _service(store, default_pr_age_days=3)

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert delta.newly_opened == ()


async def _add_project(store: InMemoryGraphStore, project_id: str, repos: str) -> None:
    from core.domain.graph import Project

    await store.upsert_node(
        Project(tenant_id=TENANT, id=project_id, name=project_id, metadata={"github_repos": repos})
    )


async def _pull_request_fact(
    store: InMemoryGraphStore,
    *,
    repo: str,
    pr_id: str,
    title: str,
    author: str = "dev-author",
    opened_days_ago: int = 5,
    as_of: date = AS_OF,
    merged: bool = False,
    observed_at: datetime | None = None,
) -> None:
    """One synced state of a merge request an ``author`` no work item claims opened."""
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id=author),
            payload={
                "repo": repo,
                "id": pr_id,
                "title": title,
                "merged": merged,
                "state": "merged" if merged else "open",
                "opened_at": _iso_days_ago(as_of, opened_days_ago),
            },
            observed_at=observed_at or datetime.now(tz=UTC),
            correlation_id=f"pr-{repo}-{pr_id}-{merged}",
        )
    )


def _identifiers(findings: Iterable[RiskFinding]) -> list[str]:
    return sorted(finding.evidence.identifier for finding in findings)


async def test_two_open_pull_requests_of_one_author_are_two_findings() -> None:
    store = InMemoryGraphStore()
    project_id, _workstream_id = await _setup_project_with_workstream(
        store, project_metadata={"github_repos": "acme/api"}
    )
    await _pull_request_fact(store, repo="acme/api", pr_id="42", title="Add feature")
    await _pull_request_fact(store, repo="acme/api", pr_id="43", title="Fix typo")
    service = _service(store, default_pr_age_days=3)

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)
    again = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    # Filed on one author, they were one finding: the second was never recorded.
    assert _identifiers(delta.newly_opened) == ["acme/api#42", "acme/api#43"]
    assert again.newly_opened == ()
    assert _identifiers(await service.project_risks(TENANT, project_id, AS_OF)) == [
        "acme/api#42",
        "acme/api#43",
    ]


async def test_a_merged_pull_request_clears_only_its_own_finding() -> None:
    store = InMemoryGraphStore()
    project_id, _workstream_id = await _setup_project_with_workstream(
        store, project_metadata={"github_repos": "acme/api"}
    )
    opened = datetime(2026, 6, 30, 9, tzinfo=UTC)
    await _pull_request_fact(
        store, repo="acme/api", pr_id="42", title="Add feature", observed_at=opened
    )
    await _pull_request_fact(
        store, repo="acme/api", pr_id="43", title="Fix typo", observed_at=opened
    )
    service = _service(store, default_pr_age_days=3)
    await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    await _pull_request_fact(
        store,
        repo="acme/api",
        pr_id="42",
        title="Add feature",
        merged=True,
        observed_at=datetime(2026, 7, 1, 9, tzinfo=UTC),
    )
    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert _identifiers(delta.newly_cleared) == ["acme/api#42"]
    assert _identifiers(await service.project_risks(TENANT, project_id, AS_OF)) == ["acme/api#43"]


async def test_the_portfolio_lists_every_open_pull_request_of_a_person_in_every_project() -> None:
    # One person's request in each of two projects: the project pages listed one
    # each, and the portfolio, keyed by the author, only the latest of the two.
    store = InMemoryGraphStore()
    await _add_project(store, "proj-orders", "acme/orders-api")
    await _add_project(store, "proj-login", "acme/login-service")
    await _pull_request_fact(
        store, repo="acme/orders-api", pr_id="3", title="ORD-6 Refunds", author="dev-author"
    )
    await _pull_request_fact(
        store, repo="acme/login-service", pr_id="1", title="LGN-3 Passkeys", author="dev-author"
    )
    service = _service(store, default_pr_age_days=3)
    for project_id in ("proj-orders", "proj-login"):
        await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    portfolio = await service.portfolio_risks(TENANT, AS_OF)

    assert _identifiers(portfolio) == ["acme/login-service#1", "acme/orders-api#3"]
    assert _identifiers(await service.project_risks(TENANT, "proj-orders", AS_OF)) == [
        "acme/orders-api#3"
    ]
    assert _identifiers(await service.project_risks(TENANT, "proj-login", AS_OF)) == [
        "acme/login-service#1"
    ]


async def _append_author_keyed_risk(
    store: InMemoryGraphStore, project_id: str, repo: str, pr_id: str, observed_at: datetime
) -> None:
    """A ``pr_age`` fact as it was recorded when a finding was keyed by its author alone."""
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="risk",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="dev-author"),
            payload={
                "risk_key": "pr_age:developer:dev-author",
                "rule_id": RiskRuleId.PR_AGE.value,
                "severity": Rag.AMBER.value,
                "entity_kind": NodeKind.DEVELOPER.value,
                "entity_id": "dev-author",
                "workstream_id": None,
                "project_id": project_id,
                "reason": f"Pull request 'Work' in {repo} has been open for 5 days.",
                "evidence_identifier": f"{repo}#{pr_id}",
                "age_days": 5,
                "threshold_days": 3,
                "owner_id": "dev-author",
                "detected_at": _iso_days_ago(AS_OF, 0),
                "transition": "opened",
            },
            observed_at=observed_at,
            correlation_id=f"risk:{TENANT}:pr_age:developer:dev-author:opened:{project_id}",
        )
    )


async def test_findings_recorded_under_the_authors_key_still_read_one_per_pull_request() -> None:
    store = InMemoryGraphStore()
    await _add_project(store, "proj-orders", "acme/orders-api")
    await _add_project(store, "proj-login", "acme/login-service")
    await _pull_request_fact(
        store, repo="acme/orders-api", pr_id="3", title="Work", author="dev-author"
    )
    await _pull_request_fact(
        store, repo="acme/login-service", pr_id="1", title="Work", author="dev-author"
    )
    first, second = datetime(2026, 7, 1, 9, tzinfo=UTC), datetime(2026, 7, 1, 10, tzinfo=UTC)
    await _append_author_keyed_risk(store, "proj-orders", "acme/orders-api", "3", first)
    await _append_author_keyed_risk(store, "proj-login", "acme/login-service", "1", second)
    service = _service(store, default_pr_age_days=3)

    portfolio = await service.portfolio_risks(TENANT, AS_OF)
    delta = await service.assess_and_persist_project(TENANT, "proj-orders", AS_OF)

    assert _identifiers(portfolio) == ["acme/login-service#1", "acme/orders-api#3"]
    # Re-assessed, the same request is the same finding: nothing opens or clears again.
    assert delta.newly_opened == () and delta.newly_cleared == ()


async def test_finding_shows_owner_human_status_alongside_signal() -> None:
    store = InMemoryGraphStore()
    project_id, workstream_id = await _setup_project_with_workstream(store)
    await _add_work_item(
        store,
        workstream_id,
        item_id="wi-1",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "created_at": _iso_days_ago(AS_OF, 5),
            "owner_id": "dev-1",
        },
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id=TENANT,
            developer_id="dev-1",
            as_of=AS_OF,
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="All good, on track.",
        )
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=30)

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    finding = delta.newly_opened[0]
    assert finding.owner_status_summary == "All good, on track."
    assert finding.owner_status_source is StatusSource.CONFIRMED
    assert finding.owner_status_has_blockers is False


async def test_owned_finding_appends_fact_against_developer_for_checkin_context_pickup() -> None:
    store = InMemoryGraphStore()
    project_id, workstream_id = await _setup_project_with_workstream(store)
    await _add_work_item(
        store,
        workstream_id,
        item_id="wi-1",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "created_at": _iso_days_ago(AS_OF, 5),
            "owner_id": "dev-1",
        },
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=30)

    await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    developer_ref = EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="dev-1")
    facts = await store.list_facts(TENANT, developer_ref)
    risk_facts = [fact for fact in facts if fact.source == "risk"]
    assert len(risk_facts) == 1
    assert risk_facts[0].payload["transition"] == "opened"


async def test_repeat_assessment_does_not_duplicate_open_findings() -> None:
    store = InMemoryGraphStore()
    project_id, workstream_id = await _setup_project_with_workstream(store)
    await _add_work_item(
        store,
        workstream_id,
        item_id="wi-1",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=30)

    first = await service.assess_and_persist_project(TENANT, project_id, AS_OF)
    second = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert len(first.newly_opened) == 1
    assert second.newly_opened == ()
    assert len(second.open_findings) == 1


async def test_resolved_condition_produces_newly_cleared_and_removes_from_open_list() -> None:
    from core.domain.graph import GraphNode

    store = InMemoryGraphStore()
    project_id, workstream_id = await _setup_project_with_workstream(store)
    await _add_work_item(
        store,
        workstream_id,
        item_id="wi-1",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=30)
    await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    # Attach a PR so the "no linked PR" condition clears.
    await store.upsert_node(
        GraphNode(
            tenant_id=TENANT,
            id="wi-1",
            kind=NodeKind.WORK_ITEM,
            name="wi-1",
            metadata={
                "item_type": "feature",
                "state": "in_progress",
                "created_at": _iso_days_ago(AS_OF, 5),
                "pr_id": "7",
            },
        )
    )

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert len(delta.newly_cleared) == 1
    assert delta.newly_cleared[0].status is RiskFindingStatus.CLEARED
    assert delta.open_findings == ()

    remaining = await service.project_risks(TENANT, project_id, AS_OF)
    assert remaining == []


async def test_project_risks_and_portfolio_risks_read_persisted_state() -> None:
    store = InMemoryGraphStore()
    project_id, workstream_id = await _setup_project_with_workstream(store)
    await _add_work_item(
        store,
        workstream_id,
        item_id="wi-1",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=30)
    await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    project_findings = await service.project_risks(TENANT, project_id, AS_OF)
    portfolio_findings = await service.portfolio_risks(TENANT, AS_OF)

    assert len(project_findings) == 1
    assert project_findings[0].rule_id is RiskRuleId.FEATURE_NO_PR
    assert len(portfolio_findings) == 1


async def test_project_risks_raises_for_unknown_project() -> None:
    store = InMemoryGraphStore()
    service = _service(store)

    with pytest.raises(GraphNotFound):
        await service.project_risks(TENANT, "does-not-exist", AS_OF)


async def test_severity_escalates_to_red_at_double_threshold() -> None:
    store = InMemoryGraphStore()
    project_id, workstream_id = await _setup_project_with_workstream(store)
    await _add_work_item(
        store,
        workstream_id,
        item_id="wi-1",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "created_at": _iso_days_ago(AS_OF, 10),
        },
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=30)

    delta = await service.assess_and_persist_project(TENANT, project_id, AS_OF)

    assert delta.newly_opened[0].severity is Rag.RED


async def _persist_amber_feature_no_pr(store: InMemoryGraphStore, detected_on: date) -> RiskService:
    """Record a 4-day-old feature-without-PR finding against a 3-day threshold.

    4 days is over the threshold but under double it, so the finding is
    recorded amber — which leaves room for it to escalate as it ages.
    """
    project_id, workstream_id = await _setup_project_with_workstream(store)
    await _add_work_item(
        store,
        workstream_id,
        item_id="wi-1",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "created_at": _iso_days_ago(detected_on, 4),
        },
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=30)
    delta = await service.assess_and_persist_project(TENANT, project_id, detected_on)
    assert [(finding.age_days, finding.severity) for finding in delta.newly_opened] == [
        (4, Rag.AMBER)
    ]
    return service


async def test_open_finding_escalates_to_red_as_it_ages_on_read() -> None:
    store = InMemoryGraphStore()
    # An open finding ages against the wall clock it was detected on, so this
    # walks forward from today rather than from the suite's fixed AS_OF.
    today = datetime.now(tz=UTC).date()
    service = await _persist_amber_feature_no_pr(store, today)

    # Two days on, the same open finding is 6 days old — double the three-day
    # threshold — so reading it back must report red, not the recorded amber.
    later = await service.project_risks(TENANT, "proj-1", today + timedelta(days=2))

    assert [(finding.age_days, finding.severity) for finding in later] == [(6, Rag.RED)]


async def test_open_finding_stays_amber_until_it_reaches_double_threshold() -> None:
    store = InMemoryGraphStore()
    today = datetime.now(tz=UTC).date()
    service = await _persist_amber_feature_no_pr(store, today)

    later = await service.project_risks(TENANT, "proj-1", today + timedelta(days=1))

    assert [(finding.age_days, finding.severity) for finding in later] == [(5, Rag.AMBER)]


async def _append_legacy_risk_fact(store: InMemoryGraphStore, project_id: str) -> None:
    """A risk fact in the shape written before the threshold was persisted.

    A finding is only re-recorded when it *opens*, so one already open when
    this landed would keep its original payload for the rest of its life.
    """
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="risk",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.WORK_ITEM, id="wi-1"),
            payload={
                "risk_key": "feature_no_pr:work_item:wi-1",
                "rule_id": RiskRuleId.FEATURE_NO_PR.value,
                "severity": Rag.AMBER.value,
                "entity_kind": NodeKind.WORK_ITEM.value,
                "entity_id": "wi-1",
                "workstream_id": "ws-1",
                "project_id": project_id,
                "reason": "Legacy finding.",
                "evidence_identifier": "wi-1",
                "age_days": 4,
                "detected_at": _iso_days_ago(AS_OF, 0),
                "transition": "opened",
            },
            observed_at=datetime.combine(AS_OF, datetime.min.time(), tzinfo=UTC),
            correlation_id="risk:demo:legacy:opened",
        )
    )


async def test_finding_recorded_without_a_threshold_is_judged_against_configured_one() -> None:
    store = InMemoryGraphStore()
    project_id, _ = await _setup_project_with_workstream(store)
    await _append_legacy_risk_fact(store, project_id)
    service = _service(store, default_no_pr_days=3, default_stale_days=30)

    later = await service.project_risks(TENANT, project_id, AS_OF + timedelta(days=30))

    # 34 days against a 3-day threshold: red, not the amber it was recorded at.
    assert [(finding.age_days, finding.severity) for finding in later] == [(34, Rag.RED)]


async def test_workstream_threshold_override_applies_when_re_deriving_severity() -> None:
    store = InMemoryGraphStore()
    project_id, _ = await _setup_project_with_workstream(
        store, workstream_metadata={"risk_no_pr_days": 30}
    )
    await _append_legacy_risk_fact(store, project_id)
    service = _service(store, default_no_pr_days=3, default_stale_days=90)

    later = await service.project_risks(TENANT, project_id, AS_OF + timedelta(days=30))

    # The workstream allows 30 days, so 34 is over but nowhere near double it.
    assert [(finding.age_days, finding.severity) for finding in later] == [(34, Rag.AMBER)]


async def test_an_open_pull_request_finding_reads_with_the_age_it_has_today() -> None:
    store = InMemoryGraphStore()
    # An open finding ages against the wall clock it was detected on, so this
    # walks forward from today rather than from the suite's fixed AS_OF.
    today = datetime.now(tz=UTC).date()
    project_id, _workstream_id = await _setup_project_with_workstream(
        store, project_metadata={"github_repos": "acme/api"}
    )
    await _pull_request_fact(
        store, repo="acme/api", pr_id="42", title="Add feature", opened_days_ago=3, as_of=today
    )
    service = _service(store, default_pr_age_days=3)
    await service.assess_and_persist_project(TENANT, project_id, today)

    (later,) = await service.project_risks(TENANT, project_id, today + timedelta(days=2))

    # One age, not "open for 3 days" beside 5.
    assert later.age_days == 5
    assert later.reason == "Pull request 'Add feature' in acme/api has been open for 5 days."
    # What is recorded stays what was true when the finding opened: the feed reads it.
    (recorded,) = await store.list_recent_facts(TENANT, sources=("risk",))
    assert recorded.payload["reason"] == (
        "Pull request 'Add feature' in acme/api has been open for 3 days."
    )


async def _append_recorded_risk(
    store: InMemoryGraphStore, project_id: str, *, rule_id: RiskRuleId, reason: str, age_days: int
) -> None:
    """An open finding as recorded on ``AS_OF``, with the sentence it was recorded with."""
    if rule_id is RiskRuleId.PR_AGE:
        entity_kind, entity_id, evidence = NodeKind.DEVELOPER, "dev-author", "acme/api#42"
    else:
        entity_kind, entity_id, evidence = NodeKind.WORK_ITEM, "wi-1", "wi-1"
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="risk",
            entity_ref=EntityRef(tenant_id=TENANT, kind=entity_kind, id=entity_id),
            payload={
                "risk_key": f"{rule_id.value}:{entity_kind.value}:{entity_id}",
                "rule_id": rule_id.value,
                "severity": Rag.AMBER.value,
                "entity_kind": entity_kind.value,
                "entity_id": entity_id,
                "workstream_id": "ws-1",
                "project_id": project_id,
                "reason": reason,
                "evidence_identifier": evidence,
                "age_days": age_days,
                "threshold_days": 3,
                "detected_at": _iso_days_ago(AS_OF, 0),
                "transition": "opened",
            },
            observed_at=datetime.combine(AS_OF, datetime.min.time(), tzinfo=UTC),
            correlation_id=f"risk:{TENANT}:{rule_id.value}:opened",
        )
    )


@pytest.mark.parametrize(
    ("rule_id", "recorded", "expected"),
    [
        pytest.param(
            RiskRuleId.PR_AGE,
            "Pull request 'Add feature' in acme/api has been open for 3 day(s).",
            "Pull request 'Add feature' in acme/api has been open for 5 days.",
            id="pull-request-recorded-as-day(s)",
        ),
        pytest.param(
            RiskRuleId.PR_AGE,
            "Pull request 'Retry after 3 days' in acme/api has been open for 3 days.",
            "Pull request 'Retry after 3 days' in acme/api has been open for 5 days.",
            id="a-title-naming-days-is-not-the-count",
        ),
        pytest.param(
            RiskRuleId.FEATURE_NO_PR,
            "wi-1 has been active for 3 days with no linked pull request.",
            "wi-1 has been active for 5 days with no linked pull request.",
            id="feature-without-a-pull-request",
        ),
        pytest.param(
            RiskRuleId.STALE_WORK_ITEM,
            "wi-1 has had no state change in 3 day(s).",
            "wi-1 has had no state change in 5 days.",
            id="stale-work-item",
        ),
        pytest.param(
            RiskRuleId.STALE_WORK_ITEM,
            "Reworded by hand.",
            "Reworded by hand.",
            id="a-sentence-with-no-count-stays-as-recorded",
        ),
    ],
)
async def test_a_recorded_reason_is_worded_with_the_age_on_the_day_read(
    rule_id: RiskRuleId, recorded: str, expected: str
) -> None:
    store = InMemoryGraphStore()
    project_id, _workstream_id = await _setup_project_with_workstream(store)
    await _append_recorded_risk(store, project_id, rule_id=rule_id, reason=recorded, age_days=3)
    service = _service(store, default_no_pr_days=3, default_stale_days=30)

    (finding,) = await service.project_risks(TENANT, project_id, AS_OF + timedelta(days=2))

    assert finding.age_days == 5
    assert finding.reason == expected
