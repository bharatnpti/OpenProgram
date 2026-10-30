from __future__ import annotations

import psycopg
import pytest
from dbos import DBOS

from config.settings import Settings
from tests.fixtures.stack_database import guard_async_connect, guard_connect, guard_dbos_launch


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        runtime_mode="memory",
        dev_principal_roles="admin",
    )


@pytest.fixture(autouse=True)
def _no_test_reaches_the_stack_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that launches DBOS or opens a connection on the developer's stack.

    The default URL is that stack's database, so a test that builds settings without
    its own URL and reaches a workflow adapter would otherwise launch there. See
    tests/fixtures/stack_database.py.
    """
    monkeypatch.setattr(DBOS, "launch", guard_dbos_launch(DBOS.launch))
    # psycopg.connect is bound to Connection.connect when psycopg is imported, and
    # DBOS's engine opens its connections through it.
    monkeypatch.setattr(psycopg, "connect", guard_connect(psycopg.connect))
    monkeypatch.setattr(psycopg.Connection, "connect", guard_connect(psycopg.Connection.connect))
    monkeypatch.setattr(
        psycopg.AsyncConnection,
        "connect",
        guard_async_connect(psycopg.AsyncConnection.connect),
    )
