from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.risk_service import RiskService
from core.domain.risk import RiskProviderConfig
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry

AS_OF = "2026-07-01"


def _iso_days_ago(days: int) -> str:
    reference = datetime(2026, 7, 1, tzinfo=UTC) - timedelta(days=days)
    return reference.isoformat()


def _app_for_role(settings: Settings, role: str, store: InMemoryGraphStore) -> FastAPI:
    role_settings = settings.model_copy(update={"dev_principal_roles": role})
    registry = ServiceRegistry(role_settings, graph_store=store)
    return create_app(settings=role_settings, registry=registry)


def _seed_project_with_open_risk(admin_app: FastAPI, client: TestClient) -> None:
    client.post("/config/projects", json={"id": "proj-1", "name": "Project One"})
    client.post("/config/workstreams", json={"id": "ws-1", "name": "Workstream One"})
    client.post("/config/projects/proj-1/workstreams/ws-1")
    client.post(
        "/config/work-items",
        json={
            "id": "wi-1",
            "name": "Feature slice",
            "item_type": "feature",
            "state": "in_progress",
            "workstream_id": "ws-1",
            "metadata": {"created_at": _iso_days_ago(10)},
        },
    )

    registry = admin_app.state.registry
    service = RiskService(
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
        status_repository=registry.status_repository(),
        provider_config=RiskProviderConfig(default_no_pr_days=3, default_stale_days=30),
    )
    asyncio.run(service.assess_and_persist_project("demo", "proj-1", date.fromisoformat(AS_OF)))


def test_project_risks_visible_to_sm_and_denied_to_dev(settings: Settings) -> None:
    store = InMemoryGraphStore()
    admin_app = _app_for_role(settings, "admin", store)
    with TestClient(admin_app) as client:
        _seed_project_with_open_risk(admin_app, client)

    sm_app = _app_for_role(settings, "sm", store)
    with TestClient(sm_app) as client:
        response = client.get(f"/projects/proj-1/risks?as_of={AS_OF}")

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == "proj-1"
    assert len(body["risks"]) == 1
    assert body["risks"][0]["rule_id"] == "feature_no_pr"
    assert body["risks"][0]["evidence"]["identifier"] == "wi-1"

    dev_app = _app_for_role(settings, "dev", store)
    with TestClient(dev_app) as client:
        denied = client.get(f"/projects/proj-1/risks?as_of={AS_OF}")

    assert denied.status_code == 403


def test_portfolio_risks_visible_to_exec(settings: Settings) -> None:
    store = InMemoryGraphStore()
    admin_app = _app_for_role(settings, "admin", store)
    with TestClient(admin_app) as client:
        _seed_project_with_open_risk(admin_app, client)

    exec_app = _app_for_role(settings, "exec", store)
    with TestClient(exec_app) as client:
        response = client.get(f"/portfolio/risks?as_of={AS_OF}")

    assert response.status_code == 200
    body = response.json()
    assert len(body["risks"]) == 1


def test_project_risks_returns_404_for_unknown_project(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.get(f"/projects/does-not-exist/risks?as_of={AS_OF}")

    assert response.status_code == 404
