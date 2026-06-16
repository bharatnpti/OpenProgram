from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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
    database_url: str = "postgresql://pulseops:pulseops@localhost:5432/pulseops"
    redis_url: str = "redis://localhost:6379/0"
    temporal_target: str = "localhost:7233"
    temporal_task_queue: str = "pulseops-foundation"
    temporal_schedule_id: str = "pulseops-heartbeat"
    temporal_heartbeat_interval_seconds: int = 60
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str | None = None
    litellm_model: str = "gpt-4o-mini"
    llm_provider: str = "litellm"
    embedding_dimension: int = 1536
    langfuse_host: str = "http://localhost:3001"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_project_id: str | None = None
    otel_exporter_otlp_endpoint: str | None = None
    chat_provider: str = "slack"
    workflow_provider: str = "temporal"
    slack_bot_token: str | None = None
    slack_api_base_url: str = "https://slack.com/api"
    slack_retry_attempts: int = 3
    slack_retry_backoff_seconds: float = 0.25
    redis_rate_limit_window_seconds: int = 1
    redis_rate_limit_max_events: int = 1
    secret_key: str = Field(default="", min_length=0)
    dev_principal_subject: str = "dev-user"
    dev_principal_roles: str = "admin"

    @field_validator("chat_provider")
    @classmethod
    def validate_chat_provider(cls, value: str) -> str:
        allowed = {"slack", "fake"}
        if value not in allowed:
            message = f"chat_provider must be one of {sorted(allowed)}"
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
        allowed = {"temporal", "fake"}
        if value not in allowed:
            message = f"workflow_provider must be one of {sorted(allowed)}"
            raise ValueError(message)
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

    @field_validator("slack_retry_attempts", "redis_rate_limit_max_events")
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

    @property
    def dev_roles(self) -> frozenset[Role]:
        values = [item.strip().lower() for item in self.dev_principal_roles.split(",")]
        return frozenset(Role(value) for value in values if value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
