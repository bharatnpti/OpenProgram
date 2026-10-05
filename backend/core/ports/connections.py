from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from core.domain.connections import (
    Connection,
    ConnectionCheck,
    ConnectionTestOutcome,
    ConnectionValues,
    ConnectorSpec,
)


class ConnectionRepository(Protocol):
    """Each tenant's connection per connector: plain settings and which secrets are set."""

    async def get(self, tenant_id: str, connector: str) -> Connection | None: ...

    async def list(self, tenant_id: str) -> list[Connection]: ...

    async def save(self, connection: Connection) -> None:
        """Store the connection, replacing the tenant's earlier one for that connector."""
        ...

    async def record_test(
        self, tenant_id: str, connector: str, outcome: ConnectionTestOutcome
    ) -> None: ...

    async def delete(self, tenant_id: str, connector: str) -> bool:
        """Remove the connection. False when there was none."""
        ...


class ConnectorCatalog(Protocol):
    """The connectors this deployment can use, described by the adapters behind them."""

    def specs(self) -> tuple[ConnectorSpec, ...]: ...

    def environment_configured(self, connector: str) -> bool:
        """Whether the server's own settings already configure this connector.

        Those settings are used whenever the tenant has no enabled connection
        of its own for it.
        """
        ...


class ConnectionTester(Protocol):
    async def test(
        self, tenant_id: str, connector: str, values: Mapping[str, str]
    ) -> ConnectionCheck:
        """Try ``values`` against the system. Never raises for a failed check."""
        ...


class ConnectionResolver(Protocol):
    async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
        """The tenant's enabled connection, secrets decrypted; None when it has none."""
        ...
