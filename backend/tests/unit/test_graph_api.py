from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry


def _app_for_role(settings: Settings, role: str, store: InMemoryGraphStore) -> FastAPI:
    role_settings = settings.model_copy(update={"dev_principal_roles": role})
    registry = ServiceRegistry(role_settings, graph_store=store)
    return create_app(settings=role_settings, registry=registry)


def test_program_tree_denies_with_403_not_500(settings: Settings) -> None:
    """A role without aggregate read must be told 'forbidden', not 'server error'.

    `AuthorizationPolicy.ensure` raises AuthorizationDenied, which is not an
    HTTPException. Uncaught it escaped the handler as a 500, so a developer
    opening the tree was told the server had failed rather than that the tree
    was not theirs to read -- and every such request logged a traceback,
    burying real faults in routine denials.
    """
    store = InMemoryGraphStore()

    admin_app = _app_for_role(settings, "admin", store)
    with TestClient(admin_app) as admin_client:
        admin_client.post("/config/programs", json={"id": "program-1", "name": "Program One"})
        allowed = admin_client.get("/graph/programs/program-1/tree")
    assert allowed.status_code == 200

    dev_app = _app_for_role(settings, "dev", store)
    with TestClient(dev_app) as dev_client:
        denied = dev_client.get("/graph/programs/program-1/tree")

    assert denied.status_code == 403
    assert "read_team_aggregate" in denied.json()["detail"]


def test_program_tree_of_an_unknown_program_is_404_not_500(settings: Settings) -> None:
    """A program the tenant does not have is 'not found', not a server error.

    The graph store raises GraphNotFound, which is not an HTTPException: left
    uncaught it escaped the handler as a 500 with a traceback, so a stale link
    or a mistyped id told the caller the server had failed.
    """
    store = InMemoryGraphStore()
    app = _app_for_role(settings, "admin", store)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.post("/config/programs", json={"id": "program-1", "name": "Program One"})
        known = client.get("/graph/programs/program-1/tree")
        unknown = client.get("/graph/programs/no-such-program/tree")

    assert known.status_code == 200
    assert unknown.status_code == 404
    assert "no-such-program" in unknown.json()["detail"]
    assert "not found" in unknown.json()["detail"]
