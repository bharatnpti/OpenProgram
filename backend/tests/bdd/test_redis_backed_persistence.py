from __future__ import annotations

import os

import pytest
from pytest_bdd import scenarios

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPENPROGRAM_RUN_INTEGRATION") != "1",
        reason="set OPENPROGRAM_RUN_INTEGRATION=1 or run make integration",
    ),
]

scenarios("features/redis_backed_persistence.feature")
