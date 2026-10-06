from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest

from api.dtos import RiskFindingResponse
from config.settings import Settings
from core.application.blocker_resolution import BlockerResolutionService
from core.application.risk_service import RiskService
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    NodeKind,
    Pod,
    Project,
    Task,
    WorkItem,
    Workstream,
)
from core.domain.risk import DriftFindingKind, RiskProviderConfig, RiskRuleId
from core.domain.rollup import NodeStatus, Rag
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from infra.workflows import drift_scan

TENANT = "demo"
AS_OF = date(2026, 7, 1)


def _iso_days_ago(as_of: date, days: int) -> str:
    reference = as_of - timedelta(days=days)
    return datetime.combine(reference, datetime.min.time(), tzinfo=UTC).isoformat()


def _service(store: InMemoryGraphStore, **overrides: object) -> RiskService:
    return RiskService(
        graph_repository=store,
        time_series_repository=store,
        status_repository=store,
        blocker_resolution=BlockerResolutionService(store, store),
        rollup_repository=store,
        provider_config=RiskProviderConfig(**overrides),
    )


async def _seed_project_with_workstream(
    store: InMemoryGraphStore, *, project_metadata: dict | None = None
) -> None:
    await store.upsert_node(
        Project(tenant_id=TENANT, id="proj-1", name="Project One", metadata=project_metadata or {})
    )
    await store.upsert_node(Workstream(tenant_id=TENANT, id="ws-1", name="Workstream One"))
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT, from_node_id="proj-1", to_node_id="ws-1", kind=EdgeKind.CONTAINS
        )
    )


async def _add_work_item(
    store: InMemoryGraphStore, workstream_id: str, *, item_id: str, metadata: dict
) -> None:
    await store.upsert_node(WorkItem(tenant_id=TENANT, id=item_id, name=item_id, metadata=metadata))
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT,
            from_node_id=workstream_id,
            to_node_id=item_id,
            kind=EdgeKind.CONTAINS,
        )
    )


async def _record_owner_status(
    store: InMemoryGraphStore,
    developer_id: str,
    source: StatusSource,
    *,
    blockers: tuple[str, ...] = (),
) -> None:
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id=TENANT,
            developer_id=developer_id,
            as_of=AS_OF,
            source=source,
            blockers=blockers,
            summary="on track",
            developer_confirmed=True,
        )
    )


async def _record_workstream_rag(store: InMemoryGraphStore, workstream_id: str, rag: Rag) -> None:
    await store.record_node_status(
        NodeStatus(
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.WORKSTREAM, id=workstream_id),
            rag=rag,
            source=StatusSource.INFERRED,
            factors=(),
            as_of=AS_OF,
        )
    )


async def _record_owner_status_with_rows(
    store: InMemoryGraphStore,
    developer_id: str,
    rows: tuple[DeveloperBlocker, ...],
) -> None:
    await store.record_developer_status_with_blockers(
        DeveloperStatus(
            tenant_id=TENANT,
            developer_id=developer_id,
            as_of=AS_OF,
            source=StatusSource.CONFIRMED,
            blockers=tuple(row.description for row in rows),
            summary="on track",
            developer_confirmed=True,
        ),
        rows,
    )


def _blocker_row(
    description: str,
    *,
    developer_id: str = "dev-1",
    work_item_id: str | None = None,
    pod_id: str | None = None,
) -> DeveloperBlocker:
    return DeveloperBlocker(
        tenant_id=TENANT,
        blocker_id=f"blk-{normalize_blocker_key(description).replace(' ', '-')}",
        developer_id=developer_id,
        description=description,
        normalized_key=normalize_blocker_key(description),
        work_item_id=work_item_id,
        pod_id=pod_id,
        source=BlockerSource.CHECKIN,
        first_seen_on=AS_OF,
        last_seen_on=AS_OF,
    )


async def _link_pod_to_workstream(store: InMemoryGraphStore, workstream_id: str) -> None:
    """A pod assigned to the workstream lets work-item blockers resolve to it."""
    await store.upsert_node(Pod(tenant_id=TENANT, id="pod-1", name="Pod One"))
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT,
            from_node_id="pod-1",
            to_node_id=workstream_id,
            kind=EdgeKind.ASSIGNED_TO,
        )
    )


def _settings(**overrides: object) -> Settings:
    settings_factory = cast(Callable[..., Settings], Settings)
    return settings_factory(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        runtime_mode="memory",
        **overrides,
    )


# ---- merged_issue_open ---------------------------------------------------


async def _seed_issue_with_merge_requests(
    store: InMemoryGraphStore,
    *,
    issue_state: str = "in_progress",
    requests: tuple[dict[str, object], ...],
) -> None:
    """Project proj-1 (repo acme/api) holding Jira issue CHK-3, assigned to Liam."""
    await store.upsert_node(
        Project(
            tenant_id=TENANT, id="proj-1", name="Checkout", metadata={"github_repos": "acme/api"}
        )
    )
    await store.upsert_node(
        Task(
            tenant_id=TENANT,
            id="CHK-3",
            name="Payment intent API",
            metadata={"key": "CHK-3", "state": issue_state, "status": "In Progress"},
        )
    )
    await store.upsert_node(Developer(tenant_id=TENANT, id="U-liam", name="Liam"))
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT, from_node_id="proj-1", to_node_id="CHK-3", kind=EdgeKind.CONTAINS
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT, from_node_id="U-liam", to_node_id="CHK-3", kind=EdgeKind.ASSIGNED_TO
        )
    )
    for index, request in enumerate(requests):
        days_ago = int(cast(int, request.pop("days_ago", 2)))
        observed = datetime.combine(AS_OF - timedelta(days=days_ago), datetime.min.time(), UTC)
        await store.append_fact(
            FactEvent(
                tenant_id=TENANT,
                source="vcs_pull_request",
                entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="U-liam"),
                payload={"repo": "acme/api", "id": str(index + 1), **request},
                observed_at=observed,
                correlation_id=f"pr-{index}",
            )
        )


async def test_merged_request_on_an_open_issue_is_drift() -> None:
    store = InMemoryGraphStore()
    await _seed_issue_with_merge_requests(
        store,
        requests=(
            {
                "title": "Payment intent API",
                "source_branch": "CHK-3-payment-intent-api",
                "merged": True,
                "state": "merged",
                "web_url": "https://git.test/acme/api/-/merge_requests/1",
            },
        ),
    )

    findings = await _service(store).project_drift(TENANT, "proj-1", AS_OF)

    assert [f.kind for f in findings] == [DriftFindingKind.MERGED_ISSUE_OPEN]
    finding = findings[0]
    assert finding.severity is Rag.AMBER
    assert finding.entity_ref == EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-3")
    assert finding.owner_id == "U-liam"
    assert finding.stated_source is None
    assert finding.evidence is not None
    assert finding.evidence.identifier == "acme/api#1"
    assert finding.evidence.url == "https://git.test/acme/api/-/merge_requests/1"
    assert "merged 2 days ago" in finding.reason
    assert "CHK-3 is still 'In Progress'" in finding.reason


@pytest.mark.parametrize(
    ("issue_state", "requests"),
    [
        pytest.param(
            "done",
            ({"title": "CHK-3 x", "merged": True, "state": "merged"},),
            id="issue-done",
        ),
        pytest.param(
            "in_progress",
            ({"title": "CHK-3 x", "merged": True, "state": "merged", "days_ago": 0},),
            id="merged-today",
        ),
        pytest.param(
            "in_progress",
            (
                {"title": "CHK-3 part 1", "merged": True, "state": "merged"},
                {"title": "CHK-3 part 2", "merged": False, "state": "open", "draft": True},
            ),
            id="another-request-still-open",
        ),
        pytest.param(
            "in_progress",
            ({"title": "Payment intent", "merged": True, "state": "merged"},),
            id="no-key",
        ),
        pytest.param(
            "in_progress",
            ({"title": "CHK-30 other issue", "merged": True, "state": "merged"},),
            id="other-key",
        ),
    ],
)
async def test_merged_issue_open_is_not_raised_when_nothing_disagrees(
    issue_state: str, requests: tuple[dict[str, object], ...]
) -> None:
    store = InMemoryGraphStore()
    await _seed_issue_with_merge_requests(store, issue_state=issue_state, requests=requests)

    findings = await _service(store).project_drift(TENANT, "proj-1", AS_OF)

    assert [f for f in findings if f.kind is DriftFindingKind.MERGED_ISSUE_OPEN] == []


async def test_merged_issue_open_ignores_requests_outside_the_project_repos() -> None:
    store = InMemoryGraphStore()
    await _seed_issue_with_merge_requests(
        store, requests=({"title": "CHK-3 x", "merged": True, "state": "merged"},)
    )
    await store.upsert_node(
        Project(
            tenant_id=TENANT, id="proj-1", name="Checkout", metadata={"github_repos": "acme/web"}
        )
    )

    assert await _service(store).project_drift(TENANT, "proj-1", AS_OF) == []


async def test_merged_issue_open_never_downgrades_the_assignee() -> None:
    # Forgetting to move a ticket is tracker hygiene, not a false status claim.
    store = InMemoryGraphStore()
    await _seed_issue_with_merge_requests(
        store, requests=({"title": "CHK-3 x", "merged": True, "state": "merged"},)
    )
    await _record_owner_status(store, "U-liam", StatusSource.CONFIRMED)

    result = await _service(store).scan_and_record_drift(TENANT, "proj-1", AS_OF)

    assert [f.kind for f in result.findings] == [DriftFindingKind.MERGED_ISSUE_OPEN]
    assert result.statuses_downgraded == 0
    status = await store.latest_developer_status(TENANT, "U-liam", AS_OF)
    assert status is not None
    assert status.source is StatusSource.CONFIRMED


# ---- said_done_no_pr ----------------------------------------------------


async def test_said_done_without_pr_flags_watermelon() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store)
    await _add_work_item(store, "ws-1", item_id="wi-1", metadata={"state": "done"})

    findings = await _service(store).project_drift(TENANT, "proj-1", AS_OF)

    assert len(findings) == 1
    assert findings[0].kind is DriftFindingKind.SAID_DONE_NO_PR
    assert findings[0].severity is Rag.RED
    assert findings[0].entity_ref.id == "wi-1"


async def test_said_done_with_linked_pr_is_clean() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store)
    await _add_work_item(store, "ws-1", item_id="wi-1", metadata={"state": "done", "pr_id": "PR-9"})

    findings = await _service(store).project_drift(TENANT, "proj-1", AS_OF)

    assert findings == []


# ---- claimed_progress_no_activity ---------------------------------------


async def test_claimed_progress_without_git_activity_flags_amber() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store, project_metadata={"github_repos": "acme/api"})
    await _add_work_item(
        store,
        "ws-1",
        item_id="wi-1",
        metadata={
            "state": "in_progress",
            "owner_id": "dev-1",
            "repo": "acme/api",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    await _record_owner_status(store, "dev-1", StatusSource.CONFIRMED)

    findings = await _service(store).project_drift(TENANT, "proj-1", AS_OF)

    assert len(findings) == 1
    assert findings[0].kind is DriftFindingKind.CLAIMED_PROGRESS_NO_ACTIVITY
    assert findings[0].severity is Rag.AMBER
    assert findings[0].owner_id == "dev-1"
    assert findings[0].reason.endswith("no Git/PR activity in 5 days.")


async def test_recent_git_activity_suppresses_claimed_progress() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store, project_metadata={"github_repos": "acme/api"})
    await _add_work_item(
        store,
        "ws-1",
        item_id="wi-1",
        metadata={
            "state": "in_progress",
            "owner_id": "dev-1",
            "repo": "acme/api",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    await _record_owner_status(store, "dev-1", StatusSource.CONFIRMED)
    await store.append_fact_once(
        FactEvent(
            tenant_id=TENANT,
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="dev-1"),
            payload={"repo": "acme/api", "opened_at": _iso_days_ago(AS_OF, 1)},
            observed_at=datetime.now(tz=UTC),
            correlation_id="pr-recent",
        )
    )

    findings = await _service(store).project_drift(TENANT, "proj-1", AS_OF)

    assert findings == []


async def test_reported_blockers_suppress_claimed_progress() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store, project_metadata={"github_repos": "acme/api"})
    await _add_work_item(
        store,
        "ws-1",
        item_id="wi-1",
        metadata={
            "state": "in_progress",
            "owner_id": "dev-1",
            "repo": "acme/api",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    await _record_owner_status(
        store, "dev-1", StatusSource.CONFIRMED, blockers=("waiting on infra",)
    )

    findings = await _service(store).project_drift(TENANT, "proj-1", AS_OF)

    assert findings == []


# ---- green_over_red -----------------------------------------------------


async def test_green_workstream_over_red_child_flags_watermelon() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store)
    await _add_work_item(
        store, "ws-1", item_id="wi-red", metadata={"state": "blocked", "owner_id": "dev-2"}
    )
    await _record_workstream_rag(store, "ws-1", Rag.GREEN)

    findings = await _service(store).project_drift(TENANT, "proj-1", AS_OF)

    assert len(findings) == 1
    assert findings[0].kind is DriftFindingKind.GREEN_OVER_RED
    assert findings[0].severity is Rag.RED
    assert findings[0].entity_ref.id == "ws-1"
    assert findings[0].child_entity_ref is not None
    assert findings[0].child_entity_ref.id == "wi-red"


# ---- scan + persistence + confidence enrichment -------------------------


async def test_scan_records_facts_and_downgrades_contradicted_confidence() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store)
    await _add_work_item(
        store, "ws-1", item_id="wi-1", metadata={"state": "done", "owner_id": "dev-1"}
    )
    await _record_owner_status(store, "dev-1", StatusSource.CONFIRMED)

    result = await _service(store).scan_and_record_drift(TENANT, "proj-1", AS_OF)

    assert result.findings_recorded == 1
    assert result.statuses_downgraded == 1
    downgraded = await store.latest_developer_status(TENANT, "dev-1", AS_OF)
    assert downgraded is not None
    assert downgraded.source is StatusSource.PARTIAL
    assert downgraded.developer_confirmed is False
    drift_facts = await store.list_recent_facts(TENANT, sources=("drift",))
    assert len(drift_facts) == 1
    assert drift_facts[0].payload["kind"] == DriftFindingKind.SAID_DONE_NO_PR.value


async def test_scan_confidence_downgrade_is_idempotent() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store)
    await _add_work_item(
        store, "ws-1", item_id="wi-1", metadata={"state": "done", "owner_id": "dev-1"}
    )
    await _record_owner_status(store, "dev-1", StatusSource.CONFIRMED)
    service = _service(store)

    first = await service.scan_and_record_drift(TENANT, "proj-1", AS_OF)
    second = await service.scan_and_record_drift(TENANT, "proj-1", AS_OF)

    # Detection is stable, but a status already downgraded to PARTIAL is no
    # longer "green" and must not be downgraded (or re-confirmed) a second time.
    assert first.findings_recorded == second.findings_recorded == 1
    assert first.statuses_downgraded == 1
    assert second.statuses_downgraded == 0


async def test_green_over_red_does_not_downgrade_owner_confidence() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store)
    await _add_work_item(
        store, "ws-1", item_id="wi-red", metadata={"state": "blocked", "owner_id": "dev-2"}
    )
    await _record_workstream_rag(store, "ws-1", Rag.GREEN)
    await _record_owner_status(store, "dev-2", StatusSource.CONFIRMED)

    result = await _service(store).scan_and_record_drift(TENANT, "proj-1", AS_OF)

    assert result.findings_recorded == 1
    assert result.statuses_downgraded == 0
    unchanged = await store.latest_developer_status(TENANT, "dev-2", AS_OF)
    assert unchanged is not None
    assert unchanged.source is StatusSource.CONFIRMED


# ---- blocker attribution scoping (watermelon + downgrade) ---------------


async def test_watermelon_true_when_only_blocker_is_on_other_work_item() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store)
    await _link_pod_to_workstream(store, "ws-1")
    await _add_work_item(
        store,
        "ws-1",
        item_id="wi-a",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "owner_id": "dev-1",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    await _add_work_item(store, "ws-1", item_id="wi-b", metadata={"state": "done", "pr_id": "PR-9"})
    await _record_owner_status_with_rows(
        store, "dev-1", (_blocker_row("waiting on design", work_item_id="wi-b"),)
    )
    service = _service(store, default_no_pr_days=3, default_stale_days=30)

    delta = await service.assess_and_persist_project(TENANT, "proj-1", AS_OF)

    assert len(delta.newly_opened) == 1
    finding = delta.newly_opened[0]
    assert finding.rule_id is RiskRuleId.FEATURE_NO_PR
    assert finding.entity_ref.id == "wi-a"
    # The disclosed blocker concerns wi-b, so it does not vouch for wi-a.
    assert finding.owner_status_has_blockers is False
    assert RiskFindingResponse.from_domain(finding).is_watermelon is True


async def test_unattributed_blocker_suppresses_watermelon() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store)
    await _add_work_item(
        store,
        "ws-1",
        item_id="wi-a",
        metadata={
            "item_type": "feature",
            "state": "in_progress",
            "owner_id": "dev-1",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    await _record_owner_status_with_rows(store, "dev-1", (_blocker_row("mystery dependency"),))
    service = _service(store, default_no_pr_days=3, default_stale_days=30)

    delta = await service.assess_and_persist_project(TENANT, "proj-1", AS_OF)

    assert len(delta.newly_opened) == 1
    finding = delta.newly_opened[0]
    # An unattributed blocker could concern any item: benefit of the doubt.
    assert finding.owner_status_has_blockers is True
    assert RiskFindingResponse.from_domain(finding).is_watermelon is False


async def test_claimed_progress_skipped_when_blocker_attributed_to_item() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store, project_metadata={"github_repos": "acme/api"})
    await _add_work_item(
        store,
        "ws-1",
        item_id="wi-1",
        metadata={
            "state": "in_progress",
            "owner_id": "dev-1",
            "repo": "acme/api",
            "created_at": _iso_days_ago(AS_OF, 5),
        },
    )
    await _record_owner_status_with_rows(
        store, "dev-1", (_blocker_row("waiting on infra", work_item_id="wi-1"),)
    )

    findings = await _service(store).project_drift(TENANT, "proj-1", AS_OF)

    assert findings == []


async def test_contradiction_downgrade_skipped_when_item_has_attributed_blocker() -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(store)
    await _add_work_item(
        store, "ws-1", item_id="wi-1", metadata={"state": "done", "owner_id": "dev-1"}
    )
    await _record_owner_status_with_rows(
        store, "dev-1", (_blocker_row("QA signoff pending", work_item_id="wi-1"),)
    )

    result = await _service(store).scan_and_record_drift(TENANT, "proj-1", AS_OF)

    # The contradiction is still detected, but the owner already disclosed a
    # blocker on the very item, so the person-level downgrade is skipped.
    assert result.findings_recorded == 1
    assert result.statuses_downgraded == 0
    status = await store.latest_developer_status(TENANT, "dev-1", AS_OF)
    assert status is not None
    assert status.source is StatusSource.CONFIRMED


# ---- scheduled workflow op (gating + delegation) ------------------------


async def test_drift_scan_skips_project_before_configured_local_run_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(
        store, project_metadata={"risk_run_local_time": "18:00", "risk_run_timezone": "UTC"}
    )
    await _add_work_item(store, "ws-1", item_id="wi-1", metadata={"state": "done"})
    registry = ServiceRegistry(_settings(), graph_store=store)
    monkeypatch.setattr(drift_scan, "_service_registry", lambda: registry)

    result = await drift_scan.run_drift_scan_activity(
        drift_scan.DriftScanInput(
            tenant_id=TENANT,
            observed_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC).isoformat(),
        )
    )

    assert result.projects_evaluated == 1
    assert result.projects_scanned == 0
    assert result.findings_recorded == 0


async def test_drift_scan_runs_after_cutoff_and_is_idempotent_same_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await _seed_project_with_workstream(
        store, project_metadata={"risk_run_local_time": "18:00", "risk_run_timezone": "UTC"}
    )
    await _add_work_item(store, "ws-1", item_id="wi-1", metadata={"state": "done"})
    registry = ServiceRegistry(_settings(), graph_store=store)
    monkeypatch.setattr(drift_scan, "_service_registry", lambda: registry)

    first = await drift_scan.run_drift_scan_activity(
        drift_scan.DriftScanInput(
            tenant_id=TENANT,
            observed_at=datetime(2026, 7, 1, 19, 0, tzinfo=UTC).isoformat(),
        )
    )
    second = await drift_scan.run_drift_scan_activity(
        drift_scan.DriftScanInput(
            tenant_id=TENANT,
            observed_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC).isoformat(),
        )
    )

    assert first.projects_scanned == 1
    assert first.findings_recorded == 1
    assert second.projects_scanned == 0
    cursor = await store.get_cursor(TENANT, "drift", "project:proj-1")
    assert cursor.value == "2026-07-01"
