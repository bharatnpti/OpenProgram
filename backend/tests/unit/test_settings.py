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


def test_settings_default_to_dev_auth_provider() -> None:
    settings = _settings(secret_key=SECRET_KEY)

    assert settings.auth_provider == "dev"
    assert settings.auth_session_ttl_seconds == 7200
    assert settings.auth_cookie_name == "openprogram_session"
    assert settings.auth_csrf_cookie_name == "openprogram_csrf"
    assert settings.auth_cookie_samesite == "lax"


def test_settings_default_slack_inbound_transport_is_http_webhook() -> None:
    settings = _settings(secret_key=SECRET_KEY, chat_provider="slack")

    assert settings.slack_inbound_transport == "http"
    assert settings.slack_socket_mode is False
    assert _settings(
        secret_key=SECRET_KEY, chat_provider="slack", slack_inbound_transport="socket"
    ).slack_socket_mode
    # Socket Mode only applies to real Slack, never to the simulator.
    assert not _settings(
        secret_key=SECRET_KEY, chat_provider="mock_slack", slack_inbound_transport="socket"
    ).slack_socket_mode


def test_settings_require_oidc_fields_for_bff_mode() -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, auth_provider="oidc_bff", runtime_mode="memory")

    settings = _settings(
        secret_key=SECRET_KEY,
        runtime_mode="memory",
        auth_provider="oidc_bff",
        oidc_issuer_url="https://issuer.example.com",
        oidc_client_id="openprogram",
        oidc_client_secret="secret",
    )

    assert settings.auth_provider == "oidc_bff"
    assert settings.auth_callback_url() == "http://127.0.0.1:8000/api/v1/auth/callback"


def test_settings_reject_invalid_auth_cookie_and_role_map_config() -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, auth_cookie_samesite="none", auth_cookie_secure=False)
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, oidc_scopes="profile,email")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, oidc_role_map_json='{"group":"unknown"}')


def test_settings_validate_safe_auth_return_url() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        auth_frontend_url="http://localhost:5173",
        auth_allowed_return_paths="/me,/portfolio",
        auth_allowed_return_origins="https://admin.example.com",
    )

    assert settings.safe_auth_return_url("/me") == "http://localhost:5173/me"
    assert (
        settings.safe_auth_return_url("https://admin.example.com/portfolio?tab=risks")
        == "https://admin.example.com/portfolio?tab=risks"
    )
    assert settings.safe_auth_return_url("https://evil.example.com/me") == "http://localhost:5173"
    assert settings.safe_auth_return_url("/admin") == "http://localhost:5173"


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
    assert settings.dbos_app_name == "openprogram"
    assert settings.heartbeat_schedule_id == "openprogram-heartbeat"
    assert settings.resolved_heartbeat_schedule_id == "openprogram-heartbeat"
    assert settings.dbos_heartbeat_cron == "0 * * * * *"
    assert settings.resolved_dbos_system_database_url == settings.database_url
    assert settings.checkin_reconcile_enabled is True
    assert settings.checkin_reconcile_schedule_id == "openprogram-checkin-reconcile"
    assert settings.checkin_reconcile_cron == "*/15 * * * 1-5"
    assert settings.checkin_reconcile_after_local_time == "09:45"
    assert settings.checkin_reconcile_timezone is None
    assert settings.resolved_checkin_reconcile_timezone == "UTC"
    assert settings.checkin_reply_wait_seconds == 14400
    assert settings.checkin_final_reply_wait_seconds == 28800
    assert settings.checkin_max_clarifications == 2
    assert settings.llm_max_tool_iterations == 3
    assert settings.ask_investigate_max_steps == 3
    assert settings.ask_investigate_max_tool_iterations == 6
    assert settings.ask_investigate_timeout_seconds == 120
    assert settings.ask_investigate_llm_model == settings.default_llm_model
    assert settings.conversation_retention_days == 30
    assert settings.conversation_purge_enabled is True
    assert settings.conversation_purge_cron == "0 3 * * *"
    assert settings.conversation_purge_schedule_id == "openprogram-conversation-purge"


def test_settings_default_cross_person_auto_notify_on() -> None:
    assert _settings(secret_key=SECRET_KEY).cross_person_auto_notify is True


def test_settings_cross_person_auto_notify_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPROGRAM_CROSS_PERSON_AUTO_NOTIFY", "false")

    assert _settings(secret_key=SECRET_KEY).cross_person_auto_notify is False


def test_settings_default_cross_person_notify_retry() -> None:
    settings = _settings(secret_key=SECRET_KEY)

    assert settings.cross_person_notify_retry_enabled is True
    assert settings.cross_person_notify_retry_schedule_id == (
        "openprogram-cross-person-notify-retry"
    )
    assert settings.cross_person_notify_retry_cron == "*/5 * * * *"
    assert settings.cross_person_notify_max_attempts == 5
    assert settings.cross_person_notify_retry_backoff_seconds == 300


def test_settings_cross_person_notify_retry_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPROGRAM_CROSS_PERSON_NOTIFY_RETRY_ENABLED", "false")
    monkeypatch.setenv("OPENPROGRAM_CROSS_PERSON_NOTIFY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("OPENPROGRAM_CROSS_PERSON_NOTIFY_RETRY_BACKOFF_SECONDS", "60")

    settings = _settings(secret_key=SECRET_KEY)

    assert settings.cross_person_notify_retry_enabled is False
    assert settings.cross_person_notify_max_attempts == 3
    assert settings.cross_person_notify_retry_backoff_seconds == 60


@pytest.mark.parametrize(
    "field",
    ["cross_person_notify_max_attempts", "cross_person_notify_retry_backoff_seconds"],
)
def test_settings_rejects_a_non_positive_cross_person_notify_retry_bound(field: str) -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, **{field: 0})


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


def test_settings_resolves_checkin_reconcile_timezone() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        tenant_default_timezone="Asia/Kolkata",
        checkin_reconcile_timezone="",
    )
    configured = _settings(
        secret_key=SECRET_KEY,
        tenant_default_timezone="Asia/Kolkata",
        checkin_reconcile_timezone="UTC",
    )

    assert settings.checkin_reconcile_timezone is None
    assert settings.resolved_checkin_reconcile_timezone == "Asia/Kolkata"
    assert configured.resolved_checkin_reconcile_timezone == "UTC"


def test_settings_rejects_invalid_pool_bounds() -> None:
    with pytest.raises(ValidationError):
        _settings(
            secret_key=SECRET_KEY,
            postgres_pool_min_size=5,
            postgres_pool_max_size=1,
        )


def test_settings_bound_every_connection_pool_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "POSTGRES_POOL_MIN_SIZE",
        "POSTGRES_POOL_MAX_SIZE",
        "POSTGRES_POOL_TIMEOUT_SECONDS",
        "DBOS_SYSTEM_POOL_SIZE",
        "SYNC_QUEUE_CONCURRENCY",
    ):
        monkeypatch.delenv(f"OPENPROGRAM_{name}", raising=False)

    settings = _settings(secret_key=SECRET_KEY)

    # docs/ops/database-connections.md budgets these against max_connections.
    assert settings.postgres_pool_min_size == 1
    assert settings.postgres_pool_max_size == 10
    assert settings.postgres_pool_timeout_seconds == 30.0
    assert settings.dbos_system_pool_size == 10
    assert settings.sync_queue_concurrency == 4


def test_settings_read_pool_limits_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENPROGRAM_POSTGRES_POOL_MAX_SIZE", "7")
    monkeypatch.setenv("OPENPROGRAM_POSTGRES_POOL_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("OPENPROGRAM_DBOS_SYSTEM_POOL_SIZE", "6")
    monkeypatch.setenv("OPENPROGRAM_SYNC_QUEUE_CONCURRENCY", "2")

    settings = _settings(secret_key=SECRET_KEY)

    assert settings.postgres_pool_max_size == 7
    assert settings.postgres_pool_timeout_seconds == 2.5
    assert settings.dbos_system_pool_size == 6
    assert settings.sync_queue_concurrency == 2


@pytest.mark.parametrize(
    "field",
    [
        "postgres_pool_timeout_seconds",
        "dbos_system_pool_size",
        "sync_queue_concurrency",
    ],
)
def test_settings_reject_a_pool_limit_that_is_not_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, **{field: 0})


def test_settings_fail_fast_on_invalid_secret_key() -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key="too-short")


def test_settings_reject_dev_auth_outside_local() -> None:
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, environment="production", auth_provider="dev")


def test_settings_reject_default_secret_key_outside_local() -> None:
    with pytest.raises(ValidationError):
        _settings(
            environment="staging",
            secret_key=SECRET_KEY,
            auth_provider="oidc_bff",
            oidc_issuer_url="https://issuer.example.com",
            oidc_client_id="openprogram",
            oidc_client_secret="secret",
            runtime_mode="memory",
        )


def test_settings_allow_hardened_non_local_config() -> None:
    unique_key = "A" * 43 + "="
    settings = _settings(
        environment="production",
        secret_key=unique_key,
        auth_provider="oidc_bff",
        oidc_issuer_url="https://issuer.example.com",
        oidc_client_id="openprogram",
        oidc_client_secret="secret",
        runtime_mode="memory",
    )
    assert settings.environment == "production"
    assert settings.auth_provider == "oidc_bff"


def test_settings_allow_dev_auth_and_default_key_in_local() -> None:
    settings = _settings(secret_key=SECRET_KEY, environment="local", auth_provider="dev")
    assert settings.auth_provider == "dev"
    assert settings.secret_key == SECRET_KEY


def _shared(**overrides: object) -> Settings:
    """A deployed (non-local) environment that passes the shared-deployment guard."""
    return _settings(
        environment="production",
        secret_key="A" * 43 + "=",
        auth_provider="oidc_bff",
        oidc_issuer_url="https://issuer.example.com",
        oidc_client_id="openprogram",
        oidc_client_secret="secret",
        runtime_mode="memory",
        **overrides,
    )


def test_the_console_url_for_outgoing_links_is_set_or_falls_back_to_the_frontend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPROGRAM_CONSOLE_URL", "https://openprogram.example.com/")
    assert _shared().public_console_url == "https://openprogram.example.com"

    # Unset or empty, the frontend address the sign-in returns to is used.
    monkeypatch.setenv("OPENPROGRAM_CONSOLE_URL", "")
    fallback = _shared(auth_frontend_url="https://console.example.com")
    assert fallback.console_url is None
    assert fallback.public_console_url == "https://console.example.com"
    with pytest.raises(ValidationError):
        _shared(console_url="openprogram.example.com")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://[::1]:5174",
        "http://console.localhost:8080",
        "http://0.0.0.0:5173",
    ],
)
def test_a_local_console_url_is_linked_only_in_the_local_environment(url: str) -> None:
    assert _shared(console_url=url).public_console_url is None
    # The default frontend address is local too: unset, a deployment sends no link.
    assert _shared().public_console_url is None
    assert _settings(secret_key=SECRET_KEY, console_url=url).public_console_url == url
    assert _shared(console_url="https://localhost.example.com").public_console_url == (
        "https://localhost.example.com"
    )


def test_settings_escalation_policy_default_ladder() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        checkin_reply_wait_seconds=100,
        escalation_scrum_master_wait_seconds=200,
        escalation_manager_wait_seconds=300,
    )
    policy = settings.escalation_policy()
    assert [step.target.value for step in policy.steps] == ["developer", "scrum_master", "manager"]
    assert [step.wait_seconds for step in policy.steps] == [100, 200, 300]


def test_settings_escalation_policy_disabled_is_empty() -> None:
    settings = _settings(secret_key=SECRET_KEY, escalation_enabled=False)
    assert settings.escalation_policy().steps == ()


def test_settings_validate_provider_selectors() -> None:
    settings = _settings(
        secret_key=SECRET_KEY,
        chat_provider="mock_slack",
        directory_provider="mock_slack",
        vcs_provider="gitlab",
        gitlab_base_url="https://gitlab.test/api/v4",
        gitlab_namespace_id="136978033",
        chat_simulator_enabled=True,
    )
    assert settings.chat_provider == "mock_slack"
    assert settings.directory_provider == "mock_slack"
    assert settings.vcs_provider == "gitlab"
    assert settings.gitlab_base_url == "https://gitlab.test/api/v4"
    assert settings.gitlab_namespace_id == "136978033"
    assert settings.chat_simulator_enabled is True

    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, chat_provider="teams")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, directory_provider="ldap")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, issue_tracker_provider="linear")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, vcs_provider="bitbucket")
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
        github_sync_repos='["oneai/openprogram", "oneai/runtime"]',
        calendar_sync_user_ids="dev-1, dev-2",
        calendar_sync_window_days=3,
    )

    assert settings.jira_sync_projects == ("PO", "ENG:program-platform", "API:pod-runtime:board-1")
    assert settings.github_sync_repos == ("oneai/openprogram", "oneai/runtime")
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
        _settings(secret_key=SECRET_KEY, checkin_reconcile_after_local_time="not-a-time")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, checkin_reconcile_after_local_time="09:45+01:00")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, checkin_reconcile_cron="")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, checkin_reconcile_timezone="Mars/Base")
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, checkin_max_clarifications=-1)
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, llm_max_tool_iterations=-1)
    for steps in (0, 6):
        with pytest.raises(ValidationError):
            _settings(secret_key=SECRET_KEY, ask_investigate_max_steps=steps)
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, ask_investigate_max_tool_iterations=-1)
    with pytest.raises(ValidationError):
        _settings(secret_key=SECRET_KEY, ask_investigate_timeout_seconds=0)


def test_investigate_runs_on_its_own_model_only_when_one_is_set() -> None:
    assert _settings(secret_key=SECRET_KEY, litellm_model="m-1").ask_investigate_llm_model == "m-1"
    deep = _settings(secret_key=SECRET_KEY, litellm_model="m-1", ask_investigate_model="m-2")
    assert deep.ask_investigate_llm_model == "m-2"
