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


def test_settings_fail_fast_on_invalid_secret_key() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key="too-short")


def test_settings_validate_provider_selectors() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, chat_provider="teams")
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, llm_provider="gemini")
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, workflow_provider="dbos")
