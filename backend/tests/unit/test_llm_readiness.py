from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import cast

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from api.main import create_app
from config.settings import Settings
from core.ports.readiness import ReadinessProbe, ReadinessReport
from infra import registry as registry_module
from infra.adapters import catalog
from infra.adapters.llm import readiness as llm_readiness
from infra.adapters.llm.readiness import LlmEndpointReadinessProbe, LlmReadinessHistory
from infra.adapters.readiness import StaticReadinessProbe
from infra.registry import ServiceRegistry

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="
API_KEY = "sk-test-readiness-key-must-never-leak"
MODEL_LIST = {"object": "list", "data": [{"id": "gpt-4.1", "object": "model"}]}
MOCK_LLM_PATH = Path(__file__).resolve().parents[3] / "scripts" / "mock_llm.py"
FLAKY_URL = "https://llm.example.test"
TIMED_OUT = ReadinessReport(ready=False, detail="unreachable: no response within 2.5s")
MISSED_TIMEOUT = (
    "unconfirmed: this probe failed (unreachable: no response within 2.5s) after"
    " a success; one more failure in a row reports degraded"
)


@pytest.fixture(autouse=True)
def _forget_logged_outcomes() -> Iterator[None]:
    llm_readiness._last_outcome.clear()
    yield
    llm_readiness._last_outcome.clear()


def _probe(
    base_url: str, api_key: str | None = API_KEY, **kwargs: object
) -> LlmEndpointReadinessProbe:
    factory = cast(Callable[..., LlmEndpointReadinessProbe], LlmEndpointReadinessProbe)
    return factory(base_url=base_url, api_key=api_key, **kwargs)


def _load_mock_llm() -> ModuleType:
    # scripts/ is not on the test import path, so load the mock by file.
    spec = importlib.util.spec_from_file_location("openprogram_test_mock_llm", MOCK_LLM_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@respx.mock
async def test_litellm_gateway_is_ready_when_it_lists_models_for_the_key() -> None:
    models = respx.get("https://litellm.test/v1/models").mock(
        return_value=httpx.Response(200, json=MODEL_LIST)
    )

    report = await _probe("https://litellm.test").report()

    assert report == ReadinessReport(ready=True)
    assert models.calls.last.request.headers["authorization"] == f"Bearer {API_KEY}"


@respx.mock
async def test_gateway_without_a_model_list_falls_back_to_litellm_readiness() -> None:
    respx.get("https://gateway.test/v1/models").mock(return_value=httpx.Response(404))
    gateway = respx.get("https://gateway.test/health/readiness").mock(
        return_value=httpx.Response(200, json={"status": "healthy"})
    )

    report = await _probe("https://gateway.test").report()

    assert report.ready is True
    # The fallback route needs no key, so none is sent to it.
    assert "authorization" not in gateway.calls.last.request.headers


@respx.mock
async def test_plain_openai_endpoint_is_ready_without_spending_tokens() -> None:
    models = respx.get("https://api.openai.test/v1/models").mock(
        return_value=httpx.Response(200, json=MODEL_LIST)
    )
    completions = respx.post("https://api.openai.test/v1/chat/completions")
    gateway_only = respx.get("https://api.openai.test/health/readiness").mock(
        return_value=httpx.Response(404)
    )

    report = await _probe("https://api.openai.test").report()

    assert report == ReadinessReport(ready=True)
    assert models.called
    assert not completions.called
    assert not gateway_only.called


async def test_local_mock_llm_answers_the_probe() -> None:
    mock_llm = _load_mock_llm()
    transport = httpx.ASGITransport(app=mock_llm.app)

    report = await _probe("http://mock-llm:8080", transport=transport).report()

    assert report == ReadinessReport(ready=True)


@respx.mock
async def test_rejected_key_is_unauthorized_not_unreachable() -> None:
    respx.get("https://api.openai.test/v1/models").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": {
                    "message": f"Incorrect API key provided: {API_KEY}.",
                    "code": "invalid_api_key",
                }
            },
        )
    )

    with capture_logs() as logs:
        report = await _probe("https://api.openai.test").report()

    assert report.ready is False
    assert report.detail == "unauthorized: the endpoint rejected the API key (HTTP 401)"
    assert API_KEY not in repr(logs)
    assert API_KEY not in repr(report)


@respx.mock
async def test_missing_key_is_reported_as_missing() -> None:
    respx.get("https://litellm.test/v1/models").mock(return_value=httpx.Response(401))

    report = await _probe("https://litellm.test", api_key=None).report()

    assert report.ready is False
    assert report.detail is not None
    assert report.detail.startswith("unauthorized: the endpoint requires an API key")


@respx.mock
async def test_key_that_may_not_list_models_still_counts_as_ready() -> None:
    # A restricted OpenAI key: authenticated, but without the model-list scope.
    respx.get("https://api.openai.test/v1/models").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": {
                    "message": "You have insufficient permissions for this operation. "
                    "Missing scopes: api.model.read."
                }
            },
        )
    )

    report = await _probe("https://api.openai.test").report()

    assert report.ready is True
    assert report.detail is not None
    assert report.detail.startswith("authenticated:")


@respx.mock
async def test_connect_timeout_is_unreachable() -> None:
    respx.get("https://litellm.test/v1/models").mock(side_effect=httpx.ConnectTimeout("slow"))

    report = await _probe("https://litellm.test").report()

    assert report == ReadinessReport(ready=False, detail="unreachable: no response within 2.5s")


@respx.mock
async def test_slow_endpoint_is_cut_off_by_the_probe_deadline() -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=MODEL_LIST)

    respx.get("https://litellm.test/v1/models").mock(side_effect=hang)

    report = await _probe("https://litellm.test", timeout_seconds=0.05).report()

    assert report == ReadinessReport(ready=False, detail="unreachable: no response within 0.05s")


@respx.mock
async def test_connection_error_is_unreachable() -> None:
    respx.get("http://litellm:4000/v1/models").mock(
        side_effect=httpx.ConnectError(f"refused with Bearer {API_KEY}")
    )

    with capture_logs() as logs:
        report = await _probe("http://litellm:4000").report()

    assert report == ReadinessReport(
        ready=False, detail="unreachable: connection failed (ConnectError)"
    )
    assert API_KEY not in repr(logs)


@pytest.mark.parametrize(
    ("status", "prefix"),
    [
        (429, "rate_limited:"),
        (500, "upstream_error:"),
        (503, "upstream_error:"),
        (400, "unexpected_response:"),
    ],
)
@respx.mock
async def test_other_failures_say_the_endpoint_was_reached(status: int, prefix: str) -> None:
    respx.get("https://litellm.test/v1/models").mock(return_value=httpx.Response(status))

    report = await _probe("https://litellm.test").report()

    assert report.ready is False
    assert report.detail is not None
    assert report.detail.startswith(prefix)
    assert f"HTTP {status}" in report.detail


@respx.mock
async def test_a_page_that_is_not_a_model_list_is_not_ready() -> None:
    # A base URL pointed at some web server that answers every path with 200.
    respx.get("https://wrong.test/v1/models").mock(
        return_value=httpx.Response(200, text="<html>welcome</html>")
    )

    report = await _probe("https://wrong.test").report()

    assert report.ready is False
    assert report.detail is not None
    assert "not a model list" in report.detail


@respx.mock
async def test_base_url_ending_in_v1_gets_a_hint() -> None:
    respx.get("https://api.openai.test/v1/v1/models").mock(return_value=httpx.Response(404))
    respx.get("https://api.openai.test/v1/health/readiness").mock(return_value=httpx.Response(404))

    report = await _probe("https://api.openai.test/v1").report()

    assert report.ready is False
    assert report.detail is not None
    assert "should not end in /v1" in report.detail


@respx.mock
async def test_a_standing_failure_logs_once_and_recovery_logs_again() -> None:
    route = respx.get("https://user:secret@litellm.test/v1/models")
    route.mock(return_value=httpx.Response(503))
    probe = _probe("https://user:secret@litellm.test")

    with capture_logs() as logs:
        await probe.report()
        await probe.report()
        route.mock(return_value=httpx.Response(200, json=MODEL_LIST))
        await probe.report()

    assert [entry["event"] for entry in logs] == [
        "llm_readiness_degraded",
        "llm_readiness_recovered",
    ]
    assert logs[0]["target"] == "https://litellm.test"
    assert "secret" not in repr(logs)


async def _poll(history: LlmReadinessHistory, times: int) -> list[ReadinessReport]:
    # A new probe per poll over one history, as the registry builds them per /ready call.
    return [await _probe(FLAKY_URL, history=history).report() for _ in range(times)]


@respx.mock
async def test_a_lone_missed_probe_right_after_a_success_still_reports_ready() -> None:
    # QA R5: two of four /ready polls timed out at 2.5 s, never two in a row,
    # while every real LLM call in the same minutes succeeded.
    respx.get(f"{FLAKY_URL}/v1/models").mock(
        side_effect=[
            httpx.Response(200, json=MODEL_LIST),
            httpx.ReadTimeout("slow"),
            httpx.Response(200, json=MODEL_LIST),
            httpx.ReadTimeout("slow"),
        ]
    )

    reports = await _poll(LlmReadinessHistory(), 4)

    assert reports == [
        ReadinessReport(ready=True),
        ReadinessReport(ready=True, detail=MISSED_TIMEOUT),
        ReadinessReport(ready=True),
        ReadinessReport(ready=True, detail=MISSED_TIMEOUT),
    ]


@respx.mock
async def test_the_second_failed_probe_in_a_row_reports_degraded() -> None:
    respx.get(f"{FLAKY_URL}/v1/models").mock(
        side_effect=[
            httpx.Response(200, json=MODEL_LIST),
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("slow"),
            httpx.Response(503),
        ]
    )

    with capture_logs() as logs:
        reports = await _poll(LlmReadinessHistory(), 4)

    assert reports[1:] == [
        ReadinessReport(ready=True, detail=MISSED_TIMEOUT),
        TIMED_OUT,
        ReadinessReport(
            ready=False, detail="upstream_error: the endpoint is reachable but failing (HTTP 503)"
        ),
    ]
    assert [entry["event"] for entry in logs] == [
        "llm_readiness_probe_missed",
        "llm_readiness_degraded",
        "llm_readiness_degraded",
    ]
    assert API_KEY not in repr(logs)


@respx.mock
async def test_a_success_between_failures_starts_the_count_again() -> None:
    respx.get(f"{FLAKY_URL}/v1/models").mock(
        side_effect=[
            httpx.Response(200, json=MODEL_LIST),
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("slow"),
            httpx.Response(200, json=MODEL_LIST),
            httpx.ReadTimeout("slow"),
        ]
    )
    history = LlmReadinessHistory()

    reports = await _poll(history, 5)

    assert [report.ready for report in reports] == [True, True, False, True, True]
    assert reports[4] == ReadinessReport(ready=True, detail=MISSED_TIMEOUT)
    assert history.consecutive_failures == 1


@respx.mock
async def test_an_endpoint_never_reached_is_degraded_from_its_first_failure() -> None:
    # Nothing to ride out before a first success: a fresh process pointed at a
    # wrong key or a dead URL says so on its very first /ready.
    respx.get(f"{FLAKY_URL}/v1/models").mock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.Response(401),
            httpx.Response(200, json=MODEL_LIST),
        ]
    )

    reports = await _poll(LlmReadinessHistory(), 3)

    assert reports == [
        TIMED_OUT,
        ReadinessReport(
            ready=False, detail="unauthorized: the endpoint rejected the API key (HTTP 401)"
        ),
        ReadinessReport(ready=True),
    ]


def _container_settings(**overrides: object) -> Settings:
    settings_factory = cast(Callable[..., Settings], Settings)
    base: dict[str, object] = {
        "_env_file": None,
        "secret_key": SECRET_KEY,
        "runtime_mode": "container",
        "chat_provider": "fake",
        "directory_provider": "fake",
        "workflow_provider": "fake",
    }
    return settings_factory(**(base | overrides))


class _FakeExecutor:
    async def fetch(self, query: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
        return [{"ok": 1}]


class _FakeRedis:
    async def ping(self) -> bool:
        return True


def test_catalog_probes_the_configured_endpoint_with_the_configured_key() -> None:
    probes = catalog.build_readiness_probes(
        _container_settings(
            llm_provider="litellm",
            litellm_base_url="https://api.openai.test",
            litellm_api_key=API_KEY,
        ),
        _FakeExecutor,
        _FakeRedis,
    )

    probe = probes["llm_provider"]
    assert isinstance(probe, LlmEndpointReadinessProbe)
    assert probe.base_url == "https://api.openai.test"
    assert probe.api_key == API_KEY
    assert API_KEY not in repr(probe)


def test_catalog_keeps_the_fake_llm_statically_ready() -> None:
    probes = catalog.build_readiness_probes(
        _container_settings(llm_provider="fake"),
        _FakeExecutor,
        _FakeRedis,
    )

    assert isinstance(probes["llm_provider"], StaticReadinessProbe)


def test_catalog_gives_the_llm_probe_the_history_it_is_handed() -> None:
    settings = _container_settings(
        llm_provider="litellm",
        litellm_base_url="https://api.openai.test",
        litellm_api_key=API_KEY,
    )
    history = LlmReadinessHistory()

    shared = catalog.build_readiness_probes(
        settings, _FakeExecutor, _FakeRedis, llm_history=history
    )["llm_provider"]
    alone = catalog.build_readiness_probes(settings, _FakeExecutor, _FakeRedis)["llm_provider"]

    assert isinstance(shared, LlmEndpointReadinessProbe)
    assert shared.history is history
    assert isinstance(alone, LlmEndpointReadinessProbe)
    assert alone.history is not history


class _BoolProbe:
    async def check(self) -> bool:
        return True


class _ReportingProbe:
    def __init__(self, report: ReadinessReport) -> None:
        self._report = report

    async def check(self) -> bool:
        return self._report.ready

    async def report(self) -> ReadinessReport:
        return self._report


class _RaisingProbe:
    async def check(self) -> bool:
        raise RuntimeError(f"postgresql://openprogram:{API_KEY}@db/openprogram")


class _HangingProbe:
    async def check(self) -> bool:
        await asyncio.sleep(5)
        return True


def test_ready_keeps_its_shape_and_adds_reasons(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes: dict[str, ReadinessProbe] = {
        "database": _BoolProbe(),
        "redis": _RaisingProbe(),
        "workflow_provider": _HangingProbe(),
        "llm_provider": _ReportingProbe(
            ReadinessReport(
                ready=False,
                detail="unauthorized: the endpoint rejected the API key (HTTP 401)",
            )
        ),
    }
    monkeypatch.setattr(catalog, "build_readiness_probes", lambda *args, **kwargs: probes)
    monkeypatch.setattr(registry_module, "_READINESS_TIMEOUT_SECONDS", 0.05)
    app = create_app(settings=settings, registry=ServiceRegistry(settings))

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"] == {
        "database": True,
        "redis": False,
        "workflow_provider": False,
        "llm_provider": False,
    }
    assert body["details"] == {
        "redis": "error: the check failed (RuntimeError)",
        "workflow_provider": "timeout: the check did not finish within 0.05s",
        "llm_provider": "unauthorized: the endpoint rejected the API key (HTTP 401)",
    }
    assert API_KEY not in response.text


def test_ready_reports_no_details_when_everything_is_ready(settings: Settings) -> None:
    app = create_app(settings=settings)

    with TestClient(app) as client:
        body = client.get("/ready").json()

    assert body["status"] == "ok"
    assert body["details"] == {}
    assert all(body["dependencies"].values())


def test_ready_rides_out_one_missed_llm_probe_across_calls(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every /ready call builds new probes; the history the registry owns is
    # what carries the earlier success over to the next call.
    answers: list[httpx.Response | Exception] = [
        httpx.Response(200, json=MODEL_LIST),
        httpx.ReadTimeout("slow"),
        httpx.ReadTimeout("slow"),
    ]

    def answer(request: httpx.Request) -> httpx.Response:
        outcome = answers.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    transport = httpx.MockTransport(answer)

    def llm_only(
        _settings: Settings,
        *_factories: object,
        llm_history: LlmReadinessHistory | None = None,
        **_options: object,
    ) -> dict[str, ReadinessProbe]:
        assert llm_history is not None
        probe = LlmEndpointReadinessProbe(
            base_url=FLAKY_URL, api_key=API_KEY, transport=transport, history=llm_history
        )
        return {"llm_provider": probe}

    monkeypatch.setattr(catalog, "build_readiness_probes", llm_only)
    app = create_app(settings=settings, registry=ServiceRegistry(settings))

    with TestClient(app) as client:
        bodies = [client.get("/ready").json() for _ in range(3)]

    assert [body["status"] for body in bodies] == ["ok", "ok", "degraded"]
    assert [body["dependencies"] for body in bodies] == [
        {"llm_provider": True},
        {"llm_provider": True},
        {"llm_provider": False},
    ]
    assert [body["details"] for body in bodies] == [
        {},
        {"llm_provider": MISSED_TIMEOUT},
        {"llm_provider": "unreachable: no response within 2.5s"},
    ]
    assert not answers
