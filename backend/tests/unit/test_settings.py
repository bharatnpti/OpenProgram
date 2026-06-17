from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import Settings
from core.domain.auth import Role

SECRET_KEY = "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ="


def test_settings_parses_dev_roles() -> None:
    settings = Settings(
        secret_key=SECRET_KEY,
        dev_principal_roles="dev,sm",
    )
    assert settings.dev_roles == frozenset({Role.DEV, Role.SM})


def test_settings_parse_cors_origins_and_pool_sizes() -> None:
    settings = Settings(
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
    settings = Settings(secret_key=SECRET_KEY)

    assert settings.workflow_provider == "dbos"
    assert settings.dbos_app_name == "pulseops"
    assert settings.dbos_heartbeat_cron == "0 * * * * *"
    assert settings.resolved_dbos_system_database_url == settings.database_url


def test_settings_resolves_configured_dbos_system_database_url() -> None:
    settings = Settings(
        secret_key=SECRET_KEY,
        dbos_system_database_url="postgresql://dbos:dbos@localhost:5432/dbos",
    )

    assert (
        settings.resolved_dbos_system_database_url == "postgresql://dbos:dbos@localhost:5432/dbos"
    )


def test_settings_rejects_invalid_pool_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(
            secret_key=SECRET_KEY,
            postgres_pool_min_size=5,
            postgres_pool_max_size=1,
        )


def test_settings_fail_fast_on_invalid_secret_key() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key="too-short")


def test_settings_validate_provider_selectors() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, chat_provider="teams")
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, issue_tracker_provider="linear")
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, vcs_provider="gitlab")
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, calendar_provider="exchange")
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, llm_provider="gemini")
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, workflow_provider="airflow")
