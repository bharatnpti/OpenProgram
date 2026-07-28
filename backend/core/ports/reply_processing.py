from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.domain.messaging import InboundMessage


@dataclass(frozen=True, kw_only=True)
class ReplyProcessingOutcome:
    """Result of running the coalesced reply through the reply pipeline."""

    status: str
    message_id: str


class ReplyProcessor(Protocol):
    """Port for the concrete reply pipeline (correlation + cross-person + check-in).

    Implemented by the composition layer so the durable ingestion use case stays
    provider-neutral and engine-agnostic.
    """

    async def process_reply(self, message: InboundMessage) -> ReplyProcessingOutcome: ...
