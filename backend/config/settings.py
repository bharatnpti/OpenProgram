from __future__ import annotations

from functools import lru_cache

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
    database_url: str = "postgresql://pulseops:pulseops@localhost:5432/pulseops"
    redis_url: str = "redis://localhost:6379/0"
    temporal_target: str = "localhost:7233"
    temporal_task_queue: str = "pulseops-foundation"
    litellm_base_url: str = "http://localhost:4000"
    litellm_model: str = "gpt-4o-mini"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    chat_provider: str = "slack"
    slack_bot_token: str | None = None
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

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        if len(value) != 44:
            raise ValueError("secret_key must be a 44-character Fernet key")
        return value

    @property
    def dev_roles(self) -> frozenset[Role]:
        values = [item.strip().lower() for item in self.dev_principal_roles.split(",")]
        return frozenset(Role(value) for value in values if value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
