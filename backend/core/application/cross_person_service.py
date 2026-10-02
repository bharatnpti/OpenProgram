from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from core.application.status_collector import OUTBOUND_DM_MAX_CHARS
from core.domain.cross_person import (
    CrossPersonNotifyRetrySummary,
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

DEFAULT_NOTIFY_MAX_ATTEMPTS = 5
DEFAULT_NOTIFY_RETRY_BACKOFF_SECONDS = 300
DEFAULT_NOTIFY_RETRY_BATCH = 50


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, kw_only=True)
class CrossPersonRequestService:
    repository: CrossPersonRequestRepository
    chat_provider: ChatProvider
    directory_repository: DirectoryUserRepository
    time_series_repository: TimeSeriesRepository | None = None
    llm_provider: LlmProvider | None = None
    model: str = "test-model"
    auto_notify: bool = True
    # Every counterpart DM attempt, the first one included, counts towards the
    # limit. After attempt n the next one waits backoff * 2^(n-1).
    notify_max_attempts: int = DEFAULT_NOTIFY_MAX_ATTEMPTS
    notify_retry_backoff_seconds: int = DEFAULT_NOTIFY_RETRY_BACKOFF_SECONDS
    clock: Callable[[], datetime] = _utc_now

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
                stored = await self._notify_best_effort(stored)
            created.append(stored)
        return created

    async def _notify_best_effort(self, request: CrossPersonRequest) -> CrossPersonRequest:
        """Tell the counterpart, but never let a failed DM lose the request.

        By now the check-in reply is final and the request is stored. If the DM
        raised here, the remaining mentions in the same reply would never be
        recorded, and the reply's retry would stop at "already processed". The
        request stays open and visible to its requester instead, with no
        notification ids and the attempt counted, so the retry pass (or
        recording it again) sends the same DM later.
        """
        try:
            return await self.notify(request)
        except Exception as error:
            stored = await self.repository.get(request.tenant_id, request.id)
            _logger.warning(
                "cross_person_notify_failed",
                tenant_id=request.tenant_id,
                request_id=request.id,
                attempt=stored.notify_attempts if stored is not None else None,
                retry_due=(
                    stored.notify_next_attempt_at.isoformat()
                    if stored is not None and stored.notify_next_attempt_at is not None
                    else None
                ),
                error=type(error).__name__,
            )
            return stored or request

    async def notify(self, request: CrossPersonRequest) -> CrossPersonRequest:
        """Send the counterpart DM once, claiming the attempt before sending.

        A send that raises leaves the attempt counted and the next one
        scheduled, and the error propagates to the caller.
        """
        if not self._should_notify(request):
            return request
        claimed = await self._claim_attempt(request, self.clock())
        if claimed is None:
            # Another sender holds this attempt, the DM has been recorded since
            # the request was read, or the request is no longer open.
            return await self.repository.get(request.tenant_id, request.id) or request
        return await self._send_counterpart_dm(claimed)

    async def retry_failed_notifications(
        self,
        tenant_id: str,
        *,
        now: datetime | None = None,
        limit: int = DEFAULT_NOTIFY_RETRY_BATCH,
    ) -> CrossPersonNotifyRetrySummary:
        """Re-send counterpart DMs that failed, once each attempt is due.

        Nothing is sent while notification is off. Each request is claimed
        before its DM goes out, so a request that gained notification ids
        since it was listed, or that a concurrent pass claimed first, is
        skipped rather than sent twice. The DM is built from the stored fields
        exactly as the first attempt was.
        """
        if not self.auto_notify:
            return CrossPersonNotifyRetrySummary()
        reference = now or self.clock()
        due = await self.repository.list_notification_retries_due(
            tenant_id,
            due_at=reference,
            max_attempts=self.notify_max_attempts,
            limit=limit,
        )
        sent = failed = skipped = given_up = 0
        for request in due:
            if not self._should_notify(request):
                skipped += 1
                continue
            claimed = await self._claim_attempt(request, reference)
            if claimed is None:
                skipped += 1
                continue
            try:
                await self._send_counterpart_dm(claimed)
            except Exception as error:
                failed += 1
                final = claimed.notify_next_attempt_at is None
                if final:
                    given_up += 1
                _logger.warning(
                    "cross_person_notify_retry_failed",
                    tenant_id=tenant_id,
                    request_id=claimed.id,
                    attempt=claimed.notify_attempts,
                    gave_up=final,
                    error=type(error).__name__,
                )
                continue
            sent += 1
        return CrossPersonNotifyRetrySummary(
            due=len(due),
            sent=sent,
            failed=failed,
            skipped=skipped,
            given_up=given_up,
        )

    def _should_notify(self, request: CrossPersonRequest) -> bool:
        if request.counterpart_id is None:
            return False
        if request.counterpart_id in {request.requester_id, request.requester_chat_ref}:
            # Matched to oneself ("need <my own name> to review"): nobody to ask.
            return False
        if request.notified:
            return False
        return request.notify_attempts < self.notify_max_attempts

    async def _claim_attempt(
        self,
        request: CrossPersonRequest,
        attempted_at: datetime,
    ) -> CrossPersonRequest | None:
        return await self.repository.claim_notification_attempt(
            request.tenant_id,
            request.id,
            expected_attempts=request.notify_attempts,
            attempted_at=attempted_at,
            next_attempt_at=self._next_attempt_at(request.notify_attempts + 1, attempted_at),
        )

    def _next_attempt_at(self, attempt: int, attempted_at: datetime) -> datetime | None:
        """When the attempt after ``attempt`` is due; None when that was the last."""
        if attempt >= self.notify_max_attempts:
            return None
        delay = self.notify_retry_backoff_seconds * 2 ** (attempt - 1)
        return attempted_at + timedelta(seconds=delay)

    async def _send_counterpart_dm(self, request: CrossPersonRequest) -> CrossPersonRequest:
        counterpart_id = request.counterpart_id
        if counterpart_id is None:
            return request
        user = await self.directory_repository.get(request.tenant_id, counterpart_id)
        chat_user = ChatUserRef(
            tenant_id=request.tenant_id,
            external_id=counterpart_id,
            display_name=(
                user.display_name if user is not None else request.counterpart_display_name
            ),
        )
        notify_correlation_id = f"xreq-{request.id}"
        # The same correlation id and idempotency key on every attempt: a chat
        # adapter that dedupes sends returns the first message instead of
        # posting a second one if an earlier attempt did reach the person.
        message_id = await self.chat_provider.send_dm(
            chat_user,
            OutboundMessage(
                tenant_id=request.tenant_id,
                text=_counterpart_message(request, await self._requester_name(request)),
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
            updated_at=self.clock(),
        )
        return updated or request

    async def handle_counterpart_reply(
        self,
        message: InboundMessage,
        request: CrossPersonRequest,
    ) -> CrossPersonRequest:
        if request.status in _CLOSED_STATUSES:
            # A thanks or a second "done" after the fact must not reopen the
            # request or tell the requester again.
            return request
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
        existing = await self.repository.get(tenant_id, request_id)
        if existing is not None and existing.status is status:
            # Resolving twice (a double click, a retried call) is not a second
            # transition, and must not tell the requester a second time.
            return existing
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

    async def list_raised(
        self,
        tenant_id: str,
        requester_id: str,
        statuses: Iterable[CrossPersonRequestStatus] | None = None,
    ) -> list[CrossPersonRequest]:
        """The requests this person asked of others.

        The mirror of ``list_inbox``: without it a requester had no way to see
        whether their own ask had landed, been acknowledged or resolved.
        """
        status_tuple = tuple(statuses) if statuses is not None else None
        return await self.repository.list_for_requester(
            tenant_id,
            requester_id,
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
                    f"request resolved: {_fit_note(request.note, _RESOLVED_NOTE_CHARS)}"
                ),
                correlation_id=f"xreq-resolved-{request.id}",
                metadata={
                    "purpose": "cross_person_request_resolved",
                    "idempotency_key": f"xreq-resolved:{request.id}",
                    "request_id": request.id,
                },
            ),
        )

    async def _requester_name(self, request: CrossPersonRequest) -> str | None:
        """The requester's display name, found by member id or by chat id."""
        for candidate in (request.requester_id, request.requester_chat_ref):
            if candidate is None:
                continue
            user = await self.directory_repository.get(request.tenant_id, candidate)
            if user is not None and user.display_name.strip():
                return user.display_name.strip()
        return None

    async def _append_fact(self, request: CrossPersonRequest, *, transition: str) -> None:
        if self.time_series_repository is None:
            return
        entity_ref = EntityRef(
            tenant_id=request.tenant_id,
            kind=NodeKind.DEVELOPER,
            id=request.counterpart_id or request.requester_id,
        )
        # Both names go on the fact so the feed can read like a sentence about
        # people. The counterpart's was already here; without the requester's,
        # the portfolio feed rendered raw ids -- "U1007 needs U1003 for ...".
        reporter = await self.directory_repository.get(request.tenant_id, request.requester_id)
        payload: dict[str, JsonScalar] = {
            "request_id": request.id,
            "source_checkin_id": request.source_correlation_id,
            "reporter_id": request.requester_id,
            "reporter_name": reporter.display_name if reporter is not None else None,
            "referenced_person_id": request.counterpart_id,
            "referenced_person_name": request.counterpart_display_name,
            "dependency_kind": _fact_kind(request),
            "dependency_status": _fact_status(request.status),
            "transition": transition,
            "summary": request.note,
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


_CLOSED_STATUSES = frozenset(
    {CrossPersonRequestStatus.RESOLVED, CrossPersonRequestStatus.DISMISSED}
)
_RESOLVED_NOTE_CHARS = 200
_MIN_NOTE_CHARS = 40
_ASK_LEAD = {
    "review": "asked for your review",
    "input": "asked for your input",
}
_REPLY_HINT = "\nReply in this thread to acknowledge, or say when it is done."


def _counterpart_message(request: CrossPersonRequest, requester_name: str | None) -> str:
    """What the counterpart is told: who asked, what kind of ask, and the note.

    The note is the short ask the model extracted, never the requester's reply,
    so it is flattened to one line and fitted under the outbound DM cap in case
    a model copied more than it should. A requester who cannot be named is
    "A teammate" rather than an internal id.
    """
    lead = _ASK_LEAD.get(request.kind.value, "is waiting on you")
    head = f"{requester_name or 'A teammate'} {lead}: "
    room = max(OUTBOUND_DM_MAX_CHARS - len(head) - len(_REPLY_HINT), _MIN_NOTE_CHARS)
    return f"{head}{_fit_note(request.note, room)}{_REPLY_HINT}"


def _fit_note(note: str, limit: int) -> str:
    flat = " ".join(note.split())
    if len(flat) <= limit:
        return flat
    return f"{flat[: limit - 1].rstrip()}…"


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
