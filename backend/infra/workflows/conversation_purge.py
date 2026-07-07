from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.domain.workflows import ConversationPurgeInput, ConversationPurgeResult

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


async def purge_conversation_turns_activity(
    payload: ConversationPurgeInput,
) -> ConversationPurgeResult:
    registry = _service_registry()
    try:
        now = _optional_datetime(payload.now) or datetime.now(tz=UTC)
        cutoff = now - timedelta(days=payload.retention_days)
        deleted_count = await registry.conversation_repository().purge_turns_older_than(
            payload.tenant_id,
            cutoff,
        )
        checkin_raw_cleared = (
            await registry.status_repository().purge_checkin_raw_replies_older_than(
                payload.tenant_id,
                cutoff,
            )
        )
        return ConversationPurgeResult(
            tenant_id=payload.tenant_id,
            cutoff=cutoff.isoformat(),
            deleted_count=deleted_count,
            checkin_raw_cleared=checkin_raw_cleared,
        )
    finally:
        await registry.close()


def _optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
