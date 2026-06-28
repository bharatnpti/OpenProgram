from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
EXPECTED_PROVIDER_ENV = {
    "PULSEOPS_CHAT_PROVIDER": "${PULSEOPS_CHAT_PROVIDER:-slack}",
    "PULSEOPS_DIRECTORY_PROVIDER": "${PULSEOPS_DIRECTORY_PROVIDER:-slack}",
    "PULSEOPS_CALENDAR_PROVIDER": "${PULSEOPS_CALENDAR_PROVIDER:-google}",
    "PULSEOPS_ISSUE_TRACKER_PROVIDER": "${PULSEOPS_ISSUE_TRACKER_PROVIDER:-jira}",
    "PULSEOPS_VCS_PROVIDER": "${PULSEOPS_VCS_PROVIDER:-github}",
}
EXPECTED_SCHEDULE_ENV = {
    "PULSEOPS_CHECKIN_FANOUT_CRON": "${PULSEOPS_CHECKIN_FANOUT_CRON:-30 9 * * 1-5}",
}
EXPECTED_RUNTIME_ENV = {
    "PULSEOPS_ENVIRONMENT": "${PULSEOPS_ENVIRONMENT:-local}",
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
