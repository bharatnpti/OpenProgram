from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from core.application.agents.status_agent import StatusAgentNode
from core.application.status_collector import StatusCollector
from core.application.sync_services import SyncRunResult
from core.domain.integrations import SyncCursor, UserRef
from core.domain.status import CheckIn, CheckInScheduleRun
from core.domain.workflows import HeartbeatInput, record_heartbeat
from infra.adapters.workflows import dbos as dbos_workflows
from infra.adapters.workflows import temporal as temporal_workflows
from infra.adapters.workflows.fake import FakeWorkflowScheduler
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.workflows import daily_checkin, jira_sync, nudge
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker, FakeLlmProvider


async def test_status_agent_calls_llm_provider() -> None:
    node = StatusAgentNode(FakeLlmProvider(), model="test-model")
    result = await node(
        {
            "tenant_id": "demo",
            "developer_name": "Asha",
            "context": "API shell is complete; graph tests are blocked.",
            "correlation_id": "corr-1",
        }
    )
    assert result["trace_id"] == "trace-fake"
    assert result["summary"].startswith("summary:")


async def test_status_agent_langgraph_wrapper_calls_llm_provider() -> None:
    node = StatusAgentNode(FakeLlmProvider(), model="test-model")
    result = await node.graph().ainvoke(
        {
            "tenant_id": "demo",
            "developer_name": "Asha",
            "context": "Graph wrapper smoke.",
            "correlation_id": "corr-graph",
        }
    )
    assert result["trace_id"] == "trace-fake"
    assert result["summary"].startswith("summary:")


def test_heartbeat_logic_is_retry_safe_shape() -> None:
    recorded_at = datetime(2026, 1, 1, tzinfo=UTC)
    result = record_heartbeat(
        HeartbeatInput(tenant_id="demo", heartbeat_id="heartbeat-1"),
        recorded_at=recorded_at,
    )
    assert result.tenant_id == "demo"
    assert result.heartbeat_id == "heartbeat-1"
    assert result.status == "ok"
    assert result.recorded_at == recorded_at.isoformat()


async def test_fake_workflow_scheduler_returns_deterministic_result() -> None:
    result = await FakeWorkflowScheduler(schedule_id="heartbeat-test").ensure_heartbeat_schedule()
    assert result.schedule_id == "heartbeat-test"
    assert result.status == "ready"


async def test_temporal_connect_retries_until_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    from temporalio.client import Client

    attempts: list[str] = []
    expected_client = object()

    async def connect(target: str) -> object:
        attempts.append(target)
        if len(attempts) < 3:
            raise RuntimeError("Temporal is starting")
        return expected_client

    monkeypatch.setattr(Client, "connect", connect)

    client = await temporal_workflows._connect_temporal(
        "temporal:7233",
        attempts=3,
        delay_seconds=0,
    )

    assert client is expected_client
    assert attempts == ["temporal:7233", "temporal:7233", "temporal:7233"]


async def test_dbos_readiness_launches_once_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class Connection:
        async def execute(self, query: str) -> None:
            calls.append(("execute", query))

        async def close(self) -> None:
            calls.append(("close", None))

    async def connect(url: str) -> Connection:
        calls.append(("connect", url))
        return Connection()

    def configure(config: dbos_workflows.DbosRuntimeConfig) -> None:
        calls.append(("configure", config))

    def launch() -> None:
        calls.append(("launch", None))

    def destroy() -> None:
        calls.append(("destroy", None))

    monkeypatch.setattr(dbos_workflows.psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(dbos_workflows, "configure_dbos_runtime", configure)
    monkeypatch.setattr(dbos_workflows.DBOS, "launch", launch)
    monkeypatch.setattr(dbos_workflows, "destroy_dbos_runtime", destroy)

    probe = dbos_workflows.DbosWorkflowReadinessProbe(
        app_name="pulseops-test",
        system_database_url="postgresql://pulseops:pulseops@localhost:5432/pulseops",
    )

    assert await probe.check() is True
    assert await probe.check() is True
    assert calls == [
        ("connect", "postgresql://pulseops:pulseops@localhost:5432/pulseops"),
        ("execute", "SELECT 1"),
        ("close", None),
        (
            "configure",
            dbos_workflows.DbosRuntimeConfig(
                app_name="pulseops-test",
                system_database_url="postgresql://pulseops:pulseops@localhost:5432/pulseops",
            ),
        ),
        ("launch", None),
    ]


async def test_jira_sync_activity_returns_json_native_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _StubRegistry()
    monkeypatch.setattr(jira_sync, "_service_registry", lambda: registry)

    result = await jira_sync.sync_jira_project_activity(
        jira_sync.JiraSyncInput(
            tenant_id="demo",
            project_key="PO",
            observed_at="2026-01-10T09:00:00+00:00",
        )
    )

    assert result.connector == "issue"
    assert result.scope == "project:PO"
    assert result.items_synced == 1
    assert result.cursor_updated_at == "2026-01-10T09:00:00+00:00"
    assert result.cursor_metadata == {"last_item_count": 1}
    assert registry.closed is True


async def test_daily_checkin_activity_is_idempotent_for_existing_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    registry = _ExistingCheckinRegistry(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    monkeypatch.setattr(daily_checkin, "_service_registry", lambda: registry)

    result = await daily_checkin.start_daily_checkin_activity(
        daily_checkin.DailyCheckinInput(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            checkin_date="2026-01-09",
        )
    )

    assert result.already_recorded is True
    assert result.asked_at == asked_at.isoformat()
    assert registry.collector_called is False
    assert registry.closed is True


async def test_daily_checkin_activity_skips_weekends_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _DailyCheckinRegistry(available=True)
    monkeypatch.setattr(daily_checkin, "_service_registry", lambda: registry)
    payload = daily_checkin.DailyCheckinInput(
        tenant_id="demo",
        developer_id="dev-1",
        correlation_id="corr-weekend",
        checkin_date="2026-01-10",
    )

    first = await daily_checkin.start_daily_checkin_activity(payload)
    second = await daily_checkin.start_daily_checkin_activity(payload)

    assert first.status == "skipped_weekend"
    assert first.skipped_reason == "check-in preference excludes this weekday"
    assert second.status == "skipped_weekend"
    assert second.already_recorded is True
    assert len(registry.repository.schedule_runs) == 1
    assert registry.collector_called is False


async def test_daily_checkin_activity_skips_calendar_unavailable_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _DailyCheckinRegistry(available=False)
    monkeypatch.setattr(daily_checkin, "_service_registry", lambda: registry)

    result = await daily_checkin.start_daily_checkin_activity(
        daily_checkin.DailyCheckinInput(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-pto",
            checkin_date="2026-01-12",
        )
    )

    assert result.status == "skipped_unavailable"
    assert result.skipped_reason == "calendar marks developer unavailable"
    assert registry.collector_called is False


async def test_nudge_activities_send_once_then_close_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-nudge",
            asked_at=datetime(2026, 1, 12, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    chat = FakeChatProvider()
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=FakeLlmProvider(),
        status_repository=store,
        model="test-model",
    )
    registry = _NudgeRegistry(store, collector)
    monkeypatch.setattr(nudge, "_service_registry", lambda: registry)
    payload = nudge.NudgeInput(
        tenant_id="demo",
        correlation_id="corr-nudge",
        as_of="2026-01-12",
        chat_external_id="U123",
    )

    first = await nudge.send_checkin_nudge_activity(payload)
    second = await nudge.send_checkin_nudge_activity(payload)
    closed = await nudge.close_checkin_non_response_activity(payload)

    assert first.status == "nudged"
    assert second.status == "already_nudged"
    assert first.nudge_message_id == "msg-U123-1"
    assert second.nudge_message_id == "msg-U123-1"
    assert len(chat.sent) == 1
    assert closed.status == "closed"
    assert closed.terminal_source == "unknown"


class _StubIssueSyncService:
    async def sync_project(
        self,
        *,
        tenant_id: str,
        project_key: str,
        container_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> SyncRunResult:
        return SyncRunResult(
            connector="issue",
            scope=f"project:{project_key}",
            items_synced=1,
            cursor=SyncCursor(
                value="cursor-1",
                updated_at=observed_at,
                metadata={"last_item_count": 1},
            ),
        )


class _StubRegistry:
    def __init__(self) -> None:
        self.closed = False
        self._service = _StubIssueSyncService()

    def issue_read_sync_service(self) -> _StubIssueSyncService:
        return self._service

    async def close(self) -> None:
        self.closed = True


class _ExistingCheckinRepository:
    def __init__(self, checkin: CheckIn) -> None:
        self._checkin = checkin
        self.schedule_runs: list[CheckInScheduleRun] = []

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> CheckIn | None:
        if self._checkin.tenant_id == tenant_id and self._checkin.correlation_id == correlation_id:
            return self._checkin
        return None

    async def checkin_preference_for(self, tenant_id: str, developer_id: str) -> None:
        return None

    async def checkin_schedule_run(
        self, tenant_id: str, developer_id: str, checkin_date: date
    ) -> None:
        return None

    async def record_checkin_schedule_run(self, run: CheckInScheduleRun) -> None:
        self.schedule_runs.append(run)


class _DailyCheckinRepository:
    def __init__(self) -> None:
        self.schedule_runs: dict[tuple[str, str, date], CheckInScheduleRun] = {}

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> None:
        return None

    async def checkin_preference_for(self, tenant_id: str, developer_id: str) -> None:
        return None

    async def checkin_schedule_run(
        self, tenant_id: str, developer_id: str, checkin_date: date
    ) -> CheckInScheduleRun | None:
        return self.schedule_runs.get((tenant_id, developer_id, checkin_date))

    async def record_checkin_schedule_run(self, run: CheckInScheduleRun) -> None:
        self.schedule_runs[(run.tenant_id, run.developer_id, run.checkin_date)] = run


class _ExplodingStatusCollector:
    async def start_checkin(self, **kwargs: object) -> CheckIn:
        raise AssertionError("start_checkin should not be called for an existing correlation")


class _ExistingCheckinRegistry:
    def __init__(self, checkin: CheckIn) -> None:
        self.closed = False
        self.collector_called = False
        self.settings = _WorkflowSettings()
        self._repository = _ExistingCheckinRepository(checkin)
        self._collector = _ExplodingStatusCollector()

    def status_repository(self) -> _ExistingCheckinRepository:
        return self._repository

    def status_collector(self) -> _ExplodingStatusCollector:
        self.collector_called = True
        return self._collector

    def availability_service(self) -> _AvailableService:
        return _AvailableService()

    async def close(self) -> None:
        self.closed = True


class _DailyCheckinRegistry:
    def __init__(self, *, available: bool) -> None:
        self.closed = False
        self.collector_called = False
        self.settings = _WorkflowSettings()
        self.repository = _DailyCheckinRepository()
        self._availability = _AvailableService(available=available)
        self._collector = _ExplodingStatusCollector()

    def status_repository(self) -> _DailyCheckinRepository:
        return self.repository

    def status_collector(self) -> _ExplodingStatusCollector:
        self.collector_called = True
        return self._collector

    def availability_service(self) -> _AvailableService:
        return self._availability

    async def close(self) -> None:
        self.closed = True


class _NudgeRegistry:
    def __init__(self, store: InMemoryGraphStore, collector: StatusCollector) -> None:
        self.closed = False
        self._store = store
        self._collector = collector

    def status_repository(self) -> InMemoryGraphStore:
        return self._store

    def status_collector(self) -> StatusCollector:
        return self._collector

    async def close(self) -> None:
        self.closed = True


class _AvailableService:
    def __init__(self, *, available: bool = True) -> None:
        self._available = available

    async def availability_for(
        self,
        user: UserRef,
        as_of: date,
        *,
        default_timezone: str = "UTC",
    ) -> _AvailabilityResult:
        return _AvailabilityResult(available=self._available, timezone=default_timezone)


class _AvailabilityResult:
    def __init__(self, *, available: bool, timezone: str) -> None:
        self.available = available
        self.timezone = timezone


class _WorkflowSettings:
    tenant_default_timezone = "UTC"
    checkin_reply_wait_seconds = 0
    checkin_final_reply_wait_seconds = 0
