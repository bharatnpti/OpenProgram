from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from core.domain.inbound import InboundChatEvent
from core.domain.messaging import ChatUserRef, InboundMessage
from core.ports.reply_processing import ReplyProcessor
from core.ports.repositories import InboundChatEventRepository

MAX_DRAIN_PASSES = 5


@dataclass(frozen=True, kw_only=True)
class CoalescedReply:
    message: InboundMessage
    source_event_ids: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ReplyDrainResult:
    tenant_id: str
    conversation_key: str
    processed: int
    passes: int
    status: str
    message_id: str | None = None


def coalesce_inbound_events(events: Sequence[InboundChatEvent]) -> CoalescedReply | None:
    """Combine buffered events for one conversation into a single reply.

    Ordering is strictly by ``received_at`` with the provider message id as a
    stable tiebreaker. The combined text joins oldest→newest and correlation,
    user, and thread come from the latest event so downstream idempotency keys
    on the most recent message.
    """
    if not events:
        return None
    ordered = sorted(events, key=lambda event: (event.received_at, event.message_ref))
    latest = ordered[-1]
    combined_text = "\n".join(event.text for event in ordered)
    thread_id = latest.chat_thread_ref or _thread_from_conversation_key(latest.conversation_key)
    message = InboundMessage(
        tenant_id=latest.tenant_id,
        user=ChatUserRef(tenant_id=latest.tenant_id, external_id=latest.chat_user_ref),
        text=combined_text,
        thread_id=thread_id,
        message_id=latest.message_ref,
        correlation_id=latest.correlation_id,
        received_at=latest.received_at,
        metadata={"source": latest.provider},
    )
    source_ids = tuple(event.id for event in ordered if event.id is not None)
    return CoalescedReply(message=message, source_event_ids=source_ids)


async def run_reply_debounce(
    received_before_timeout: Callable[[], Awaitable[bool]],
) -> int:
    """Reset-on-message quiet-window loop.

    ``received_before_timeout`` returns ``True`` when a new event arrived before
    the debounce window elapsed (reset the timer) and ``False`` when the window
    passed with no new event (quiet -> drain). Returns the number of resets, i.e.
    how many additional events extended the window.
    """
    resets = 0
    while await received_before_timeout():
        resets += 1
    return resets


class ReplyIngestionService:
    """Durable, retry-safe orchestration of coalesced inbound replies.

    Events stay ``processed_at IS NULL`` until the pipeline fully succeeds, so a
    transient failure (e.g. an LLM step) leaves the reply buffered for retry
    rather than lost. This removes the R3 "stuck as ignored" failure mode.
    """

    def __init__(
        self,
        *,
        repository: InboundChatEventRepository,
        processor: ReplyProcessor,
    ) -> None:
        self._repository = repository
        self._processor = processor

    async def drain_conversation(
        self,
        *,
        tenant_id: str,
        conversation_key: str,
        now: datetime | None = None,
        max_passes: int = MAX_DRAIN_PASSES,
    ) -> ReplyDrainResult:
        processed_total = 0
        passes = 0
        status = "empty"
        message_id: str | None = None
        while passes < max_passes:
            events = await self._repository.list_unprocessed_for_conversation(
                tenant_id, conversation_key
            )
            coalesced = coalesce_inbound_events(events)
            if coalesced is None:
                break
            outcome = await self._processor.process_reply(coalesced.message)
            # Mark processed only after the pipeline succeeded; a raised
            # exception above leaves the events buffered for a durable retry.
            await self._repository.mark_processed(
                tenant_id,
                coalesced.source_event_ids,
                now or datetime.now(tz=UTC),
            )
            processed_total += len(coalesced.source_event_ids)
            passes += 1
            status = outcome.status
            message_id = outcome.message_id
        return ReplyDrainResult(
            tenant_id=tenant_id,
            conversation_key=conversation_key,
            processed=processed_total,
            passes=passes,
            status=status,
            message_id=message_id,
        )


def _thread_from_conversation_key(conversation_key: str) -> str:
    _, _, thread = conversation_key.partition(":")
    return thread or conversation_key
