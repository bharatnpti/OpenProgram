from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.directory_sync_service import DirectorySyncService
from core.application.sync_recording import classify_sync_error
from core.application.sync_services import IssueReadSyncService, VcsReadSyncService
from core.application.sync_status_service import (
    SyncStatusService,
    cron_interval,
    stale_after_for_cron,
)
from core.domain.directory import DirectoryUser
from core.domain.errors import ProviderConfigurationError, ProviderUnavailable, SecretNotFound
from core.domain.graph import EdgeKind, EntityRef, FactEvent, GraphEdge, NodeKind, Pod, Project
from core.domain.integrations import Issue, Repo, SyncCursor
from core.domain.sync_status import (
    SyncErrorKind,
    SyncHealth,
    SyncOutcome,
    SyncSource,
    SyncSourceStatus,
    SyncStatusConfig,
    SyncTargetOrigin,
)
from core.domain.workflows import SyncDispatchInput
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_status import PostgresSyncCursorRepository
from tests.contract.fakes import FakeDirectoryUserRepository, FakeIssueTracker, FakeVcsProvider

TENANT = "demo"
NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
# Provider error text that must never reach the status view: it echoes a token.
LEAKY_MESSAGE = "upstream said 401 for token=sk-live-abc123 at https://tracker.example.invalid/rest"


class _FailingIssueTracker(FakeIssueTracker):
    async def list_issues_for_query(
        self, tenant_id: str, jql: str, cursor: SyncCursor
    ) -> list[Issue]:
        raise ProviderUnavailable(LEAKY_MESSAGE)

    async def list_issues_updated_since(
        self, tenant_id: str, project_key: str, cursor: SyncCursor
    ) -> list[Issue]:
        raise ProviderUnavailable("issue tracker credentials are not configured")


class _FailingVcsProvider(FakeVcsProvider):
    async def list_repos(self, tenant_id: str) -> list[Repo]:
        raise RuntimeError(LEAKY_MESSAGE)


@dataclass
class _FailingDirectoryProvider:
    async def fetch_users(self, tenant_id: str) -> list[DirectoryUser]:
        raise ProviderConfigurationError(LEAKY_MESSAGE)


@dataclass
class _StaticDirectoryProvider:
    users: list[DirectoryUser] = field(default_factory=list)

    async def fetch_users(self, tenant_id: str) -> list[DirectoryUser]:
        return list(self.users)


def _config(**overrides: object) -> SyncStatusConfig:
    values: dict[str, object] = {
        "issue_tracker_provider": "jira",
        "vcs_provider": "github",
        "calendar_provider": "google",
        "directory_provider": "fake",
        "issue_sync_cron": "0 * * * *",
        "vcs_sync_cron": "*/15 * * * *",
        "directory_sync_cron": "0 */6 * * *",
    }
    values.update(overrides)
    return SyncStatusConfig(**values)


def _service(store: InMemoryGraphStore, **overrides: object) -> SyncStatusService:
    return SyncStatusService(
        graph_repository=store,
        cursor_repository=store,
        time_series_repository=store,
        config=_config(**overrides),
    )


def _source(sources: tuple[SyncSourceStatus, ...], source: SyncSource) -> SyncSourceStatus:
    return next(item for item in sources if item.source is source)


async def _configured_store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    await store.upsert_node(
        Project(
            tenant_id=TENANT,
            id="proj-pay",
            name="Payments",
            metadata={"jira_project_key": "PAY", "github_repos": "acme/pay-api"},
        )
    )
    await store.upsert_node(Pod(tenant_id=TENANT, id="pod-checkout", name="Checkout"))
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT,
            from_node_id="proj-pay",
            to_node_id="pod-checkout",
            kind=EdgeKind.CONTAINS,
        )
    )
    return store


def _ok_cursor(checked_at: datetime, items: int = 3) -> SyncCursor:
    return SyncCursor(
        value=checked_at.isoformat(),
        updated_at=checked_at,
        metadata={"last_checked_at": checked_at.isoformat(), "last_item_count": items},
    )


async def _issue_scope(store: InMemoryGraphStore) -> str:
    report = await _service(store).status(TENANT, now=NOW)
    issue = _source(report.sources, SyncSource.ISSUE_TRACKER)
    return issue.targets[0].scope


# --- recording -------------------------------------------------------------


async def test_failed_issue_sync_records_failure_without_moving_the_cursor() -> None:
    store = InMemoryGraphStore()
    previous = _ok_cursor(NOW - timedelta(hours=2))
    await store.record_cursor(TENANT, "issue", "query:project:proj-pay:abc", previous)
    service = IssueReadSyncService(
        issue_tracker=_FailingIssueTracker(),
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
    )

    with pytest.raises(ProviderUnavailable):
        await service.sync_query(
            tenant_id=TENANT,
            jql='project = "PAY"',
            target_node_id="proj-pay",
            target_node_kind=NodeKind.PROJECT,
            cursor_scope="query:project:proj-pay:abc",
            observed_at=NOW,
        )

    cursor = await store.get_cursor(TENANT, "issue", "query:project:proj-pay:abc")
    # Same resume position, so the next run is an idempotent retry.
    assert cursor.value == previous.value
    assert cursor.updated_at == previous.updated_at
    assert cursor.metadata["last_checked_at"] == previous.metadata["last_checked_at"]
    assert cursor.metadata["last_failed_at"] == NOW.isoformat()
    assert cursor.metadata["last_error_kind"] == "provider_unavailable"
    # Only the category is stored -- never the provider's text.
    assert LEAKY_MESSAGE not in str(cursor.metadata)
    assert "sk-live" not in str(cursor.metadata)


async def test_missing_issue_tracker_credentials_record_a_credentials_failure() -> None:
    store = InMemoryGraphStore()
    service = IssueReadSyncService(
        issue_tracker=_FailingIssueTracker(),
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
    )

    with pytest.raises(ProviderUnavailable):
        await service.sync_project(tenant_id=TENANT, project_key="PAY", observed_at=NOW)

    cursor = await store.get_cursor(TENANT, "issue", "project:PAY")
    assert cursor.value is None
    assert cursor.metadata == {
        "last_failed_at": NOW.isoformat(),
        "last_error_kind": "credentials",
    }


async def test_failed_vcs_sync_records_an_unexpected_failure() -> None:
    store = InMemoryGraphStore()
    service = VcsReadSyncService(
        vcs_provider=_FailingVcsProvider(),
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
    )

    with pytest.raises(RuntimeError):
        await service.sync_repo(tenant_id=TENANT, repo_name="acme/pay-api", observed_at=NOW)

    cursor = await store.get_cursor(TENANT, "vcs", "repo:acme/pay-api")
    assert cursor.metadata["last_error_kind"] == "unexpected"
    assert LEAKY_MESSAGE not in str(cursor.metadata)


async def test_successful_sync_after_a_failure_reads_as_succeeded() -> None:
    store = InMemoryGraphStore()
    failed_at = NOW - timedelta(minutes=30)
    await store.record_cursor(
        TENANT,
        "vcs",
        "repo:acme/pay-api",
        SyncCursor(
            metadata={"last_failed_at": failed_at.isoformat(), "last_error_kind": "credentials"}
        ),
    )
    service = VcsReadSyncService(
        vcs_provider=FakeVcsProvider(repos=[Repo(tenant_id=TENANT, id="1", name="acme/pay-api")]),
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
    )

    await service.sync_repo(tenant_id=TENANT, repo_name="acme/pay-api", observed_at=NOW)

    report = await _service(store).status(TENANT, now=NOW)
    vcs = _source(report.sources, SyncSource.VCS)
    target = vcs.targets[0]
    assert target.last_outcome is SyncOutcome.SUCCEEDED
    assert target.last_error is None
    assert target.last_synced_at == NOW
    assert target.last_attempt_at == NOW


async def test_directory_sync_records_success_and_failure() -> None:
    store = InMemoryGraphStore()
    users = [DirectoryUser(tenant_id=TENANT, external_id="U1", display_name="Asha")]
    ok = DirectorySyncService(
        provider=_StaticDirectoryProvider(users),
        repository=FakeDirectoryUserRepository(),
        cursor_repository=store,
        clock=lambda: NOW - timedelta(hours=1),
    )
    failing = DirectorySyncService(
        provider=_FailingDirectoryProvider(),
        repository=FakeDirectoryUserRepository(),
        cursor_repository=store,
        clock=lambda: NOW,
    )

    result = await ok.sync(TENANT)
    with pytest.raises(ProviderConfigurationError):
        await failing.sync(TENANT)

    assert result.synced_count == 1
    cursor = await store.get_cursor(TENANT, "directory", "workspace")
    assert cursor.metadata["last_checked_at"] == (NOW - timedelta(hours=1)).isoformat()
    assert cursor.metadata["last_item_count"] == 1
    assert cursor.metadata["last_error_kind"] == "credentials"
    report = await _service(store).status(TENANT, now=NOW)
    directory = _source(report.sources, SyncSource.DIRECTORY)
    assert directory.health is SyncHealth.FAILING
    assert directory.last_error is SyncErrorKind.CREDENTIALS
    assert directory.target_origin is SyncTargetOrigin.WORKSPACE


async def test_directory_sync_without_cursor_repository_still_syncs() -> None:
    users = [DirectoryUser(tenant_id=TENANT, external_id="U1", display_name="Asha")]
    service = DirectorySyncService(
        provider=_StaticDirectoryProvider(users),
        repository=FakeDirectoryUserRepository(),
    )

    result = await service.sync(TENANT)

    assert result.synced_count == 1


def test_classify_sync_error_uses_closed_categories() -> None:
    assert classify_sync_error(ProviderConfigurationError("bad scope")) is SyncErrorKind.CREDENTIALS
    assert classify_sync_error(SecretNotFound("missing")) is SyncErrorKind.CREDENTIALS
    assert (
        classify_sync_error(ProviderUnavailable("calendar credentials are not configured"))
        is SyncErrorKind.CREDENTIALS
    )
    assert (
        classify_sync_error(ProviderUnavailable("VCS request failed"))
        is SyncErrorKind.PROVIDER_UNAVAILABLE
    )
    assert classify_sync_error(KeyError("x")) is SyncErrorKind.UNEXPECTED


# --- schedule thresholds ---------------------------------------------------


@pytest.mark.parametrize(
    ("cron", "expected"),
    [
        ("*/15 * * * *", timedelta(minutes=15)),
        ("0 * * * *", timedelta(hours=1)),
        ("0 */6 * * *", timedelta(hours=6)),
        ("0 8 * * *", timedelta(days=1)),
        ("0,30 * * * *", timedelta(minutes=30)),
        ("0 9-17 * * 1-5", timedelta(minutes=24 * 60 / 9)),
        ("0 * * * * *", timedelta(minutes=1)),
    ],
)
def test_cron_interval_estimates_common_sync_schedules(cron: str, expected: timedelta) -> None:
    assert cron_interval(cron) == expected


@pytest.mark.parametrize("cron", ["", "0 0 1 * *", "nonsense", "*/0 * * * *", "5-1 * * * *"])
def test_cron_interval_declines_unsupported_expressions(cron: str) -> None:
    assert cron_interval(cron) is None


def test_stale_after_is_three_missed_runs_with_a_floor() -> None:
    assert stale_after_for_cron("0 * * * *") == timedelta(hours=3)
    assert stale_after_for_cron("*/15 * * * *") == timedelta(hours=1)
    assert stale_after_for_cron("0 0 1 * *") == timedelta(days=1)


# --- status read model -----------------------------------------------------


async def test_status_reports_runtime_targets_and_their_health() -> None:
    store = await _configured_store()
    scope = await _issue_scope(store)
    await store.record_cursor(TENANT, "issue", scope, _ok_cursor(NOW - timedelta(minutes=12), 4))
    await store.record_cursor(
        TENANT, "vcs", "repo:acme/pay-api", _ok_cursor(NOW - timedelta(hours=5))
    )
    await store.append_fact(
        FactEvent(
            tenant_id=TENANT,
            source="issue",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="PAY-1"),
            payload={"key": "PAY-1"},
            observed_at=NOW - timedelta(hours=2),
            correlation_id="issue:demo:PAY-1",
        )
    )

    report = await _service(store).status(TENANT, now=NOW)

    assert report.generated_at == NOW
    assert [source.source for source in report.sources] == [
        SyncSource.ISSUE_TRACKER,
        SyncSource.VCS,
        SyncSource.CALENDAR,
        SyncSource.DIRECTORY,
    ]
    issue = _source(report.sources, SyncSource.ISSUE_TRACKER)
    assert issue.health is SyncHealth.HEALTHY
    assert issue.target_origin is SyncTargetOrigin.RUNTIME_CONFIG
    assert issue.provider == "jira"
    assert issue.simulated is False
    assert issue.schedule == "0 * * * *"
    assert issue.stale_after_minutes == 180
    assert issue.last_synced_at == NOW - timedelta(minutes=12)
    assert issue.newest_item_at == NOW - timedelta(hours=2)
    target = issue.targets[0]
    assert target.label == "Project Payments"
    assert target.detail == 'project = "PAY"'
    assert target.items_synced == 4
    assert target.last_outcome is SyncOutcome.SUCCEEDED

    vcs = _source(report.sources, SyncSource.VCS)
    # Every 15 minutes, so five hours without a sync is stale.
    assert vcs.health is SyncHealth.STALE
    assert vcs.targets[0].label == "acme/pay-api"
    assert vcs.targets[0].detail == "Linked to Payments"
    assert vcs.newest_item_at is None

    calendar = _source(report.sources, SyncSource.CALENDAR)
    assert calendar.health is SyncHealth.DISABLED
    assert calendar.sync_enabled is False
    assert calendar.targets == ()

    directory = _source(report.sources, SyncSource.DIRECTORY)
    assert directory.health is SyncHealth.NEVER_SYNCED
    assert directory.simulated is True


async def test_status_reports_failing_target_with_category_only() -> None:
    store = await _configured_store()
    scope = await _issue_scope(store)
    synced_at = NOW - timedelta(hours=1)
    await store.record_cursor(
        TENANT,
        "issue",
        scope,
        SyncCursor(
            metadata={
                "last_checked_at": synced_at.isoformat(),
                "last_failed_at": (NOW - timedelta(minutes=5)).isoformat(),
                "last_error_kind": "provider_unavailable",
            }
        ),
    )

    report = await _service(store).status(TENANT, now=NOW)

    issue = _source(report.sources, SyncSource.ISSUE_TRACKER)
    assert issue.health is SyncHealth.FAILING
    assert issue.last_error is SyncErrorKind.PROVIDER_UNAVAILABLE
    assert issue.last_synced_at == synced_at
    assert issue.last_attempt_at == NOW - timedelta(minutes=5)
    assert issue.targets[0].last_outcome is SyncOutcome.FAILED


async def test_status_without_targets_is_not_configured_and_keeps_adhoc_rows() -> None:
    store = InMemoryGraphStore()
    await store.record_cursor(TENANT, "issue", "project:OPS", _ok_cursor(NOW))
    # Other connectors share the cursor table; they are not data sources.
    await store.record_cursor(TENANT, "risk", "project:proj-1", SyncCursor(value="x"))

    report = await _service(store).status(TENANT, now=NOW)

    issue = _source(report.sources, SyncSource.ISSUE_TRACKER)
    assert issue.health is SyncHealth.NOT_CONFIGURED
    assert issue.target_origin is SyncTargetOrigin.NONE
    assert [(target.scope, target.configured) for target in issue.targets] == [
        ("project:OPS", False)
    ]
    assert issue.targets[0].label == "Project OPS"
    assert issue.last_synced_at == NOW
    assert _source(report.sources, SyncSource.VCS).targets == ()


async def test_status_falls_back_to_environment_targets() -> None:
    store = InMemoryGraphStore()
    legacy = SyncDispatchInput(
        tenant_id=TENANT, connector="issue", scope="project:OPS", payload={"project_key": "OPS"}
    )

    report = await _service(
        store, legacy_issue_targets=(legacy,), issue_tracker_provider="fake"
    ).status(TENANT, now=NOW)

    issue = _source(report.sources, SyncSource.ISSUE_TRACKER)
    assert issue.target_origin is SyncTargetOrigin.ENVIRONMENT
    assert issue.health is SyncHealth.NEVER_SYNCED
    assert issue.simulated is True
    assert [(target.scope, target.configured) for target in issue.targets] == [
        ("project:OPS", True)
    ]


async def test_status_reports_invalid_target_config_as_failing() -> None:
    store = await _configured_store()
    await store.upsert_node(
        Pod(
            tenant_id=TENANT,
            id="pod-checkout",
            name="Checkout",
            metadata={"github_repos": "acme/not-allowed"},
        )
    )
    await store.record_cursor(TENANT, "vcs", "repo:acme/pay-api", _ok_cursor(NOW))

    report = await _service(store).status(TENANT, now=NOW)

    for source in (SyncSource.ISSUE_TRACKER, SyncSource.VCS):
        status = _source(report.sources, source)
        assert status.health is SyncHealth.FAILING
        assert status.config_error is not None
        assert "acme/not-allowed" in status.config_error
    vcs = _source(report.sources, SyncSource.VCS)
    assert [(target.scope, target.configured) for target in vcs.targets] == [
        ("repo:acme/pay-api", True)
    ]


# --- API -------------------------------------------------------------------


def test_sync_status_endpoint_returns_sanitised_status_for_admin(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        registry = app.state.registry
        failing = IssueReadSyncService(
            issue_tracker=_FailingIssueTracker(),
            graph_repository=registry.graph_repository(),
            time_series_repository=registry.time_series_repository(),
            cursor_repository=registry.sync_cursor_repository(),
        )
        with pytest.raises(ProviderUnavailable):
            asyncio.run(
                failing.sync_query(
                    tenant_id=TENANT,
                    jql='project = "OPS"',
                    target_node_id="proj-ops",
                    target_node_kind=NodeKind.PROJECT,
                    cursor_scope="query:project:proj-ops:abc",
                )
            )
        response = client.get("/admin/ops/sync-status")

    assert response.status_code == 200
    body = response.json()
    assert [source["source"] for source in body["sources"]] == [
        "issue_tracker",
        "vcs",
        "calendar",
        "directory",
    ]
    issue = body["sources"][0]
    assert issue["provider"] == settings.issue_tracker_provider
    target = next(
        item for item in issue["targets"] if item["scope"] == "query:project:proj-ops:abc"
    )
    assert target["health"] == "failing"
    assert target["last_outcome"] == "failed"
    assert target["last_error"] == (
        "The provider could not be reached or returned a response the sync could not read."
    )
    assert body["sources"][2]["health"] == "disabled"
    # Neither the provider's text nor anything from it reaches the response.
    assert "sk-live" not in response.text
    assert "tracker.example.invalid" not in response.text


def test_sync_status_endpoint_is_admin_only(settings: Settings) -> None:
    for role in ("dev", "sm", "po", "mgr", "exec"):
        app = create_app(settings=settings.model_copy(update={"dev_principal_roles": role}))
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/admin/ops/sync-status")
        assert response.status_code == 403, role


# --- Postgres --------------------------------------------------------------


@dataclass
class _RecordingExecutor:
    rows: list[dict[str, object]]
    fetch_calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    async def execute(self, query: str, params: tuple[object, ...]) -> object:
        return object()

    async def fetch(self, query: str, params: tuple[object, ...]) -> list[dict[str, object]]:
        self.fetch_calls.append((query, params))
        return self.rows

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_RecordingExecutor]:
        yield self


async def test_postgres_list_cursors_reads_the_tenant_rows() -> None:
    executor = _RecordingExecutor(
        rows=[
            {
                "connector": "issue",
                "scope": "project:PAY",
                "cursor_value": "2026-03-02T11:00:00+00:00",
                "cursor_updated_at": NOW,
                "metadata": {"last_checked_at": NOW.isoformat(), "last_item_count": 2},
            }
        ]
    )
    repository = PostgresSyncCursorRepository(executor)

    records = await repository.list_cursors(TENANT)

    query, params = executor.fetch_calls[0]
    assert "FROM connector_sync_cursors" in query
    assert "WHERE tenant_id = %s" in query
    assert params == (TENANT,)
    assert [(record.connector, record.scope) for record in records] == [("issue", "project:PAY")]
    assert records[0].cursor.updated_at == NOW
    assert records[0].cursor.metadata["last_item_count"] == 2
