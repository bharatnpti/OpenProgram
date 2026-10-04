"""llm_trace readiness follows the trace sink: disabled when nothing is traced.

/ready reported degraded on a stack without Langfuse only because the
llm_trace probe checked a Langfuse host nobody had deployed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import cast

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.ports.readiness import ReadinessProbe, ReportingReadinessProbe
from infra.adapters import catalog
from infra.adapters.llm.litellm_provider import LangfuseTraceSink, LiteLlmProvider, NoopTraceSink
from infra.adapters.readiness import DisabledReadinessProbe, HttpReadinessProbe
from infra.registry import ServiceRegistry

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="
LANGFUSE_HOST = "https://langfuse.test"
LITELLM_URL = "https://litellm.test"
MODEL_LIST = {"object": "list", "data": [{"id": "test-model", "object": "model"}]}


def _container_settings(**overrides: object) -> Settings:
    settings_factory = cast(Callable[..., Settings], Settings)
    base: dict[str, object] = {
        "_env_file": None,
        "secret_key": SECRET_KEY,
        "runtime_mode": "container",
        "chat_provider": "fake",
        "directory_provider": "fake",
        "workflow_provider": "fake",
        "llm_provider": "litellm",
        "litellm_base_url": LITELLM_URL,
        "litellm_api_key": "test-litellm-key",
    }
    return settings_factory(**(base | overrides))


def _traced_settings(**overrides: object) -> Settings:
    return _container_settings(
        langfuse_host=LANGFUSE_HOST,
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
        **overrides,
    )


class _FakeExecutor:
    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        if "pg_extension" in query:
            return [{"extname": name} for name in ("age", "timescaledb", "vector")]
        return [{"ok": 1}]


class _FakeRedis:
    async def ping(self) -> bool:
        return True


def _probes(settings: Settings) -> dict[str, ReadinessProbe]:
    return catalog.build_readiness_probes(settings, _FakeExecutor, _FakeRedis)


def _ready_body(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """GET /ready over the real container probes, with stand-ins for Postgres and Redis."""
    build = catalog.build_readiness_probes

    def with_fake_stores(
        settings: Settings, *_factories: object, **_options: object
    ) -> dict[str, ReadinessProbe]:
        return build(settings, _FakeExecutor, _FakeRedis)

    monkeypatch.setattr(catalog, "build_readiness_probes", with_fake_stores)
    app = create_app(settings=settings, registry=ServiceRegistry(settings))
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    return cast(dict[str, object], response.json())


@pytest.mark.parametrize(
    ("public_key", "secret_key"),
    [(None, None), ("pk-test", None), (None, "sk-test"), ("  ", "sk-test")],
)
async def test_llm_trace_is_disabled_without_both_langfuse_keys(
    public_key: str | None, secret_key: str | None
) -> None:
    # The host has a localhost default, so a host alone is not tracing.
    settings = _container_settings(langfuse_public_key=public_key, langfuse_secret_key=secret_key)

    probe = _probes(settings)["llm_trace"]

    assert isinstance(probe, DisabledReadinessProbe)
    assert isinstance(probe, ReportingReadinessProbe)
    report = await probe.report()
    assert (report.ready, report.detail) == (True, "disabled")
    # The sink agrees: nothing is sent to Langfuse.
    provider = catalog.build_llm_provider(settings)
    assert isinstance(provider, LiteLlmProvider)
    assert isinstance(provider.trace_sink, NoopTraceSink)


async def test_llm_trace_is_disabled_for_the_fake_llm() -> None:
    probe = _probes(_traced_settings(llm_provider="fake"))["llm_trace"]

    assert isinstance(probe, DisabledReadinessProbe)
    report = await probe.report()
    assert (report.ready, report.detail) == (True, "disabled")


def test_memory_mode_keeps_no_llm_trace_probe_and_no_tracing() -> None:
    settings = _traced_settings(runtime_mode="memory")

    probes = _probes(settings)
    provider = catalog.build_llm_provider(settings)

    assert "llm_trace" not in probes
    assert isinstance(provider, LiteLlmProvider)
    assert isinstance(provider.trace_sink, NoopTraceSink)


def test_configured_tracing_probes_the_host_the_sink_sends_to() -> None:
    settings = _traced_settings()

    probe = _probes(settings)["llm_trace"]
    provider = catalog.build_llm_provider(settings)

    assert isinstance(probe, HttpReadinessProbe)
    assert (probe.base_url, probe.path) == (LANGFUSE_HOST, "/api/public/health")
    assert isinstance(provider, LiteLlmProvider)
    assert provider.trace_sink == LangfuseTraceSink(
        host=LANGFUSE_HOST, public_key="pk-test", secret_key="sk-test"
    )


def test_ready_is_ok_and_says_llm_trace_disabled_when_tracing_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{LITELLM_URL}/v1/models").mock(
            return_value=httpx.Response(200, json=MODEL_LIST)
        )
        langfuse = router.get(f"{LANGFUSE_HOST}/api/public/health")

        body = _ready_body(_container_settings(), monkeypatch)

    assert body["status"] == "ok"
    dependencies = cast(dict[str, bool], body["dependencies"])
    assert dependencies["llm_trace"] is True
    assert all(dependencies.values())
    assert body["details"] == {"llm_trace": "disabled"}
    assert not langfuse.called


@pytest.mark.parametrize(
    "langfuse_health",
    [httpx.Response(503, json={"status": "down"}), httpx.ConnectError("refused")],
)
def test_ready_stays_degraded_when_configured_tracing_fails(
    langfuse_health: httpx.Response | Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    with respx.mock as router:
        router.get(f"{LITELLM_URL}/v1/models").mock(
            return_value=httpx.Response(200, json=MODEL_LIST)
        )
        router.get(f"{LANGFUSE_HOST}/api/public/health").mock(side_effect=[langfuse_health])

        body = _ready_body(_traced_settings(), monkeypatch)

    assert body["status"] == "degraded"
    dependencies = cast(dict[str, bool], body["dependencies"])
    assert [name for name, ready in dependencies.items() if not ready] == ["llm_trace"]
