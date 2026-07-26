from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.domain.workflows import (
    InboundSweeperInput,
    InboundSweeperResult,
    ReplyCoalesceInput,
    ReplyCoalesceResult,
)

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


async def drain_conversation_activity(payload: ReplyCoalesceInput) -> ReplyCoalesceResult:
    """Drain all buffered inbound events for one conversation (retry-safe)."""
    registry = _service_registry()
    try:
        result = await registry.drain_inbound_conversation(
            payload.tenant_id,
            payload.conversation_key,
        )
        return ReplyCoalesceResult(
            tenant_id=payload.tenant_id,
            conversation_key=payload.conversation_key,
            processed=result.processed,
            passes=result.passes,
        )
    finally:
        await registry.close()


async def sweep_inbound_events_activity(payload: InboundSweeperInput) -> InboundSweeperResult:
    """Re-drain conversations whose events are stuck past the grace window."""
    registry = _service_registry()
    try:
        now = _optional_datetime(payload.now) or datetime.now(tz=UTC)
        return await registry.sweep_inbound_events(
            payload.tenant_id,
            payload.grace_seconds,
            now,
        )
    finally:
        await registry.close()


def _optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
