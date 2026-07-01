from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from config.settings import Settings
from core.domain.graph import EdgeKind, GraphEdge, Project, WorkItem, Workstream
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from infra.workflows import risk_assessment

TENANT = "demo"


def _settings(**overrides: object) -> Settings:
    settings_factory = cast(Callable[..., Settings], Settings)
    return settings_factory(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        runtime_mode="memory",
        **overrides,
    )


async def _seed_project(store: InMemoryGraphStore, *, project_metadata: dict) -> None:
    await store.upsert_node(
        Project(tenant_id=TENANT, id="proj-1", name="Project One", metadata=project_metadata)
    )
    await store.upsert_node(Workstream(tenant_id=TENANT, id="ws-1", name="Workstream One"))
    await store.add_edge(
        GraphEdge(
            tenant_id=TENANT, from_node_id="proj-1", to_node_id="ws-1", kind=EdgeKind.CONTAINS
        )
    )
    created_at = (datetime(2026, 7, 1, tzinfo=UTC) - timedelta(days=10)).isoformat()
    await store.upsert_node(
        WorkItem(
            tenant_id=TENANT,
            id="wi-1",
            name="Feature slice",
            metadata={
                "item_type": "feature",
                "state": "in_progress",
                "created_at": created_at,
            },
        )
    )
    await store.add_edge(
        GraphEdge(tenant_id=TENANT, from_node_id="ws-1", to_node_id="wi-1", kind=EdgeKind.CONTAINS)
    )


async def test_assessment_skips_project_before_configured_local_run_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await _seed_project(
        store, project_metadata={"risk_run_local_time": "18:00", "risk_run_timezone": "UTC"}
    )
    settings = _settings()
    registry = ServiceRegistry(settings, graph_store=store)
    monkeypatch.setattr(risk_assessment, "_service_registry", lambda: registry)

    result = await risk_assessment.run_risk_assessment_activity(
        risk_assessment.RiskAssessmentInput(
            tenant_id=TENANT,
            observed_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC).isoformat(),
        )
    )

    assert result.projects_evaluated == 1
    assert result.projects_assessed == 0
    assert result.newly_opened == 0


async def test_assessment_runs_once_past_configured_local_run_time_and_is_idempotent_same_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await _seed_project(
        store, project_metadata={"risk_run_local_time": "18:00", "risk_run_timezone": "UTC"}
    )
    settings = _settings(risk_default_no_pr_days=3, risk_default_stale_days=30)
    registry = ServiceRegistry(settings, graph_store=store)
    monkeypatch.setattr(risk_assessment, "_service_registry", lambda: registry)

    first = await risk_assessment.run_risk_assessment_activity(
        risk_assessment.RiskAssessmentInput(
            tenant_id=TENANT,
            observed_at=datetime(2026, 7, 1, 19, 0, tzinfo=UTC).isoformat(),
        )
    )
    second = await risk_assessment.run_risk_assessment_activity(
        risk_assessment.RiskAssessmentInput(
            tenant_id=TENANT,
            observed_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC).isoformat(),
        )
    )

    assert first.projects_assessed == 1
    assert first.newly_opened == 1
    assert second.projects_assessed == 0

    cursor = await store.get_cursor(TENANT, "risk", "project:proj-1")
    assert cursor.value == "2026-07-01"


async def test_assessment_runs_again_on_a_new_local_day(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryGraphStore()
    await _seed_project(
        store, project_metadata={"risk_run_local_time": "18:00", "risk_run_timezone": "UTC"}
    )
    settings = _settings()
    registry = ServiceRegistry(settings, graph_store=store)
    monkeypatch.setattr(risk_assessment, "_service_registry", lambda: registry)

    await risk_assessment.run_risk_assessment_activity(
        risk_assessment.RiskAssessmentInput(
            tenant_id=TENANT,
            observed_at=datetime(2026, 7, 1, 19, 0, tzinfo=UTC).isoformat(),
        )
    )
    next_day = await risk_assessment.run_risk_assessment_activity(
        risk_assessment.RiskAssessmentInput(
            tenant_id=TENANT,
            observed_at=datetime(2026, 7, 2, 19, 0, tzinfo=UTC).isoformat(),
        )
    )

    assert next_day.projects_assessed == 1


async def test_assessment_uses_global_default_run_time_when_project_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await _seed_project(store, project_metadata={})
    settings = _settings(risk_run_default_local_time="09:00")
    registry = ServiceRegistry(settings, graph_store=store)
    monkeypatch.setattr(risk_assessment, "_service_registry", lambda: registry)

    before = await risk_assessment.run_risk_assessment_activity(
        risk_assessment.RiskAssessmentInput(
            tenant_id=TENANT,
            observed_at=datetime(2026, 7, 1, 8, 0, tzinfo=UTC).isoformat(),
        )
    )
    after = await risk_assessment.run_risk_assessment_activity(
        risk_assessment.RiskAssessmentInput(
            tenant_id=TENANT,
            observed_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC).isoformat(),
        )
    )

    assert before.projects_assessed == 0
    assert after.projects_assessed == 1
