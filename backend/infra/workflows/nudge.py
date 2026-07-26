from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from core.domain.escalation import (
    EscalationContact,
    EscalationTarget,
    escalation_contacts_from_metadata,
)
from core.domain.graph import NodeKind
from core.domain.integrations import UserRef
from core.domain.status import StatusSource

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


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

    action: str  # "send" | "suppressed_unavailable" | "no_contact"
    recipient_chat_external_id: str | None = None
    recipient_display_name: str | None = None


def decide_escalation_delivery(
    *,
    target: EscalationTarget,
    developer_available: bool,
    developer_chat_external_id: str | None,
    contact: EscalationContact | None,
) -> EscalationDelivery:
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
    """Deliver one ladder rung: nudge the developer, or notify a pod contact."""
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

        target = EscalationTarget(payload.target)
        as_of = date.fromisoformat(payload.as_of)
        developer_available = True
        contact: EscalationContact | None = None
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
        )
        if delivery.action != "send":
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status=delivery.action,
            )

        nudge_message_id = await registry.status_collector().send_nudge(
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
            return contact
    return None


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
