from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from core.domain.dead_letter import DeadLetter, DeadLetterStatus
from core.ports.repositories import DeadLetterRepository


class DeadLetterService:
    """Record and re-arm workflow events whose durable retries were exhausted.

    Ids are derived deterministically from ``conversation_key`` and the first
    time the burst was seen, so re-sweeps upsert the same row instead of piling
    up duplicates.
    """

    def __init__(self, repository: DeadLetterRepository) -> None:
        self._repository = repository

    async def record_inbound(
        self,
        *,
        tenant_id: str,
        conversation_key: str,
        event_ids: Sequence[str],
        reason: str,
        attempts: int,
        first_seen_at: datetime,
        now: datetime,
    ) -> DeadLetter:
        dead_letter = DeadLetter(
            id=self._inbound_id(conversation_key, first_seen_at),
            tenant_id=tenant_id,
            kind="inbound_reply",
            conversation_key=conversation_key,
            event_ids=tuple(event_ids),
            reason=reason,
            attempts=attempts,
            first_seen_at=first_seen_at,
            dead_lettered_at=now,
        )
        await self._repository.record_dead_letter(dead_letter)
        return dead_letter

    async def list_open(self, tenant_id: str, limit: int = 100) -> list[DeadLetter]:
        return await self._repository.list_open_dead_letters(tenant_id, limit)

    async def count_open(self, tenant_id: str) -> int:
        return await self._repository.count_open_dead_letters(tenant_id)

    async def rearm(self, tenant_id: str, id: str, now: datetime) -> DeadLetter | None:
        return await self._repository.mark_dead_letter_rearmed(tenant_id, id, now)

    @staticmethod
    def _inbound_id(conversation_key: str, first_seen_at: datetime) -> str:
        return f"{conversation_key}:{first_seen_at.isoformat()}"


__all__ = ["DeadLetterService", "DeadLetterStatus"]
