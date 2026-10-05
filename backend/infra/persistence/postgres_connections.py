from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from opentelemetry import trace

from core.domain.connections import Connection, ConnectionTestOutcome

_tracer = trace.get_tracer("openprogram.persistence.connections")

_COLUMNS = """
    tenant_id, connector, enabled, settings, secret_keys, updated_at, updated_by,
    last_test_ok, last_test_message, last_test_at
"""


class AsyncSqlExecutor(Protocol):
    async def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]: ...


class PostgresConnectionRepository:
    """Connections in ``integration_connections``, one row per tenant and connector.

    Secret values are not here: only the names of the secret fields that hold
    one. The values live encrypted in ``connector_secrets``.
    """

    def __init__(self, executor: AsyncSqlExecutor) -> None:
        self._executor = executor

    async def get(self, tenant_id: str, connector: str) -> Connection | None:
        with _tracer.start_as_current_span("postgres.connections.get"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_COLUMNS}
                FROM integration_connections
                WHERE tenant_id = %s AND connector = %s
                """,
                (tenant_id, connector),
            )
        return _connection_from_row(rows[0]) if rows else None

    async def list(self, tenant_id: str) -> list[Connection]:
        with _tracer.start_as_current_span("postgres.connections.list"):
            rows = await self._executor.fetch(
                f"""
                SELECT {_COLUMNS}
                FROM integration_connections
                WHERE tenant_id = %s
                ORDER BY connector
                """,
                (tenant_id,),
            )
        return [_connection_from_row(row) for row in rows]

    async def save(self, connection: Connection) -> None:
        last_test = connection.last_test
        with _tracer.start_as_current_span("postgres.connections.save"):
            await self._executor.execute(
                """
                INSERT INTO integration_connections (
                    tenant_id, connector, enabled, settings, secret_keys, updated_at,
                    updated_by, last_test_ok, last_test_message, last_test_at
                )
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, connector) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    settings = EXCLUDED.settings,
                    secret_keys = EXCLUDED.secret_keys,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by,
                    last_test_ok = EXCLUDED.last_test_ok,
                    last_test_message = EXCLUDED.last_test_message,
                    last_test_at = EXCLUDED.last_test_at
                """,
                (
                    connection.tenant_id,
                    connection.connector,
                    connection.enabled,
                    json.dumps(dict(connection.settings), sort_keys=True),
                    sorted(connection.secret_keys),
                    connection.updated_at,
                    connection.updated_by,
                    last_test.ok if last_test is not None else None,
                    last_test.message if last_test is not None else None,
                    last_test.tested_at if last_test is not None else None,
                ),
            )

    async def record_test(
        self, tenant_id: str, connector: str, outcome: ConnectionTestOutcome
    ) -> None:
        with _tracer.start_as_current_span("postgres.connections.record_test"):
            await self._executor.execute(
                """
                UPDATE integration_connections
                SET last_test_ok = %s, last_test_message = %s, last_test_at = %s
                WHERE tenant_id = %s AND connector = %s
                """,
                (outcome.ok, outcome.message, outcome.tested_at, tenant_id, connector),
            )

    async def delete(self, tenant_id: str, connector: str) -> bool:
        with _tracer.start_as_current_span("postgres.connections.delete"):
            rows = await self._executor.fetch(
                """
                DELETE FROM integration_connections
                WHERE tenant_id = %s AND connector = %s
                RETURNING connector
                """,
                (tenant_id, connector),
            )
        return bool(rows)


def _connection_from_row(row: Mapping[str, object]) -> Connection:
    tested_at = row.get("last_test_at")
    ok = row.get("last_test_ok")
    last_test = (
        ConnectionTestOutcome(
            ok=bool(ok),
            message=str(row.get("last_test_message") or ""),
            tested_at=tested_at,
        )
        if isinstance(tested_at, datetime) and isinstance(ok, bool)
        else None
    )
    return Connection(
        tenant_id=str(row["tenant_id"]),
        connector=str(row["connector"]),
        enabled=bool(row["enabled"]),
        settings=_string_mapping(row.get("settings")),
        secret_keys=frozenset(_strings(row.get("secret_keys"))),
        updated_at=_datetime(row["updated_at"]),
        updated_by=str(row["updated_by"]),
        last_test=last_test,
    )


def _string_mapping(value: object) -> dict[str, str]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, str)}


def _strings(value: object) -> list[str]:
    if isinstance(value, list | tuple):
        return [item for item in value if isinstance(item, str)]
    return []


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    raise TypeError("integration_connections row has no updated_at timestamp")
