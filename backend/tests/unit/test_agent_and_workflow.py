from __future__ import annotations

from datetime import UTC, datetime

from core.application.agents.status_agent import StatusAgentNode
from core.domain.workflows import HeartbeatInput, record_heartbeat
from infra.adapters.workflows.fake import FakeWorkflowScheduler
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
