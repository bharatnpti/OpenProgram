from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
EXPECTED_PROVIDER_ENV = {
    "OPENPROGRAM_CHAT_PROVIDER": "${OPENPROGRAM_CHAT_PROVIDER:-slack}",
    "OPENPROGRAM_DIRECTORY_PROVIDER": "${OPENPROGRAM_DIRECTORY_PROVIDER:-slack}",
    "OPENPROGRAM_CALENDAR_PROVIDER": "${OPENPROGRAM_CALENDAR_PROVIDER:-google}",
    "OPENPROGRAM_ISSUE_TRACKER_PROVIDER": "${OPENPROGRAM_ISSUE_TRACKER_PROVIDER:-jira}",
    "OPENPROGRAM_VCS_PROVIDER": "${OPENPROGRAM_VCS_PROVIDER:-github}",
}
EXPECTED_SCHEDULE_ENV = {
    "OPENPROGRAM_CHECKIN_FANOUT_CRON": "${OPENPROGRAM_CHECKIN_FANOUT_CRON:-30 9 * * 1-5}",
}
EXPECTED_RUNTIME_ENV = {
    "OPENPROGRAM_ENVIRONMENT": "${OPENPROGRAM_ENVIRONMENT:-local}",
}
EXPECTED_LLM_ENV = {
    "OPENPROGRAM_LLM_PROVIDER": "${OPENPROGRAM_LLM_PROVIDER:-litellm}",
    "OPENPROGRAM_LITELLM_BASE_URL": (
        "${OPENPROGRAM_LITELLM_BASE_URL_INTERNAL:-http://litellm:4000}"
    ),
    "OPENPROGRAM_LITELLM_API_KEY": "${OPENPROGRAM_LITELLM_API_KEY:-local-litellm-key}",
    "OPENPROGRAM_LITELLM_MODEL": "${OPENPROGRAM_LITELLM_MODEL:-gpt-5.5}",
    "OPENPROGRAM_LANGFUSE_HOST": (
        "${OPENPROGRAM_LANGFUSE_HOST_INTERNAL:-http://langfuse-web:3000}"
    ),
    "OPENPROGRAM_LANGFUSE_PUBLIC_KEY": (
        "${LANGFUSE_PUBLIC_KEY:-${OPENPROGRAM_LANGFUSE_PUBLIC_KEY:-pk-lf-local}}"
    ),
    "OPENPROGRAM_LANGFUSE_SECRET_KEY": (
        "${LANGFUSE_SECRET_KEY:-${OPENPROGRAM_LANGFUSE_SECRET_KEY:-sk-lf-local}}"
    ),
    "OPENPROGRAM_LANGFUSE_PROJECT_ID": "${OPENPROGRAM_LANGFUSE_PROJECT_ID:-local-project}",
}
EXPECTED_LANGFUSE_INIT_ENV = {
    "LANGFUSE_INIT_PROJECT_ID": "${OPENPROGRAM_LANGFUSE_PROJECT_ID:-local-project}",
    "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": (
        "${LANGFUSE_PUBLIC_KEY:-${OPENPROGRAM_LANGFUSE_PUBLIC_KEY:-pk-lf-local}}"
    ),
    "LANGFUSE_INIT_PROJECT_SECRET_KEY": (
        "${LANGFUSE_SECRET_KEY:-${OPENPROGRAM_LANGFUSE_SECRET_KEY:-sk-lf-local}}"
    ),
}
EXPECTED_LITELLM_ENV = {
    "LITELLM_MASTER_KEY": "${OPENPROGRAM_LITELLM_API_KEY:-local-litellm-key}",
    "OPENAI_API_KEY": "${OPENAI_API_KEY:-}",
    "OPENAI_BASE_URL": "${OPENAI_BASE_URL:-https://eu.api.openai.com/v1}",
}


def test_testcontainers_compose_import_is_available() -> None:
    from testcontainers.compose import DockerCompose

    assert DockerCompose.__name__ == "DockerCompose"


def test_backend_and_worker_provider_and_schedule_defaults_are_env_overrideable() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    for service_name in ("backend", "worker"):
        environment = compose["services"][service_name]["environment"]

        assert {key: environment.get(key) for key in EXPECTED_PROVIDER_ENV} == EXPECTED_PROVIDER_ENV
        assert {key: environment.get(key) for key in EXPECTED_SCHEDULE_ENV} == EXPECTED_SCHEDULE_ENV
        assert {key: environment.get(key) for key in EXPECTED_RUNTIME_ENV} == EXPECTED_RUNTIME_ENV
        assert {key: environment.get(key) for key in EXPECTED_LLM_ENV} == EXPECTED_LLM_ENV

    litellm_environment = compose["services"]["litellm"]["environment"]
    assert {
        key: litellm_environment.get(key) for key in EXPECTED_LITELLM_ENV
    } == EXPECTED_LITELLM_ENV

    langfuse_environment = compose["x-langfuse-env"]
    assert {
        key: langfuse_environment.get(key) for key in EXPECTED_LANGFUSE_INIT_ENV
    } == EXPECTED_LANGFUSE_INIT_ENV


def test_langfuse_compose_health_reflects_clickhouse_dependency() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["clickhouse"]["restart"] == "unless-stopped"

    langfuse_healthcheck = compose["services"]["langfuse-web"]["healthcheck"]["test"]
    assert langfuse_healthcheck[0] == "CMD-SHELL"
    assert "http://langfuse-web:3000/api/public/health" in langfuse_healthcheck[1]
    assert "http://clickhouse:8123/ping" in langfuse_healthcheck[1]
