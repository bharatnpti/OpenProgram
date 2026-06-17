from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.application.agents.status_agent import StatusAgentNode
from core.application.sync_services import SyncRunResult
from core.domain.integrations import SyncCursor
from core.domain.status import CheckIn
from core.domain.workflows import HeartbeatInput, record_heartbeat
from infra.adapters.workflows.fake import FakeWorkflowScheduler
from infra.workflows import daily_checkin, jira_sync
from tests.contract.fakes import FakeLlmProvider


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
        )
    )

    assert result.already_recorded is True
    assert result.asked_at == asked_at.isoformat()
    assert registry.collector_called is False
    assert registry.closed is True


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

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> CheckIn | None:
        if self._checkin.tenant_id == tenant_id and self._checkin.correlation_id == correlation_id:
            return self._checkin
        return None


class _ExplodingStatusCollector:
    async def start_checkin(self, **kwargs: object) -> CheckIn:
        raise AssertionError("start_checkin should not be called for an existing correlation")


class _ExistingCheckinRegistry:
    def __init__(self, checkin: CheckIn) -> None:
        self.closed = False
        self.collector_called = False
        self._repository = _ExistingCheckinRepository(checkin)
        self._collector = _ExplodingStatusCollector()

    def status_repository(self) -> _ExistingCheckinRepository:
        return self._repository

    def status_collector(self) -> _ExplodingStatusCollector:
        self.collector_called = True
        return self._collector

    async def close(self) -> None:
        self.closed = True
