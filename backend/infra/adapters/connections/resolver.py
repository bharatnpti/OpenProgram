"""Read tenant connections for adapters, with a short in-process cache.

Adapters resolve their connection on every call (a sync run makes many), so a
resolved connection is kept for a few seconds. Saving a connection through the
same process clears it at once; other processes see the change within the TTL.

A connection that cannot be read (the database is unreachable, or not yet
migrated) is treated as not set up, so the adapter falls back to the server's
settings instead of failing; the failure is logged by type only and retried
after a few seconds. A read that does not finish within a few seconds -- a
database that went away under an open pool makes it wait 30 s for a
connection -- counts as unreadable too, so a request is never held that long.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

from core.domain.connections import ConnectionValues
from core.ports.connections import ConnectionResolver

DEFAULT_TTL_SECONDS = 30.0
UNREADABLE_RETRY_SECONDS = 5.0
# The same budget the registry gives each readiness probe.
DEFAULT_READ_TIMEOUT_SECONDS = 3.0

_logger = structlog.get_logger(__name__)


@dataclass
class CachedConnectionResolver:
    inner: ConnectionResolver
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS
    clock: Callable[[], float] = time.monotonic
    _cache: dict[tuple[str, str], tuple[float, ConnectionValues | None]] = field(
        default_factory=dict
    )

    async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
        key = (tenant_id, connector)
        now = self.clock()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        try:
            async with asyncio.timeout(self.read_timeout_seconds):
                values = await self.inner.resolve(tenant_id, connector)
        except Exception as error:
            # Never the message: it can echo a connection string.
            _logger.warning(
                "connection_unreadable",
                tenant_id=tenant_id,
                connector=connector,
                error_type=type(error).__name__,
            )
            self._cache[key] = (now + UNREADABLE_RETRY_SECONDS, None)
            return None
        self._cache[key] = (now + self.ttl_seconds, values)
        return values

    def invalidate(self, tenant_id: str) -> None:
        for key in [key for key in self._cache if key[0] == tenant_id]:
            del self._cache[key]
