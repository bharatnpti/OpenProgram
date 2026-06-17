from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from pydantic import ValidationError

from config.settings import Settings
from core.domain.auth import Role

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="


def _settings(**overrides: object) -> Settings:
    settings_factory = cast(Callable[..., Settings], Settings)
    return settings_factory(_env_file=None, **overrides)


def test_settings_parses_dev_roles() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        dev_principal_roles="dev,sm",
    )
    assert settings.dev_roles == frozenset({Role.DEV, Role.SM})


def test_settings_parse_cors_origins_and_pool_sizes() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        cors_origins="https://app.example.com, https://admin.example.com",
        postgres_pool_min_size=2,
        postgres_pool_max_size=4,
        redis_max_connections=20,
    )
    assert settings.cors_origins == ("https://app.example.com", "https://admin.example.com")
    assert settings.postgres_pool_min_size == 2
    assert settings.postgres_pool_max_size == 4
    assert settings.redis_max_connections == 20


def test_settings_defaults_workflow_provider_to_dbos() -> None:
    settings = _settings(secret_key=SECRET_KEY)

    assert settings.workflow_provider == "dbos"
    assert settings.dbos_app_name == "pulseops"
    assert settings.heartbeat_schedule_id == "pulseops-heartbeat"
    assert settings.resolved_heartbeat_schedule_id == "pulseops-heartbeat"
    assert settings.dbos_heartbeat_cron == "0 * * * * *"
    assert settings.resolved_dbos_system_database_url == settings.database_url
    assert settings.checkin_reply_wait_seconds == 14400
    assert settings.checkin_final_reply_wait_seconds == 28800
    assert settings.checkin_max_clarifications == 2
    assert settings.llm_max_tool_iterations == 3
    assert settings.conversation_retention_days == 30
    assert settings.conversation_purge_cron == "0 3 * * *"
    assert settings.conversation_purge_schedule_id == "pulseops-conversation-purge"


def test_settings_resolves_configured_heartbeat_schedule_id() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        heartbeat_schedule_id="generic-heartbeat",
        temporal_schedule_id="legacy-heartbeat",
    )

    assert settings.heartbeat_schedule_id == "generic-heartbeat"
    assert settings.resolved_heartbeat_schedule_id == "generic-heartbeat"


def test_settings_uses_legacy_temporal_schedule_id_when_generic_unset() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        heartbeat_schedule_id=None,
        temporal_schedule_id="legacy-heartbeat",
    )

    assert settings.heartbeat_schedule_id == "legacy-heartbeat"
    assert settings.resolved_heartbeat_schedule_id == "legacy-heartbeat"


def test_settings_resolves_configured_dbos_system_database_url() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        dbos_system_database_url="postgresql://dbos:dbos@localhost:5432/dbos",
    )

    assert (
        settings.resolved_dbos_system_database_url == "postgresql://dbos:dbos@localhost:5432/dbos"
    )


def test_settings_rejects_invalid_pool_bounds() -> None:
    with pytest.raises(ValidationError):
        _settings(
            secret_key=SECRET_KEY,
            postgres_pool_min_size=5,
            postgres_pool_max_size=1,
        )


def test_settings_fail_fast_on_invalid_secret_key() -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key="too-short")


def test_settings_validate_provider_selectors() -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, chat_provider="teams")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, issue_tracker_provider="linear")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, vcs_provider="gitlab")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, calendar_provider="exchange")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, llm_provider="gemini")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, workflow_provider="airflow")


def test_settings_parse_explicit_sync_target_lists() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        jira_sync_projects="PO, ENG:program-platform, API:pod-runtime:board-1",
        github_sync_repos='["oneai/program-manager", "oneai/runtime"]',
        calendar_sync_user_ids="dev-1, dev-2",
        calendar_sync_window_days=3,
    )

    assert settings.jira_sync_projects == ("PO", "ENG:program-platform", "API:pod-runtime:board-1")
    assert settings.github_sync_repos == ("oneai/program-manager", "oneai/runtime")
    assert settings.calendar_sync_user_ids == ("dev-1", "dev-2")
    assert settings.calendar_sync_window_days == 3


def test_settings_rejects_invalid_sync_targets() -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, jira_sync_projects="PO:container:board:extra")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, jira_sync_projects="PO:")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, jira_sync_projects="PO:container:")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, calendar_sync_window_days=0)
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, conversation_retention_days=0)
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, conversation_purge_cron="")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, conversation_purge_schedule_id="")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, checkin_max_clarifications=-1)
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, llm_max_tool_iterations=-1)
