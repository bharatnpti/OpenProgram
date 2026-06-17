from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.domain.auth import Role


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PULSEOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: str = "local"
    tenant_id: str = "demo"
    runtime_mode: Literal["container", "memory"] = "container"
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    database_url: str = "postgresql://pulseops:pulseops@localhost:5432/pulseops"
    postgres_pool_min_size: int = 1
    postgres_pool_max_size: int = 5
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 10
    heartbeat_schedule_id: str | None = None
    checkin_fanout_schedule_id: str = "pulseops-checkin-fanout"
    checkin_fanout_cron: str = "30 9 * * 1-5"
    jira_sync_projects: tuple[str, ...] = ()
    github_sync_repos: tuple[str, ...] = ()
    calendar_sync_user_ids: tuple[str, ...] = ()
    calendar_sync_window_days: int = 1
    jira_sync_cron: str = "0 * * * *"
    github_sync_cron: str = "*/15 * * * *"
    calendar_sync_cron: str = "0 8 * * *"
    conversation_retention_days: int = 30
    conversation_purge_cron: str = "0 3 * * *"
    conversation_purge_schedule_id: str = "pulseops-conversation-purge"
    temporal_target: str = "localhost:7233"
    temporal_task_queue: str = "pulseops-foundation"
    temporal_schedule_id: str = "pulseops-heartbeat"
    temporal_heartbeat_interval_seconds: int = 60
    dbos_app_name: str = "pulseops"
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
    slack_api_base_url: str = "https://slack.com/api"
    slack_retry_attempts: int = 3
    slack_retry_backoff_seconds: float = 0.25
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    github_base_url: str = "https://api.github.com"
    github_token: str | None = None
    github_owner: str | None = None
    google_calendar_base_url: str = "https://www.googleapis.com/calendar/v3"
    google_calendar_token: str | None = None
    google_calendar_id: str | None = None
    redis_rate_limit_window_seconds: int = 1
    redis_rate_limit_max_events: int = 1
    secret_key: str = Field(default="", min_length=0)
    dev_principal_subject: str = "dev-user"
    dev_principal_roles: str = "admin"

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
        allowed = {"slack", "fake"}
        if value not in allowed:
            message = f"chat_provider must be one of {sorted(allowed)}"
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
        allowed = {"github", "fake"}
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
        "dbos_app_name",
        "dbos_heartbeat_cron",
        "checkin_fanout_schedule_id",
        "checkin_fanout_cron",
        "jira_sync_cron",
        "github_sync_cron",
        "calendar_sync_cron",
        "conversation_purge_cron",
        "conversation_purge_schedule_id",
    )
    @classmethod
    def validate_non_empty_string(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
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
    )
    @classmethod
    def validate_positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value

    @field_validator("temporal_heartbeat_interval_seconds", "redis_rate_limit_window_seconds")
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
    def validate_pool_bounds(self) -> Self:
        if self.postgres_pool_max_size < self.postgres_pool_min_size:
            raise ValueError("postgres_pool_max_size must be >= postgres_pool_min_size")
        if self.heartbeat_schedule_id is None:
            object.__setattr__(self, "heartbeat_schedule_id", self.temporal_schedule_id)
        return self

    @property
    def dev_roles(self) -> frozenset[Role]:
        values = [item.strip().lower() for item in self.dev_principal_roles.split(",")]
        return frozenset(Role(value) for value in values if value)

    @property
    def resolved_dbos_system_database_url(self) -> str:
        return self.dbos_system_database_url or self.database_url

    @property
    def resolved_heartbeat_schedule_id(self) -> str:
        return self.heartbeat_schedule_id or self.temporal_schedule_id


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
