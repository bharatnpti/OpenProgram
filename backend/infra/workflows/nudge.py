from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from core.domain.escalation import (
    EscalationContact,
    EscalationTarget,
    escalation_contacts_from_metadata,
    with_member_identity,
)
from core.domain.graph import NodeKind
from core.domain.inbound import conversation_key
from core.domain.integrations import UserRef
from core.domain.status import CheckIn, CheckInCorrelation, StatusSource

if TYPE_CHECKING:
    from core.application.status_collector import StatusCollector
    from core.ports.repositories import GraphRepository
    from infra.registry import ServiceRegistry

# Rung outcome for a person who has answered the open check-in, though their
# reply is not finalized yet. Deliberately not "already_replied": that ends the
# ladder, and the ladder's close-out is what finalizes the accumulated reply.
SUPPRESSED_REPLIED = "suppressed_replied"


@dataclass(frozen=True, kw_only=True)
class EscalationStepPayload:
    """A single ladder rung, JSON-native for the workflow engines."""

    target: str
    wait_seconds: int


@dataclass(frozen=True, kw_only=True)
class NudgeInput:
    tenant_id: str
    correlation_id: str
    as_of: str
    developer_name: str | None = None
    chat_external_id: str | None = None
    reply_wait_seconds: int = 14400
    final_reply_wait_seconds: int = 28800
    escalation_steps: tuple[EscalationStepPayload, ...] = ()

    def resolved_steps(self) -> tuple[EscalationStepPayload, ...]:
        """Escalation ladder, defaulting to the historical single developer nudge."""
        if self.escalation_steps:
            return self.escalation_steps
        return (
            EscalationStepPayload(
                target=EscalationTarget.DEVELOPER.value,
                wait_seconds=self.reply_wait_seconds,
            ),
        )

    def step_input(self, nudge_number: int, target: str) -> EscalationStepInput:
        return EscalationStepInput(
            tenant_id=self.tenant_id,
            correlation_id=self.correlation_id,
            as_of=self.as_of,
            developer_name=self.developer_name,
            chat_external_id=self.chat_external_id,
            nudge_number=nudge_number,
            target=target,
        )


@dataclass(frozen=True, kw_only=True)
class EscalationStepInput:
    tenant_id: str
    correlation_id: str
    as_of: str
    developer_name: str | None
    chat_external_id: str | None
    nudge_number: int
    target: str


@dataclass(frozen=True, kw_only=True)
class EscalationDelivery:
    """Pure decision: whether/where to deliver a given escalation rung."""

    action: str  # "send" | "suppressed_replied" | "suppressed_unavailable" | "no_contact"
    recipient_chat_external_id: str | None = None
    recipient_display_name: str | None = None


def decide_escalation_delivery(
    *,
    target: EscalationTarget,
    developer_available: bool,
    developer_chat_external_id: str | None,
    contact: EscalationContact | None,
    developer_replied: bool = False,
) -> EscalationDelivery:
    if developer_replied:
        # Every rung is for silence: the nudge asks for an update the person
        # already gave, and the SM/manager notice says they haven't completed
        # the check-in. Once they have answered at all -- even with a
        # clarification still pending -- no rung goes out.
        return EscalationDelivery(action=SUPPRESSED_REPLIED)
    if target is EscalationTarget.DEVELOPER:
        if not developer_available:
            return EscalationDelivery(action="suppressed_unavailable")
        return EscalationDelivery(
            action="send",
            recipient_chat_external_id=developer_chat_external_id,
        )
    if contact is None:
        return EscalationDelivery(action="no_contact")
    return EscalationDelivery(
        action="send",
        recipient_chat_external_id=contact.chat_external_id,
        recipient_display_name=contact.display_name,
    )


@dataclass(frozen=True, kw_only=True)
class NudgeResult:
    tenant_id: str
    developer_id: str
    correlation_id: str
    status: str
    nudge_message_id: str | None = None
    terminal_source: str | None = None


async def send_checkin_nudge_activity(payload: NudgeInput) -> NudgeResult:
    registry = _service_registry()
    try:
        repository = registry.status_repository()
        checkin = await repository.checkin_by_correlation(
            payload.tenant_id,
            payload.correlation_id,
        )
        if checkin is None:
            raise ValueError("cannot nudge without a recorded check-in")
        if checkin.replied_at is not None:
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status="already_replied",
            )

        nudge = await repository.checkin_nudge_for(payload.tenant_id, payload.correlation_id, 1)
        if nudge is not None:
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status="already_nudged",
                nudge_message_id=nudge.outbound_message_id
                or _pending_nudge_message_id(payload.correlation_id),
            )

        nudge_message_id = await registry.status_collector().send_nudge(
            tenant_id=payload.tenant_id,
            correlation_id=payload.correlation_id,
            developer_name=payload.developer_name,
            chat_external_id=payload.chat_external_id,
        )
        return NudgeResult(
            tenant_id=payload.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=payload.correlation_id,
            status="nudged",
            nudge_message_id=nudge_message_id,
        )
    finally:
        await registry.close()


async def send_escalation_step_activity(payload: EscalationStepInput) -> NudgeResult:
    """Deliver one ladder rung: nudge the developer, or notify a pod contact.

    Rungs are for silence. Someone who has answered the open check-in at all is
    never nudged or escalated as a non-responder, even while a clarification is
    pending; the rung reports ``suppressed_replied`` and the ladder runs on to
    its close-out, which finalizes their accumulated reply. Someone with no
    reply at all goes up the ladder exactly as before.
    """
    registry = _service_registry()
    try:
        repository = registry.status_repository()
        checkin = await repository.checkin_by_correlation(
            payload.tenant_id,
            payload.correlation_id,
        )
        if checkin is None:
            raise ValueError("cannot nudge without a recorded check-in")
        if checkin.replied_at is not None:
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status="already_replied",
            )

        collector = registry.status_collector()
        target = EscalationTarget(payload.target)
        as_of = date.fromisoformat(payload.as_of)
        developer_replied = await _developer_has_replied(registry, collector, checkin)
        developer_available = True
        contact: EscalationContact | None = None
        # A replied person's rung is suppressed whoever it is for: nothing to look up.
        if not developer_replied:
            if target is EscalationTarget.DEVELOPER:
                developer_available = await _developer_available(
                    registry, payload.tenant_id, checkin.developer_id, as_of
                )
            else:
                contact = await _resolve_pod_contact(
                    registry, payload.tenant_id, checkin.developer_id, target, as_of
                )

        delivery = decide_escalation_delivery(
            target=target,
            developer_available=developer_available,
            developer_chat_external_id=payload.chat_external_id or checkin.developer_id,
            contact=contact,
            developer_replied=developer_replied,
        )
        if delivery.action != "send":
            if delivery.action == SUPPRESSED_REPLIED:
                _log_suppressed_for_reply(payload, checkin, target)
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status=delivery.action,
            )

        nudge_message_id = await collector.send_nudge(
            tenant_id=payload.tenant_id,
            correlation_id=payload.correlation_id,
            developer_name=payload.developer_name,
            chat_external_id=payload.chat_external_id,
            nudge_number=payload.nudge_number,
            target=target,
            recipient_chat_external_id=delivery.recipient_chat_external_id,
            recipient_display_name=delivery.recipient_display_name,
        )
        return NudgeResult(
            tenant_id=payload.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=payload.correlation_id,
            status="escalated" if target is not EscalationTarget.DEVELOPER else "nudged",
            nudge_message_id=nudge_message_id,
        )
    finally:
        await registry.close()


async def _developer_has_replied(
    registry: ServiceRegistry, collector: StatusCollector, checkin: CheckIn
) -> bool:
    """Whether the person has answered this open check-in at all.

    ``replied_at`` stays unset until a reply is finalized, so on its own it
    reads a person whose reply drew a clarification as silent. Their reply is
    on record as a user turn on the check-in's correlation; a reply that has
    arrived but still waits in the inbound buffer counts too.
    """
    if await collector.has_reply_on_record(checkin):
        return True
    return await _reply_waiting_in_inbound_buffer(registry, checkin)


async def _reply_waiting_in_inbound_buffer(registry: ServiceRegistry, checkin: CheckIn) -> bool:
    """A message from the person in the check-in's DM, received but not yet handled.

    Inbound DMs wait for the coalesce debounce or the sweeper before the
    collector records them, so a reply can be minutes old and on no record
    yet. Looks in the DM itself and in a thread on the check-in message.
    """
    correlation = await registry.status_repository().checkin_correlation_by_id(
        checkin.tenant_id,
        checkin.correlation_id,
    )
    if correlation is None:
        return False
    inbound = registry.inbound_chat_event_repository()
    for thread_ref in _reply_thread_refs(correlation):
        events = await inbound.list_unprocessed_for_conversation(
            checkin.tenant_id,
            conversation_key(checkin.tenant_id, thread_ref),
        )
        if any(
            event.chat_user_ref == correlation.chat_user_ref
            and event.received_at >= checkin.asked_at
            for event in events
        ):
            return True
    return False


def _reply_thread_refs(correlation: CheckInCorrelation) -> tuple[str, ...]:
    refs = (correlation.chat_thread_ref, correlation.outbound_message_id)
    return tuple(dict.fromkeys(ref.strip() for ref in refs if ref and ref.strip()))


def _log_suppressed_for_reply(
    payload: EscalationStepInput, checkin: CheckIn, target: EscalationTarget
) -> None:
    # Imported here, not at module load: the workflow definitions import this
    # module, and the workflow runtime loads them inside a sandbox that refuses
    # structlog's import-time randomness (through rich).
    import structlog

    structlog.get_logger(__name__).info(
        "checkin_escalation_suppressed_replied",
        tenant_id=payload.tenant_id,
        developer_id=checkin.developer_id,
        correlation_id=payload.correlation_id,
        nudge_number=payload.nudge_number,
        escalation_target=target.value,
    )


async def _developer_available(
    registry: ServiceRegistry, tenant_id: str, developer_id: str, as_of: date
) -> bool:
    availability = registry.availability_service()
    result = await availability.availability_for(
        UserRef(tenant_id=tenant_id, external_id=developer_id),
        as_of,
        default_timezone=registry.settings.tenant_default_timezone,
    )
    return result.available


async def _resolve_pod_contact(
    registry: ServiceRegistry,
    tenant_id: str,
    developer_id: str,
    target: EscalationTarget,
    as_of: date,
) -> EscalationContact | None:
    graph = registry.graph_repository()
    memberships = await graph.active_developer_memberships(tenant_id, developer_id, as_of)
    for edge in memberships:
        candidate_id = edge.to_node_id if edge.from_node_id == developer_id else edge.from_node_id
        node = await graph.get_node(tenant_id, candidate_id)
        if node is None or node.kind is not NodeKind.POD:
            continue
        contact = escalation_contacts_from_metadata(node.metadata).contact_for(target)
        if contact is not None:
            return await _member_backed_contact(registry, graph, tenant_id, contact)
    return None


async def _member_backed_contact(
    registry: ServiceRegistry,
    graph: GraphRepository,
    tenant_id: str,
    contact: EscalationContact,
) -> EscalationContact:
    """Deliver to the picked member's current chat id, not the one saved with it."""
    if contact.member_id is None:
        return contact
    member = await graph.get_node(tenant_id, contact.member_id)
    if member is None or member.kind is not NodeKind.DEVELOPER:
        return contact
    link = await registry.identity_link_repository().get_identity_link(tenant_id, contact.member_id)
    return with_member_identity(
        contact,
        member_name=member.name,
        chat_user_id=link.chat_user_id.strip() if link and link.chat_user_id else None,
    )


async def close_checkin_non_response_activity(payload: NudgeInput) -> NudgeResult:
    registry = _service_registry()
    try:
        repository = registry.status_repository()
        checkin = await repository.checkin_by_correlation(
            payload.tenant_id,
            payload.correlation_id,
        )
        if checkin is None:
            raise ValueError("cannot close non-response without a recorded check-in")
        if checkin.replied_at is not None:
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status="already_replied",
            )

        as_of = date.fromisoformat(payload.as_of)
        existing_status = await repository.latest_developer_status(
            payload.tenant_id,
            checkin.developer_id,
            as_of,
        )
        if (
            existing_status is not None
            and existing_status.as_of == as_of
            and existing_status.source
            in {StatusSource.INFERRED, StatusSource.STALE, StatusSource.UNKNOWN}
        ):
            # The day was closed by an earlier check-in; this one closes too, so
            # a reply from now on is a late update, not an answer (G9).
            await repository.consume_checkin_correlation(
                payload.tenant_id, payload.correlation_id, datetime.now(tz=UTC)
            )
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status="already_closed",
                terminal_source=existing_status.source.value,
            )

        terminal_status = await registry.status_collector().record_non_response(
            tenant_id=payload.tenant_id,
            developer_id=checkin.developer_id,
            as_of=as_of,
            developer_name=payload.developer_name,
            correlation_id=payload.correlation_id,
        )
        return NudgeResult(
            tenant_id=payload.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=payload.correlation_id,
            status="closed",
            terminal_source=terminal_status.source.value,
        )
    finally:
        await registry.close()


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())


def _pending_nudge_message_id(correlation_id: str) -> str:
    return f"pending-nudge-{correlation_id}-1"
