"""Shared pytest-bdd fixtures for the Mock Slack BDD suite.

These fixtures back the Gherkin scenarios under ``backend/tests/bdd/features``
that convert the manual ``mock-slack-e2e-testing.md`` matrix (``MS-E2E-001``
through ``MS-E2E-045``) into automated pytest-bdd scenarios.

Most scenarios run entirely in-memory through the real FastAPI app
(``create_app``) configured with the ``mock_slack`` chat/directory providers
and ``fake`` read providers, mirroring the existing unit/integration test
conventions in ``backend/tests/unit`` and ``backend/tests/integration``.

A small number of scenarios need real backing infrastructure:

- ``MS-E2E-044`` needs a real Redis instance to prove the simulator store
  survives an application "restart" (a fresh registry attached to the same
  Redis instance). See ``redis_container``.
- The pure frontend/browser scenarios under ``features/ui`` need a live
  backend + Vite dev server driven by Playwright. See ``ui_stack`` and
  ``browser_page``. Those fixtures skip gracefully (matching the existing
  ``backend/tests/integration`` docker-skip convention) when Docker/Node/
  Playwright browsers are not available.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.domain.workflows import CheckinScheduleConfig as _CheckinScheduleConfig
from core.domain.workflows import (
    ConversationPurgeScheduleConfig,
    DeveloperCheckinDispatch,
    ScheduleBootstrapResult,
    SyncDispatchInput,
    SyncScheduleConfig,
)
from infra.registry import ServiceRegistry
from infra.workflows import daily_checkin

ROOT = Path(__file__).resolve().parents[3]
SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="


class InProcessWorkflowScheduler:
    """Runs check-in dispatch synchronously through the real ``daily_checkin``
    activity instead of DBOS/Temporal, so the admin dispatch HTTP route
    exercises the same eligibility/idempotency logic as production without
    needing real workflow infrastructure in the BDD suite."""

    def __init__(self, schedule_id: str = "bdd-schedule") -> None:
        self.schedule_id = schedule_id

    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=self.schedule_id, status="ready")

    async def ensure_checkin_fanout_schedule(
        self, config: _CheckinScheduleConfig
    ) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")

    async def ensure_conversation_purge_schedule(
        self, config: ConversationPurgeScheduleConfig
    ) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")

    async def ensure_sync_schedules(
        self, configs: list[SyncScheduleConfig]
    ) -> list[ScheduleBootstrapResult]:
        return [
            ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")
            for config in configs
        ]

    async def dispatch_developer_checkin(self, input: DeveloperCheckinDispatch) -> str:
        result = await daily_checkin.start_daily_checkin_activity(
            daily_checkin.DailyCheckinInput(
                tenant_id=input.tenant_id,
                developer_id=input.developer_id,
                developer_name=input.developer_name,
                chat_external_id=input.chat_external_id,
                checkin_date=input.checkin_date,
            )
        )
        return f"in-process-{result.correlation_id}"

    async def dispatch_sync(self, input: SyncDispatchInput) -> str:
        return f"in-process-sync-{input.connector}-{input.scope}"


class WorldRegistry(ServiceRegistry):
    """Default registry for BDD scenarios: real providers/repositories, but
    dispatch runs in-process (see ``InProcessWorkflowScheduler``) instead of
    requiring a live DBOS/Temporal engine."""

    def workflow_scheduler(self) -> InProcessWorkflowScheduler:
        return InProcessWorkflowScheduler()


def mock_slack_settings(**overrides: object) -> Settings:
    """Base settings for the local admin Mock Slack E2E stack (see MS-E2E-001)."""
    values: dict[str, object] = {
        "secret_key": SECRET_KEY,
        "runtime_mode": "memory",
        "environment": "local",
        "tenant_id": "demo",
        "chat_provider": "mock_slack",
        "directory_provider": "mock_slack",
        "issue_tracker_provider": "fake",
        "vcs_provider": "fake",
        "calendar_provider": "fake",
        "llm_provider": "fake",
        "workflow_provider": "fake",
        "chat_simulator_enabled": True,
        "checkin_fanout_cron": "*/15 * * * *",
        "dev_principal_subject": "dev-admin",
        "dev_principal_roles": "admin",
        **overrides,
    }
    return Settings(_env_file=None, **values)


@dataclass
class World:
    """Mutable per-scenario state shared across Given/When/Then steps."""

    settings: Settings | None = None
    app: FastAPI | None = None
    client: TestClient | None = None
    response: httpx.Response | None = None
    stash: dict[str, Any] = field(default_factory=dict)
    _exit_stack: ExitStack = field(default_factory=ExitStack)
    _monkeypatch: pytest.MonkeyPatch = field(default_factory=pytest.MonkeyPatch)

    def start_app(
        self,
        *,
        registry: ServiceRegistry | None = None,
        settings: Settings | None = None,
        **settings_overrides: object,
    ) -> TestClient:
        self.settings = settings or mock_slack_settings(**settings_overrides)
        resolved_registry = registry or WorldRegistry(self.settings)
        self.app = create_app(settings=self.settings, registry=resolved_registry)
        # Route the real daily_checkin activity's registry lookup back to this
        # app's shared in-memory registry, so dispatches made through the HTTP
        # admin API and direct assertions observe the same state.
        self._monkeypatch.setattr(
            daily_checkin, "_service_registry", lambda: self.app.state.registry
        )
        self.client = self._exit_stack.enter_context(TestClient(self.app))
        return self.client

    def registry(self) -> ServiceRegistry:
        assert self.app is not None, "app not started yet"
        return self.app.state.registry

    def close(self) -> None:
        self._exit_stack.close()
        self._monkeypatch.undo()


@pytest.fixture
def world() -> Iterator[World]:
    instance = World()
    yield instance
    instance.close()


# ---------------------------------------------------------------------------
# Redis-backed persistence fixture (MS-E2E-044 only)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_container() -> Iterator[str]:
    """A real, throwaway Redis instance used only for restart-persistence checks."""
    try:
        from testcontainers.redis import RedisContainer
    except Exception as exc:  # pragma: no cover - depends on optional docker env
        pytest.skip(f"testcontainers redis module unavailable: {exc}")

    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:
        pytest.skip(f"docker daemon unavailable: {exc}")

    with RedisContainer("redis:7") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


# ---------------------------------------------------------------------------
# Playwright-backed UI stack (frontend-only scenarios under features/ui)
# ---------------------------------------------------------------------------

UI_BDD_ENV_VAR = "OPENPROGRAM_RUN_UI_BDD"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class UiStack:
    backend_url: str
    frontend_url: str


def _run_uvicorn_in_thread(app: FastAPI, host: str, port: int) -> tuple[object, threading.Thread]:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


def _wait_for_http(url: str, *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    return False


@pytest.fixture(scope="session")
def ui_stack() -> Iterator[UiStack]:
    if os.getenv(UI_BDD_ENV_VAR) != "1":
        pytest.skip(f"set {UI_BDD_ENV_VAR}=1 or run make ui-bdd to exercise browser scenarios")

    backend_port = _free_port()
    backend_host = "127.0.0.1"
    backend_url = f"http://{backend_host}:{backend_port}"
    frontend_port = _free_port()
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    # The dev-server port is picked dynamically per test run, so the backend's
    # CORS allowlist must be built to match before the app (and its
    # middleware) are constructed.
    settings = mock_slack_settings(cors_origins=(frontend_url,))
    registry = WorldRegistry(settings)
    app = create_app(settings=settings, registry=registry)
    monkeypatch_session = pytest.MonkeyPatch()
    monkeypatch_session.setattr(daily_checkin, "_service_registry", lambda: registry)
    server, _thread = _run_uvicorn_in_thread(app, backend_host, backend_port)
    if not _wait_for_http(f"{backend_url}/health", timeout_seconds=20):
        server.should_exit = True
        pytest.skip("backend server for UI BDD scenarios did not become healthy in time")

    node_modules = ROOT / "frontend" / "node_modules"
    if not node_modules.exists():
        server.should_exit = True
        pytest.skip("frontend/node_modules missing; run `npm install` in frontend/ first")

    env = {
        **os.environ,
        "VITE_API_BASE_URL": backend_url,
    }
    process = subprocess.Popen(  # noqa: S603
        [
            "npm",
            "run",
            "dev",
            "--",
            "--port",
            str(frontend_port),
            "--strictPort",
            "--host",
            "127.0.0.1",
        ],
        cwd=str(ROOT / "frontend"),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_for_http(frontend_url, timeout_seconds=30):
            pytest.skip("frontend dev server for UI BDD scenarios did not start in time")
        yield UiStack(backend_url=backend_url, frontend_url=frontend_url)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        server.should_exit = True
        monkeypatch_session.undo()


@pytest.fixture
def browser_page(ui_stack: UiStack) -> Iterator[Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional dependency
        pytest.skip(f"playwright unavailable: {exc}")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"chromium browser unavailable (run `playwright install chromium`): {exc}")
        try:
            page = browser.new_page(base_url=ui_stack.frontend_url)
            yield page
        finally:
            browser.close()


def _reset_backend_state(ui_stack: UiStack) -> None:
    httpx.delete(f"{ui_stack.backend_url}/test/chat-simulator/state", timeout=5.0)


@pytest.fixture(autouse=False)
def clean_ui_stack(ui_stack: UiStack) -> Iterator[UiStack]:
    _reset_backend_state(ui_stack)
    yield ui_stack
    _reset_backend_state(ui_stack)
