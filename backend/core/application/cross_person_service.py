from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestResolution,
    CrossPersonRequestStatus,
    new_cross_person_request,
)
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from core.domain.llm import LlmRequest
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.ports.chat import ChatProvider
from core.ports.directory import DirectoryUserRepository
from core.ports.llm import LlmProvider
from core.ports.repositories import CrossPersonRequestRepository, TimeSeriesRepository

_logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class CrossPersonRequestService:
    repository: CrossPersonRequestRepository
    chat_provider: ChatProvider
    directory_repository: DirectoryUserRepository
    time_series_repository: TimeSeriesRepository | None = None
    llm_provider: LlmProvider | None = None
    model: str = "test-model"
    auto_notify: bool = False

    async def record_from_checkin(
        self,
        *,
        tenant_id: str,
        requester_id: str,
        requester_chat_ref: str | None,
        source_correlation_id: str,
        resolutions: Iterable[CrossPersonRequestResolution],
        observed_at: datetime | None = None,
    ) -> list[CrossPersonRequest]:
        created: list[CrossPersonRequest] = []
        for index, resolution in enumerate(resolutions):
            request = new_cross_person_request(
                tenant_id=tenant_id,
                id=_request_id(source_correlation_id, index, resolution),
                requester_id=requester_id,
                requester_chat_ref=requester_chat_ref,
                source_correlation_id=source_correlation_id,
                resolution=resolution,
                created_at=observed_at,
            )
            stored = await self.repository.create(request)
            transition = (
                "needs_resolution"
                if stored.status is CrossPersonRequestStatus.NEEDS_RESOLUTION
                else "opened"
            )
            await self._append_fact(stored, transition=transition)
            if self.auto_notify and stored.status is CrossPersonRequestStatus.OPEN:
                stored = await self.notify(stored)
            created.append(stored)
        return created

    async def notify(self, request: CrossPersonRequest) -> CrossPersonRequest:
        if request.counterpart_id is None:
            return request
        if request.notify_message_id is not None and request.notify_correlation_id is not None:
            return request
        user = await self.directory_repository.get(request.tenant_id, request.counterpart_id)
        chat_user = ChatUserRef(
            tenant_id=request.tenant_id,
            external_id=request.counterpart_id,
            display_name=(
                user.display_name if user is not None else request.counterpart_display_name
            ),
        )
        notify_correlation_id = f"xreq-{request.id}"
        message_id = await self.chat_provider.send_dm(
            chat_user,
            OutboundMessage(
                tenant_id=request.tenant_id,
                text=_counterpart_message(request),
                correlation_id=notify_correlation_id,
                metadata={
                    "purpose": "cross_person_request",
                    "idempotency_key": f"xreq-notify:{request.id}",
                    "request_id": request.id,
                    "requester_id": request.requester_id,
                },
            ),
        )
        updated = await self.repository.record_notification(
            request.tenant_id,
            request.id,
            notify_message_id=message_id,
            notify_correlation_id=notify_correlation_id,
            updated_at=datetime.now(tz=UTC),
        )
        return updated or request

    async def handle_counterpart_reply(
        self,
        message: InboundMessage,
        request: CrossPersonRequest,
    ) -> CrossPersonRequest:
        next_status = await self._classify_counterpart_reply(message, request)
        updated = await self.repository.update_status(
            request.tenant_id,
            request.id,
            next_status,
            message.received_at,
        )
        stored = updated or request
        await self._append_fact(stored, transition=next_status.value)
        if next_status is CrossPersonRequestStatus.RESOLVED:
            await self._notify_requester_resolved(stored)
        return stored

    async def update_status(
        self,
        tenant_id: str,
        request_id: str,
        status: CrossPersonRequestStatus,
    ) -> CrossPersonRequest | None:
        updated = await self.repository.update_status(
            tenant_id,
            request_id,
            status,
            datetime.now(tz=UTC),
        )
        if updated is not None:
            await self._append_fact(updated, transition=status.value)
            if status is CrossPersonRequestStatus.RESOLVED:
                await self._notify_requester_resolved(updated)
        return updated

    async def get(
        self,
        tenant_id: str,
        request_id: str,
    ) -> CrossPersonRequest | None:
        return await self.repository.get(tenant_id, request_id)

    async def list_portfolio(
        self,
        tenant_id: str,
        status: CrossPersonRequestStatus | None = None,
    ) -> list[CrossPersonRequest]:
        if status is None:
            return await self.repository.list_open(tenant_id)
        if status in {
            CrossPersonRequestStatus.OPEN,
            CrossPersonRequestStatus.ACKNOWLEDGED,
            CrossPersonRequestStatus.NEEDS_RESOLUTION,
        }:
            return [
                request
                for request in await self.repository.list_open(tenant_id)
                if request.status is status
            ]
        return []

    async def list_inbox(
        self,
        tenant_id: str,
        counterpart_id: str,
        statuses: Iterable[CrossPersonRequestStatus] | None = None,
    ) -> list[CrossPersonRequest]:
        status_tuple = tuple(statuses) if statuses is not None else None
        return await self.repository.list_for_counterpart(
            tenant_id,
            counterpart_id,
            statuses=status_tuple,
        )

    async def open_request_for_counterpart_reply(
        self,
        message: InboundMessage,
    ) -> CrossPersonRequest | None:
        candidates = await self.repository.list_for_counterpart(
            message.tenant_id,
            message.user.external_id,
            statuses=(
                CrossPersonRequestStatus.OPEN,
                CrossPersonRequestStatus.ACKNOWLEDGED,
            ),
        )
        return candidates[0] if len(candidates) == 1 else None

    async def _classify_counterpart_reply(
        self,
        message: InboundMessage,
        request: CrossPersonRequest,
    ) -> CrossPersonRequestStatus:
        if _reply_resolves(message.text):
            return CrossPersonRequestStatus.RESOLVED
        if _reply_acknowledges(message.text):
            return CrossPersonRequestStatus.ACKNOWLEDGED
        if self.llm_provider is None:
            return CrossPersonRequestStatus.ACKNOWLEDGED
        prompt = (
            "Classify this reply to a cross-person work request as JSON with one key, status. "
            "Use resolved only when the reply says the requested review/input/dependency is done. "
            "Use acknowledged when the reply only confirms ownership or gives a non-final update. "
            f"Request kind: {request.kind.value}. "
            f"Request note: {request.note}. Reply: {message.text}"
        )
        response = await self.llm_provider.complete(
            LlmRequest(
                tenant_id=message.tenant_id,
                prompt=prompt,
                model=self.model,
                correlation_id=message.correlation_id,
                metadata={
                    "service": "cross_person_request",
                    "purpose": "classify_counterpart_reply",
                    "request_id": request.id,
                },
            )
        )
        try:
            parsed = json.loads(response.text)
        except json.JSONDecodeError:
            _logger.warning(
                "cross_person_reply_json_decode_failed",
                tenant_id=message.tenant_id,
                request_id=request.id,
                trace_id=response.trace_id,
            )
            return CrossPersonRequestStatus.ACKNOWLEDGED
        status = parsed.get("status") if isinstance(parsed, dict) else None
        if status == CrossPersonRequestStatus.RESOLVED.value:
            return CrossPersonRequestStatus.RESOLVED
        return CrossPersonRequestStatus.ACKNOWLEDGED

    async def _notify_requester_resolved(self, request: CrossPersonRequest) -> None:
        if request.requester_chat_ref is None:
            return
        counterpart = (
            request.counterpart_display_name or request.counterpart_id or "The counterpart"
        )
        await self.chat_provider.send_dm(
            ChatUserRef(tenant_id=request.tenant_id, external_id=request.requester_chat_ref),
            OutboundMessage(
                tenant_id=request.tenant_id,
                text=(
                    f"{counterpart} marked your {request.kind.value} "
                    f"request resolved: {request.note}"
                ),
                correlation_id=f"xreq-resolved-{request.id}",
                metadata={
                    "purpose": "cross_person_request_resolved",
                    "idempotency_key": f"xreq-resolved:{request.id}",
                    "request_id": request.id,
                },
            ),
        )

    async def _append_fact(self, request: CrossPersonRequest, *, transition: str) -> None:
        if self.time_series_repository is None:
            return
        entity_ref = EntityRef(
            tenant_id=request.tenant_id,
            kind=NodeKind.DEVELOPER,
            id=request.counterpart_id or request.requester_id,
        )
        payload: dict[str, JsonScalar] = {
            "request_id": request.id,
            "source_checkin_id": request.source_correlation_id,
            "reporter_id": request.requester_id,
            "requester_id": request.requester_id,
            "referenced_person_id": request.counterpart_id,
            "referenced_person_name": request.counterpart_display_name,
            "counterpart_id": request.counterpart_id,
            "counterpart_name": request.counterpart_display_name,
            "kind": request.kind.value,
            "dependency_kind": _fact_kind(request),
            "status": request.status.value,
            "dependency_status": _fact_status(request.status),
            "transition": transition,
            "summary": request.note,
            "note": request.note,
            "first_seen_at": request.created_at.isoformat(),
            "last_seen_at": request.updated_at.isoformat(),
            "needs_resolution": request.status is CrossPersonRequestStatus.NEEDS_RESOLUTION,
        }
        observed_at = (
            request.created_at
            if transition in {"opened", "needs_resolution"}
            else request.updated_at
        )
        await self.time_series_repository.append_fact_once(
            FactEvent(
                tenant_id=request.tenant_id,
                source="cross_person_request",
                entity_ref=entity_ref,
                payload=payload,
                observed_at=observed_at,
                correlation_id=f"cross-person:{request.id}:{transition}",
            )
        )


def _request_id(
    source_correlation_id: str,
    index: int,
    resolution: CrossPersonRequestResolution,
) -> str:
    digest = hashlib.sha256(
        "|".join(
            (
                source_correlation_id,
                str(index),
                resolution.mention.raw_name,
                resolution.mention.kind,
                resolution.mention.note,
                resolution.mention.email or "",
            )
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"xreq-{digest}"


def _counterpart_message(request: CrossPersonRequest) -> str:
    requester = request.requester_id
    return (
        f"{requester} needs your {request.kind.value}: {request.note}\n"
        "Reply here with an acknowledgement, or say when it is done."
    )


def _fact_kind(request: CrossPersonRequest) -> str:
    if request.kind.value == "review":
        return "needs_review"
    if request.kind.value == "input":
        return "needs_input"
    if any(word in request.note.casefold() for word in ("block", "blocked", "blocking")):
        return "blocked_by"
    return "waiting_on"


def _fact_status(status: CrossPersonRequestStatus) -> str:
    if status is CrossPersonRequestStatus.RESOLVED:
        return "resolved"
    if status is CrossPersonRequestStatus.NEEDS_RESOLUTION:
        return "stale"
    return "open"


def _reply_acknowledges(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    exact_acknowledgements = {
        "ok",
        "okay",
        "ack",
        "acknowledged",
        "on it",
        "looking",
        "will do",
    }
    return normalized in exact_acknowledgements or any(
        phrase in normalized
        for phrase in (
            "i'll take",
            "i will take",
            "i'll look",
            "i will look",
            "on it",
            "will review",
            "can review",
        )
    )


def _reply_resolves(text: str) -> bool:
    normalized = text.casefold()
    negated = (
        "not done",
        "not resolved",
        "not yet",
        "still working",
        "still reviewing",
        "not ready",
        "not finished",
    )
    if any(phrase in normalized for phrase in negated):
        return False
    resolved = (
        "done",
        "resolved",
        "completed",
        "finished",
        "reviewed",
        "approved",
        "merged",
        "sent",
        "shared",
        "provided",
        "unblocked",
    )
    return any(phrase in normalized for phrase in resolved)
