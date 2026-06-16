from __future__ import annotations

import pytest

from config.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        runtime_mode="memory",
        dev_principal_roles="admin",
    )
