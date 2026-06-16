from __future__ import annotations

from datetime import datetime

from core.application.agents.status_agent import StatusAgentNode
from infra.workflows.heartbeat import HeartbeatInput, record_heartbeat_activity
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


async def test_heartbeat_activity_is_retry_safe_shape() -> None:
    result = await record_heartbeat_activity(
        HeartbeatInput(tenant_id="demo", heartbeat_id="heartbeat-1")
    )
    assert result.tenant_id == "demo"
    assert result.heartbeat_id == "heartbeat-1"
    assert result.status == "ok"
    assert datetime.fromisoformat(result.recorded_at).tzinfo is not None
