from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.domain.auth import Role


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENPROGRAM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: str = "local"
    tenant_id: str = "demo"
    runtime_mode: Literal["container", "memory"] = "container"
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    database_url: str = "postgresql://openprogram:openprogram@localhost:5432/openprogram"
    postgres_pool_min_size: int = 1
    postgres_pool_max_size: int = 5
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 10
    heartbeat_schedule_id: str | None = None
    checkin_fanout_schedule_id: str = "openprogram-checkin-fanout"
    checkin_fanout_cron: str = "30 9 * * 1-5"
    jira_sync_projects: tuple[str, ...] = ()
    github_sync_repos: tuple[str, ...] = ()
    calendar_sync_user_ids: tuple[str, ...] = ()
    calendar_sync_window_days: int = 1
    jira_sync_cron: str = "0 * * * *"
    github_sync_cron: str = "*/15 * * * *"
    calendar_sync_cron: str = "0 8 * * *"
    conversation_retention_days: int = 30
    conversation_purge_enabled: bool = True
    conversation_purge_cron: str = "0 3 * * *"
    conversation_purge_schedule_id: str = "openprogram-conversation-purge"
    directory_provider: str = "slack"
    chat_simulator_enabled: bool = False
    directory_sync_cron: str = "0 */6 * * *"
    directory_sync_schedule_id: str = "openprogram-directory-sync"
    risk_assessment_cron: str = "*/30 * * * *"
    risk_default_no_pr_days: int = 3
    risk_default_pr_age_days: int = 3
    risk_default_stale_days: int = 7
    risk_run_default_local_time: str = "18:00"
    temporal_target: str = "localhost:7233"
    temporal_task_queue: str = "openprogram-foundation"
    temporal_schedule_id: str = "openprogram-heartbeat"
    temporal_heartbeat_interval_seconds: int = 60
    dbos_app_name: str = "openprogram"
    dbos_system_database_url: str | None = None
    dbos_heartbeat_cron: str = "0 * * * * *"
    tenant_default_timezone: str = "UTC"
    checkin_reply_wait_seconds: int = 14400
    checkin_final_reply_wait_seconds: int = 28800
    checkin_max_clarifications: int = 2
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str | None = None
    litellm_model: str = "gpt-4o-mini"
    llm_provider: str = "litellm"
    llm_max_tool_iterations: int = 3
    embedding_dimension: int = 1536
    langfuse_host: str = "http://localhost:3001"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_project_id: str | None = None
    otel_exporter_otlp_endpoint: str | None = None
    chat_provider: str = "slack"
    issue_tracker_provider: str = "jira"
    vcs_provider: str = "github"
    calendar_provider: str = "google"
    workflow_provider: str = "dbos"
    slack_bot_token: str | None = None
    slack_signing_secret: str | None = None
    slack_signature_tolerance_seconds: int = 300
    slack_api_base_url: str = "https://slack.com/api"
    slack_retry_attempts: int = 3
    slack_retry_backoff_seconds: float = 0.25
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    github_base_url: str = "https://api.github.com"
    github_token: str | None = None
    github_owner: str | None = None
    gitlab_base_url: str = "https://gitlab.com/api/v4"
    gitlab_token: str | None = None
    gitlab_namespace_id: str | None = None
    google_calendar_base_url: str = "https://www.googleapis.com/calendar/v3"
    google_calendar_token: str | None = None
    google_calendar_id: str | None = None
    redis_rate_limit_window_seconds: int = 1
    redis_rate_limit_max_events: int = 1
    secret_key: str = Field(default="", min_length=0)
    dev_principal_subject: str = "dev-user"
    dev_principal_roles: str = "admin"
    auth_provider: Literal["dev", "oidc_bff"] = "dev"
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_scopes: tuple[str, ...] = ("openid", "profile", "email")
    auth_public_backend_url: str = "http://127.0.0.1:8000"
    auth_frontend_url: str = "http://localhost:5173"
    auth_session_ttl_seconds: int = 7200
    auth_flow_state_ttl_seconds: int = 300
    auth_cookie_name: str = "openprogram_session"
    auth_csrf_cookie_name: str = "openprogram_csrf"
    auth_csrf_header_name: str = "x-csrf-token"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_allowed_return_paths: tuple[str, ...] = ("/",)
    auth_allowed_return_origins: tuple[str, ...] = ()
    oidc_role_claim_paths: tuple[str, ...] = (
        "realm_access.roles",
        "resource_access.<client_id>.roles",
        "groups",
        "roles",
    )
    oidc_role_map_json: str = (
        '{"admin":"admin","dev":"dev","developer":"dev","engineer":"dev",'
        '"po":"po","product_owner":"po","product-owner":"po",'
        '"sm":"sm","scrum_master":"sm","scrum-master":"sm",'
        '"mgr":"mgr","manager":"mgr","exec":"exec","executive":"exec"}'
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return tuple(str(item) for item in parsed)
            return tuple(item.strip() for item in stripped.split(",") if item.strip())
        if isinstance(value, list | tuple | set):
            return tuple(str(item) for item in value)
        return value

    @field_validator(
        "jira_sync_projects",
        "github_sync_repos",
        "calendar_sync_user_ids",
        "oidc_scopes",
        "auth_allowed_return_paths",
        "auth_allowed_return_origins",
        "oidc_role_claim_paths",
        mode="before",
    )
    @classmethod
    def parse_string_tuple(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return ()
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return tuple(str(item).strip() for item in parsed if str(item).strip())
            return tuple(item.strip() for item in stripped.split(",") if item.strip())
        if isinstance(value, list | tuple | set):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return value

    @field_validator("jira_sync_projects")
    @classmethod
    def validate_jira_sync_projects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if item.count(":") > 2:
                raise ValueError(
                    "jira_sync_projects entries must be PROJECT, PROJECT:CONTAINER, "
                    "or PROJECT:CONTAINER:BOARD"
                )
            parts = item.split(":")
            project = parts[0].strip()
            if not project:
                raise ValueError("jira_sync_projects project key must not be empty")
            if len(parts) >= 2 and not parts[1].strip():
                raise ValueError("jira_sync_projects container id must not be empty")
            if len(parts) == 3 and not parts[2].strip():
                raise ValueError("jira_sync_projects board id must not be empty")
        return value

    @field_validator("chat_provider")
    @classmethod
    def validate_chat_provider(cls, value: str) -> str:
        allowed = {"slack", "fake", "mock_slack"}
        if value not in allowed:
            message = f"chat_provider must be one of {sorted(allowed)}"
            raise ValueError(message)
        return value

    @field_validator("directory_provider")
    @classmethod
    def validate_directory_provider(cls, value: str) -> str:
        allowed = {"slack", "fake", "mock_slack"}
        if value not in allowed:
            message = f"directory_provider must be one of {sorted(allowed)}"
            raise ValueError(message)
        return value

    @field_validator("issue_tracker_provider")
    @classmethod
    def validate_issue_tracker_provider(cls, value: str) -> str:
        allowed = {"jira", "fake"}
        if value not in allowed:
            message = f"issue_tracker_provider must be one of {sorted(allowed)}"
            raise ValueError(message)
        return value

    @field_validator("vcs_provider")
    @classmethod
    def validate_vcs_provider(cls, value: str) -> str:
        allowed = {"github", "gitlab", "fake"}
        if value not in allowed:
            message = f"vcs_provider must be one of {sorted(allowed)}"
            raise ValueError(message)
        return value

    @field_validator("calendar_provider")
    @classmethod
    def validate_calendar_provider(cls, value: str) -> str:
        allowed = {"google", "fake"}
        if value not in allowed:
            message = f"calendar_provider must be one of {sorted(allowed)}"
            raise ValueError(message)
        return value

    @field_validator("llm_provider")
    @classmethod
    def validate_llm_provider(cls, value: str) -> str:
        allowed = {"litellm", "fake"}
        if value not in allowed:
            message = f"llm_provider must be one of {sorted(allowed)}"
            raise ValueError(message)
        return value

    @field_validator("workflow_provider")
    @classmethod
    def validate_workflow_provider(cls, value: str) -> str:
        allowed = {"dbos", "temporal", "fake"}
        if value not in allowed:
            message = f"workflow_provider must be one of {sorted(allowed)}"
            raise ValueError(message)
        return value

    @field_validator("dbos_system_database_url", mode="before")
    @classmethod
    def empty_dbos_system_database_url_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("heartbeat_schedule_id", mode="before")
    @classmethod
    def empty_heartbeat_schedule_id_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "oidc_issuer_url",
        "oidc_client_id",
        "oidc_client_secret",
        mode="before",
    )
    @classmethod
    def empty_oidc_string_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "dbos_app_name",
        "dbos_heartbeat_cron",
        "checkin_fanout_schedule_id",
        "checkin_fanout_cron",
        "jira_sync_cron",
        "github_sync_cron",
        "calendar_sync_cron",
        "conversation_purge_cron",
        "conversation_purge_schedule_id",
        "directory_sync_cron",
        "directory_sync_schedule_id",
        "risk_assessment_cron",
        "risk_run_default_local_time",
        "auth_public_backend_url",
        "auth_frontend_url",
        "auth_cookie_name",
        "auth_csrf_cookie_name",
        "auth_csrf_header_name",
        "oidc_role_map_json",
    )
    @classmethod
    def validate_non_empty_string(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value

    @field_validator("auth_public_backend_url", "auth_frontend_url")
    @classmethod
    def validate_absolute_http_url(cls, value: str) -> str:
        stripped = value.strip().rstrip("/")
        parts = urlsplit(stripped)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("value must be an absolute http(s) URL")
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))

    @field_validator("auth_allowed_return_paths")
    @classmethod
    def validate_return_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("auth_allowed_return_paths must not be empty")
        for item in value:
            if not item.startswith("/") or item.startswith("//"):
                raise ValueError("auth_allowed_return_paths entries must be absolute paths")
        return value

    @field_validator("auth_allowed_return_origins")
    @classmethod
    def validate_return_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            parts = urlsplit(item.strip())
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise ValueError("auth_allowed_return_origins entries must be http(s) origins")
            normalized.append(urlunsplit((parts.scheme, parts.netloc, "", "", "")))
        return tuple(normalized)

    @field_validator("oidc_scopes")
    @classmethod
    def validate_oidc_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if "openid" not in {item.lower() for item in value}:
            raise ValueError("oidc_scopes must include openid")
        return value

    @field_validator("oidc_role_claim_paths")
    @classmethod
    def validate_role_claim_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("oidc_role_claim_paths must not be empty")
        return value

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        if len(value) != 44:
            raise ValueError("secret_key must be a 44-character Fernet key")
        return value

    @field_validator("embedding_dimension")
    @classmethod
    def validate_embedding_dimension(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("embedding_dimension must be positive")
        return value

    @field_validator(
        "slack_retry_attempts",
        "redis_rate_limit_max_events",
        "postgres_pool_min_size",
        "postgres_pool_max_size",
        "redis_max_connections",
        "calendar_sync_window_days",
        "conversation_retention_days",
        "risk_default_no_pr_days",
        "risk_default_pr_age_days",
        "risk_default_stale_days",
        "auth_session_ttl_seconds",
        "auth_flow_state_ttl_seconds",
    )
    @classmethod
    def validate_positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value

    @field_validator(
        "temporal_heartbeat_interval_seconds",
        "redis_rate_limit_window_seconds",
        "slack_signature_tolerance_seconds",
    )
    @classmethod
    def validate_positive_seconds(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("seconds value must be positive")
        return value

    @field_validator("checkin_reply_wait_seconds", "checkin_final_reply_wait_seconds")
    @classmethod
    def validate_non_negative_seconds(cls, value: int) -> int:
        if value < 0:
            raise ValueError("seconds value must be non-negative")
        return value

    @field_validator("checkin_max_clarifications", "llm_max_tool_iterations")
    @classmethod
    def validate_non_negative_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("count value must be non-negative")
        return value

    @model_validator(mode="after")
    def validate_cross_field_settings(self) -> Self:
        if self.postgres_pool_max_size < self.postgres_pool_min_size:
            raise ValueError("postgres_pool_max_size must be >= postgres_pool_min_size")
        if self.heartbeat_schedule_id is None:
            object.__setattr__(self, "heartbeat_schedule_id", self.temporal_schedule_id)
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError("auth_cookie_secure must be true when auth_cookie_samesite is none")
        if self.auth_provider == "oidc_bff":
            missing = [
                name
                for name, value in (
                    ("oidc_issuer_url", self.oidc_issuer_url),
                    ("oidc_client_id", self.oidc_client_id),
                    ("oidc_client_secret", self.oidc_client_secret),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"OIDC BFF auth requires {', '.join(missing)}")
            issuer_parts = urlsplit(self.oidc_issuer_url or "")
            if issuer_parts.scheme not in {"http", "https"} or not issuer_parts.netloc:
                raise ValueError("oidc_issuer_url must be an absolute http(s) URL")
            if self.runtime_mode == "container" and not self.redis_url.strip():
                raise ValueError("redis_url is required for OIDC BFF container mode")
        _ = self.oidc_role_map
        return self

    @property
    def dev_roles(self) -> frozenset[Role]:
        values = [item.strip().lower() for item in self.dev_principal_roles.split(",")]
        return frozenset(Role(value) for value in values if value)

    @property
    def oidc_role_map(self) -> dict[str, Role]:
        parsed = json.loads(self.oidc_role_map_json)
        if not isinstance(parsed, dict):
            raise ValueError("oidc_role_map_json must be a JSON object")
        role_map: dict[str, Role] = {}
        for raw_key, raw_value in parsed.items():
            key = str(raw_key).strip().lower()
            if not key:
                raise ValueError("oidc_role_map_json keys must not be empty")
            try:
                role = Role(str(raw_value).strip().lower())
            except ValueError as exc:
                raise ValueError(f"oidc_role_map_json maps {key!r} to an unknown role") from exc
            role_map[key] = role
        return role_map

    def auth_callback_url(self) -> str:
        return f"{self.auth_public_backend_url}/api/v1/auth/callback"

    def frontend_logged_out_url(self) -> str:
        return f"{self.auth_frontend_url}/logged-out"

    def auth_login_url(self, return_url: str | None = None) -> str:
        from urllib.parse import urlencode

        query = urlencode({"return_url": return_url or "/"})
        return f"{self.auth_public_backend_url}/api/v1/auth/login?{query}"

    def safe_auth_return_url(self, value: str | None) -> str:
        fallback = self.auth_frontend_url
        if not value:
            return fallback
        raw = value.strip()
        if not raw:
            return fallback
        frontend_origin = _origin(self.auth_frontend_url)
        allowed_origins = {frontend_origin, *self.auth_allowed_return_origins}
        parsed = urlsplit(raw)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme not in {"http", "https"}:
                return fallback
            origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
            if origin not in allowed_origins:
                return fallback
            path = parsed.path or "/"
            if not self._return_path_allowed(path):
                return fallback
            return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))
        if not raw.startswith("/") or raw.startswith("//"):
            return fallback
        path = urlsplit(raw).path or "/"
        if not self._return_path_allowed(path):
            return fallback
        return f"{frontend_origin}{raw}"

    def _return_path_allowed(self, path: str) -> bool:
        return any(
            path == allowed or path.startswith(allowed.rstrip("/") + "/")
            for allowed in self.auth_allowed_return_paths
        )

    @property
    def resolved_dbos_system_database_url(self) -> str:
        return self.dbos_system_database_url or self.database_url

    @property
    def resolved_heartbeat_schedule_id(self) -> str:
        return self.heartbeat_schedule_id or self.temporal_schedule_id

    @property
    def default_llm_model(self) -> str:
        return self.litellm_model


def _origin(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
