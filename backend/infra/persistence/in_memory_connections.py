from __future__ import annotations

from dataclasses import dataclass, field, replace

from core.domain.connections import Connection, ConnectionTestOutcome


@dataclass
class InMemoryConnectionRepository:
    """Connections for memory mode: gone when the process stops."""

    _connections: dict[tuple[str, str], Connection] = field(default_factory=dict)

    async def get(self, tenant_id: str, connector: str) -> Connection | None:
        return self._connections.get((tenant_id, connector))

    async def list(self, tenant_id: str) -> list[Connection]:
        return [
            connection
            for (tenant, _connector), connection in sorted(self._connections.items())
            if tenant == tenant_id
        ]

    async def save(self, connection: Connection) -> None:
        self._connections[(connection.tenant_id, connection.connector)] = connection

    async def record_test(
        self, tenant_id: str, connector: str, outcome: ConnectionTestOutcome
    ) -> None:
        existing = self._connections.get((tenant_id, connector))
        if existing is not None:
            self._connections[(tenant_id, connector)] = replace(existing, last_test=outcome)

    async def delete(self, tenant_id: str, connector: str) -> bool:
        return self._connections.pop((tenant_id, connector), None) is not None
