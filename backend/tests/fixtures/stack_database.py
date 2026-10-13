"""The one database a test must never reach: the one the developer's own stack runs on.

The settings fall back to it (``DEFAULT_DATABASE_URL``), docker-compose publishes it
on this machine, and a launch of DBOS on it registers the code under test as the
stack's application version, after which the stack's scheduled runs wait for a worker
that never comes. A test that wants a database brings its own URL: a copy, a
throwaway container, an address nothing listens on.

``conftest.py`` installs these guards around every test. They raise through
``pytest.fail``, which is not an ``Exception``: the code under test often catches
``Exception`` and carries on, and a refusal swallowed that way is no refusal.
``OPENPROGRAM_TEST_ALLOW_STACK_DATABASE=1`` switches them off for someone who means
to run against the stack.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import pytest
from psycopg.conninfo import conninfo_to_dict

STACK_DATABASE_NAME = "openprogram"
STACK_POSTGRES_PORT = "5432"
OPT_IN = "OPENPROGRAM_TEST_ALLOW_STACK_DATABASE"

# A throwaway container publishes on a port of its own and a copy has another
# name, so neither is mistaken for the stack. Nor is an address that only exists
# inside the compose network.
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
_ADDRESS_OPTIONS = ("host", "hostaddr", "port", "dbname", "service")


def reaches_stack_database(conninfo: str = "", **options: object) -> bool:
    """Whether this connection string names the stack's database on this machine."""
    address = {key: value for key, value in options.items() if key in _ADDRESS_OPTIONS}
    try:
        fields = conninfo_to_dict(conninfo or "", **address)
    except Exception:  # noqa: BLE001 - a string libpq cannot read connects nowhere
        return False
    host = str(fields.get("host") or fields.get("hostaddr") or "").split(",")[0]
    local = host in _LOCAL_HOSTS or host.startswith("/")
    port = str(fields.get("port") or STACK_POSTGRES_PORT)
    return local and port == STACK_POSTGRES_PORT and fields.get("dbname") == STACK_DATABASE_NAME


def refuse_stack_database(action: str, conninfo: str = "", **options: object) -> None:
    if os.getenv(OPT_IN) == "1":
        return
    if reaches_stack_database(conninfo, **options):
        pytest.fail(
            f"a test tried to {action} the local stack's database "
            f"('{STACK_DATABASE_NAME}' on this machine's Postgres). Give the test a "
            "database of its own: set OPENPROGRAM_DATABASE_URL, or the URL in the "
            f"config it builds. Set {OPT_IN}=1 only to run against the stack on purpose.",
            pytrace=False,
        )


def guard_dbos_launch(launch: Callable[[], None]) -> Callable[[], None]:
    def guarded() -> None:
        from infra.adapters.workflows import dbos as dbos_workflows

        runtime = dbos_workflows._configured_runtime
        if runtime is not None:
            refuse_stack_database("launch DBOS on", runtime.system_database_url)
        launch()

    guarded.stack_database_guard = True  # type: ignore[attr-defined]
    return guarded


def guard_connect[T](connect: Callable[..., T]) -> Callable[..., T]:
    def guarded(conninfo: str = "", **kwargs: object) -> T:
        refuse_stack_database("connect to", conninfo, **kwargs)
        return connect(conninfo, **kwargs)

    guarded.stack_database_guard = True  # type: ignore[attr-defined]
    return guarded


def guard_async_connect[T](
    connect: Callable[..., Awaitable[T]],
) -> Callable[..., Awaitable[T]]:
    async def guarded(conninfo: str = "", **kwargs: object) -> T:
        refuse_stack_database("connect to", conninfo, **kwargs)
        return await connect(conninfo, **kwargs)

    guarded.stack_database_guard = True  # type: ignore[attr-defined]
    return guarded
