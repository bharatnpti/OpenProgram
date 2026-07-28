from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DeadLetterStatus(StrEnum):
    """Lifecycle of a dead-lettered workflow event.

    ``OPEN`` means the event exhausted its durable retries and awaits operator
    action; ``REARMED`` means an operator re-triggered processing for it.
    """

    OPEN = "open"
    REARMED = "rearmed"


@dataclass(frozen=True, kw_only=True)
class DeadLetter:
    """A stuck inbound event whose durable retries were exhausted.

    Privacy: this record carries only identifiers and diagnostics -- never raw
    DM/reply content. It mirrors the ``InboundChatEventRepository`` rule so a
    dead-letter row can be listed in operator views without leaking content.
    """

    id: str
    tenant_id: str
    kind: str
    conversation_key: str
    event_ids: tuple[str, ...]
    reason: str
    attempts: int
    first_seen_at: datetime
    dead_lettered_at: datetime
    status: DeadLetterStatus = DeadLetterStatus.OPEN
    rearmed_at: datetime | None = None
