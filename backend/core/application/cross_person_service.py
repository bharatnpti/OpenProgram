from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from core.application.blocker_settlement import BlockerSettlement, cleared_by_merge
from core.application.counterpart_replies import (
    CounterpartReplyReading,
    eta_phrase,
    read_counterpart_reply,
)
from core.application.json_parsing import extract_json_object
from core.application.merge_request_links import (
    MERGE_REQUEST_FACT_SOURCE,
    MergeRequestIndex,
    merge_request_label,
)
from core.application.status_collector import OUTBOUND_DM_MAX_CHARS
from core.domain.cross_person import (
    CrossPersonNotifyRetrySummary,
    CrossPersonRequest,
    CrossPersonRequestResolution,
    CrossPersonRequestStatus,
    is_repeat_of,
    new_cross_person_request,
    request_subject,
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
    # Closes the requester's blockers that a resolved request or a merged
    # merge request has cleared; None leaves blockers to the next check-in.
    blocker_settlement: BlockerSettlement | None = None
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
            if resolution.request_id is not None:
                settled = await self._settle(tenant_id, resolution, observed_at)
                if settled is not None:
                    created.append(settled)
                continue
            request = new_cross_person_request(
                tenant_id=tenant_id,
                id=_request_id(source_correlation_id, index, resolution),
                requester_id=requester_id,
                requester_chat_ref=requester_chat_ref,
                source_correlation_id=source_correlation_id,
                resolution=resolution,
                created_at=observed_at,
            )
            repeated = await self._refresh_repeat(request)
            if repeated is not None:
                created.append(repeated)
                continue
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

    async def _refresh_repeat(self, request: CrossPersonRequest) -> CrossPersonRequest | None:
        """The requester's open ask this one repeats, refreshed; None when it is new.

        A check-in each round restates what is still pending ("still waiting
        on Asha for CHK-10"). Read as a new request every time, it opened a
        copy per round, DMed the counterpart again, and the older copies never
        closed. The same requester asking the same person about the same work
        now refreshes the open request instead: it is marked as asked again
        and gains the issue link if it had none, and nobody is told twice.
        A request with no counterpart yet has no one to compare and stays new.
        """
        if request.counterpart_id is None:
            return None
        for existing in await self.repository.list_for_requester(
            request.tenant_id,
            request.requester_id,
            statuses=_STILL_OPEN,
        ):
            if is_repeat_of(request, existing, vague_matches=True):
                refreshed = await self.repository.refresh(
                    request.tenant_id,
                    existing.id,
                    task_ref=request.task_ref,
                    updated_at=request.created_at,
                )
                if refreshed is not None:
                    return refreshed
        return None

    async def _close_repeats(
        self,
        request: CrossPersonRequest,
        resolved_at: datetime,
    ) -> tuple[CrossPersonRequest, ...]:
        """Resolve the still-open copies of an ask that has just been resolved.

        Copies recorded before asks were refreshed in place (one per round)
        would otherwise stay open after the ask itself was done. Only a clear
        match closes: the same requester, person and work, never a vague ask.
        The requester was told about the request itself, so not again here.
        """
        closed: list[CrossPersonRequest] = []
        for other in await self.repository.list_for_requester(
            request.tenant_id,
            request.requester_id,
            statuses=_STILL_OPEN,
        ):
            if not is_repeat_of(request, other, vague_matches=False):
                continue
            updated = await self.repository.update_status(
                request.tenant_id,
                other.id,
                CrossPersonRequestStatus.RESOLVED,
                resolved_at,
                from_statuses=_STILL_OPEN,
            )
            if updated is not None:
                await self._append_fact(updated, transition=CrossPersonRequestStatus.RESOLVED.value)
                closed.append(updated)
        return tuple(closed)

    async def resolve_merged_work(self, tenant_id: str) -> tuple[CrossPersonRequest, ...]:
        """Resolve the open requests whose merge requests have all been merged.

        A review asked for on "storefront-web !1", or on CHK-3 whose merge
        request is checkout-api !1, is done once that merge request is merged,
        whether or not the reviewer answered the DM. Every merge request the
        ask names, or that names its issue, has to be merged: one still open
        keeps the ask open. Safe to run after every sync, and from the six
        per-repository syncs at once: the resolve is one conditional update,
        and only the pass that changed the request announces it (N24).
        """
        if self.time_series_repository is None:
            return ()
        candidates = [
            request
            for request in await self.repository.list_open(tenant_id)
            if request.status in _STILL_OPEN
        ]
        if not candidates:
            return ()
        index = MergeRequestIndex.from_facts(
            await self.time_series_repository.list_recent_facts(
                tenant_id,
                sources=(MERGE_REQUEST_FACT_SOURCE,),
                limit=_MERGE_REQUEST_FACT_SCAN_LIMIT,
            )
        )
        resolved: list[CrossPersonRequest] = []
        for request in candidates:
            subject = request_subject(request)
            merged = index.merged_work(
                issue_keys=subject.issue_keys,
                refs=[
                    (ref.repo, ref.number) for ref in subject.merge_requests if ref.repo is not None
                ],
            )
            if merged is None:
                continue
            updated = await self._resolve_by_merge(request, merged)
            if updated is not None:
                resolved.append(updated)
        return tuple(resolved)

    async def supersede_repeats(self, tenant_id: str) -> tuple[CrossPersonRequest, ...]:
        """Dismiss the older open copies of an ask as superseded by its newest (N11b).

        Copies recorded before asks were refreshed in place (N11) stay open
        side by side while nothing resolves the ask: in qa2, Mina's R1 and R2
        asks of Asha about CHK-10, and Noah's R2 ask of Liam about CHK-6 beside
        his R3 one. Of the open or acknowledged requests with the same
        requester, person and work (a clear match, never a vague ask), the
        newest is kept and each older one is dismissed; its fact records the
        transition as ``superseded`` and names the request kept. Nobody is
        told: the ask stays open in the copy kept, and its people heard of it
        when it was made. Dismissed, not resolved: a resolved copy would make
        the kept request's own resolution silent (``_told_about_a_copy``).

        A one-time cleanup that is safe on every pass: once the copies are
        closed nothing is left to do, and of two passes at once only one
        closes each copy and records it.
        """
        still_open = sorted(
            (
                request
                for request in await self.repository.list_open(tenant_id)
                if request.status in _STILL_OPEN
            ),
            key=lambda request: (request.created_at, request.updated_at, request.id),
            reverse=True,
        )
        kept: list[CrossPersonRequest] = []
        superseded: list[CrossPersonRequest] = []
        for request in still_open:
            newer = next(
                (other for other in kept if is_repeat_of(request, other, vague_matches=False)),
                None,
            )
            if newer is None:
                kept.append(request)
                continue
            updated = await self.repository.update_status(
                tenant_id,
                request.id,
                CrossPersonRequestStatus.DISMISSED,
                max(self.clock(), request.updated_at),
                from_statuses=_STILL_OPEN,
            )
            if updated is None:
                # Closed since it was listed: resolved, or superseded by a
                # pass running beside this one, which recorded it.
                continue
            await self._append_fact(updated, transition=_SUPERSEDED, superseded_by=newer.id)
            _logger.info(
                "cross_person_request_superseded",
                tenant_id=tenant_id,
                request_id=updated.id,
                superseded_by=newer.id,
            )
            superseded.append(updated)
        return tuple(superseded)

    async def settle_merged_work(self, tenant_id: str) -> tuple[CrossPersonRequest, ...]:
        """After a sync: resolve what merged merge requests have done.

        Older copies of a still-open ask are superseded first
        (``supersede_repeats``), so the pass works on one request per ask; then
        requests (``resolve_merged_work``), then any open blocker whose merge
        requests are all merged and that was last stated before the merge,
        whether or not a request was ever raised for it.
        """
        try:
            await self.supersede_repeats(tenant_id)
        except Exception as error:
            # The cleanup must not stop the pass: the next sync runs it again.
            _logger.warning(
                "cross_person_supersede_failed",
                tenant_id=tenant_id,
                error=type(error).__name__,
            )
        resolved = await self.resolve_merged_work(tenant_id)
        if self.blocker_settlement is not None and self.time_series_repository is not None:
            index = MergeRequestIndex.from_facts(
                await self.time_series_repository.list_recent_facts(
                    tenant_id,
                    sources=(MERGE_REQUEST_FACT_SOURCE,),
                    limit=_MERGE_REQUEST_FACT_SCAN_LIMIT,
                )
            )
            await self.blocker_settlement.settle_for_merged_work(
                tenant_id, index, as_of=self.clock()
            )
        return resolved

    async def _resolve_by_merge(
        self,
        request: CrossPersonRequest,
        merged: tuple[FactEvent, ...],
    ) -> CrossPersonRequest | None:
        current = await self.repository.get(request.tenant_id, request.id)
        if current is None or current.status not in _STILL_OPEN:
            # Resolved by a reply, or as a copy of another, since it was listed.
            return None
        resolved_at = max((fact.observed_at for fact in merged), default=self.clock())
        updated = await self.repository.update_status(
            request.tenant_id,
            request.id,
            CrossPersonRequestStatus.RESOLVED,
            max(resolved_at, current.updated_at),
            from_statuses=_STILL_OPEN,
        )
        if updated is None:
            # Another pass resolved it since it was read: the sync of another
            # repository, a reply or the console. That pass tells the requester
            # (N24: Liam got the CHK-3 notice twice, a second apart).
            return None
        await self._append_fact(updated, transition=CrossPersonRequestStatus.RESOLVED.value)
        told = await self._told_about_a_copy(updated)
        await self._close_repeats(updated, updated.updated_at)
        await self._settle_blockers(updated, updated.updated_at, merged=merged)
        if told:
            return updated
        try:
            await self._notify_requester_resolved(
                updated,
                merged_labels=tuple(merge_request_label(fact) for fact in merged),
            )
        except Exception as error:
            # One requester's failed DM must not stop the pass for the others.
            _logger.warning(
                "cross_person_merge_notice_failed",
                tenant_id=updated.tenant_id,
                request_id=updated.id,
                error=type(error).__name__,
            )
        return updated

    async def _told_about_a_copy(self, request: CrossPersonRequest) -> bool:
        """Whether a copy of this ask was resolved before, so its requester was told.

        Copies recorded before asks were refreshed in place can resolve one by
        one -- Zoe's R3 copy by Noah's reply, her R2 copy later by the merge --
        and the requester hears about the ask once. Asked before this
        resolution closes its own copies, which are still open then.
        """
        return any(
            is_repeat_of(request, other, vague_matches=False)
            for other in await self.repository.list_for_requester(
                request.tenant_id,
                request.requester_id,
                statuses=(CrossPersonRequestStatus.RESOLVED,),
            )
        )

    async def _settle_blockers(
        self,
        request: CrossPersonRequest,
        resolved_at: datetime,
        *,
        merged: tuple[FactEvent, ...] = (),
    ) -> None:
        """Close the requester's blockers that waited on this request (N19).

        Its copies are closed first and are no rival; any other request the
        requester still has open is, so a blocker waiting on two people stays
        open until both are done. A failure is logged and leaves the blocker
        to the next merge pass or check-in: the request is resolved either way.
        ``merged`` are the merge requests that resolved it, if a merge did: the
        requester's summary then says "CHK-17 merged" rather than that the
        request was resolved (N34).
        """
        if self.blocker_settlement is None:
            return
        try:
            others = [
                other
                for other in await self.repository.list_for_requester(
                    request.tenant_id,
                    request.requester_id,
                    statuses=_STILL_OPEN,
                )
                if other.id != request.id and not is_repeat_of(request, other, vague_matches=False)
            ]
            await self.blocker_settlement.settle_for_request(
                request,
                resolved_at=resolved_at,
                open_requests=others,
                cleared_by=(
                    cleared_by_merge(request_subject(request).issue_keys, merged)
                    if merged
                    else None
                ),
            )
        except Exception as error:
            _logger.warning(
                "cross_person_blocker_settle_failed",
                tenant_id=request.tenant_id,
                request_id=request.id,
                error=type(error).__name__,
            )

    async def _settle(
        self,
        tenant_id: str,
        resolution: CrossPersonRequestResolution,
        observed_at: datetime | None,
    ) -> CrossPersonRequest | None:
        """Open a needs_resolution request with the member the requester has named since.

        The request was recorded when it was stated, before anyone knew who it
        named, so the answer to "who did you mean?" finishes that request
        rather than starting another. A request already settled (a redelivered
        answer) is returned as it is and nobody is told twice.
        """
        request_id = resolution.request_id
        if (
            request_id is None
            or resolution.counterpart_id is None
            or resolution.status is not CrossPersonRequestStatus.OPEN
        ):
            return None
        opened = await self.repository.assign_counterpart(
            tenant_id,
            request_id,
            counterpart_id=resolution.counterpart_id,
            counterpart_display_name=resolution.counterpart_display_name,
            counterpart_email=resolution.counterpart_email,
            updated_at=observed_at or self.clock(),
        )
        if opened is None:
            return await self.repository.get(tenant_id, request_id)
        await self._append_fact(opened, transition="opened")
        if self.auto_notify:
            opened = await self._notify_best_effort(opened)
        return opened

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
        reading = await self._read_counterpart_reply(message, request)
        updated = await self.repository.update_status(
            request.tenant_id,
            request.id,
            reading.status,
            message.received_at,
            from_statuses=_STILL_OPEN,
        )
        if updated is None:
            # Nothing changed: the request is in that state already (a second
            # "on it"), or the merge pass or another reply closed it since it
            # was read, and that one told the requester.
            return await self.repository.get(request.tenant_id, request.id) or request
        await self._append_fact(updated, transition=reading.status.value)
        if reading.status is CrossPersonRequestStatus.RESOLVED:
            # What the resolution settles is stored before anyone is told, so
            # a failed DM cannot leave a copy or a blocker open behind it.
            told = await self._told_about_a_copy(updated)
            await self._close_repeats(updated, message.received_at)
            await self._settle_blockers(updated, message.received_at)
            if not told:
                await self._notify_requester_resolved(updated)
        elif reading.eta is not None and request.status is CrossPersonRequestStatus.OPEN:
            # The first acknowledgement that says when is worth a message;
            # a bare "on it", or a second ETA, the requester sees in the
            # console. Only an open request becomes acknowledged once.
            await self._notify_requester_acknowledged(updated, reading.eta)
        return updated

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
            self.clock(),
        )
        if updated is None:
            # Missing, or a concurrent call made this change first and has
            # told the requester: the request as it is now.
            return await self.repository.get(tenant_id, request_id)
        await self._append_fact(updated, transition=status.value)
        if status is CrossPersonRequestStatus.RESOLVED:
            told = await self._told_about_a_copy(updated)
            await self._close_repeats(updated, updated.updated_at)
            await self._settle_blockers(updated, updated.updated_at)
            if not told:
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

    async def _read_counterpart_reply(
        self,
        message: InboundMessage,
        request: CrossPersonRequest,
    ) -> CounterpartReplyReading:
        """What the counterpart's reply says: read directly, else by the model.

        Short natural replies ("approved", "LGTM", "merged", "should merge
        tomorrow") are read without the model. The model's answer is taken
        from the first JSON object in its text, fenced or not; when there is
        none the reply counts as an acknowledgement, as before. The ETA always
        comes from the reply's own when-statement, never from the model.
        """
        direct = read_counterpart_reply(message.text)
        if direct is not None:
            return direct
        eta = eta_phrase(message.text)
        if self.llm_provider is None:
            return CounterpartReplyReading(status=CrossPersonRequestStatus.ACKNOWLEDGED, eta=eta)
        prompt = (
            "Classify this reply to a cross-person work request as JSON with one key, status. "
            "Use resolved only when the reply says the requested review/input/dependency is done. "
            "Use acknowledged when the reply only confirms ownership or gives a non-final update. "
            "Answer with the JSON object only. "
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
        parsed = extract_json_object(response.text)
        if parsed is None:
            _logger.warning(
                "cross_person_reply_json_decode_failed",
                tenant_id=message.tenant_id,
                request_id=request.id,
                trace_id=response.trace_id,
            )
            return CounterpartReplyReading(status=CrossPersonRequestStatus.ACKNOWLEDGED, eta=eta)
        status = parsed.get("status")
        if isinstance(status, str) and status.strip().casefold() == "resolved":
            return CounterpartReplyReading(status=CrossPersonRequestStatus.RESOLVED)
        return CounterpartReplyReading(status=CrossPersonRequestStatus.ACKNOWLEDGED, eta=eta)

    async def _notify_requester_acknowledged(self, request: CrossPersonRequest, eta: str) -> None:
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
                    f"{counterpart} acknowledged your {request.kind.value} request and "
                    f"expects it {eta}: {_fit_note(request.note, _RESOLVED_NOTE_CHARS)}"
                ),
                correlation_id=f"xreq-acknowledged-{request.id}",
                metadata={
                    "purpose": "cross_person_request_acknowledged",
                    "idempotency_key": requester_notice_key(
                        request.id, CrossPersonRequestStatus.ACKNOWLEDGED
                    ),
                    "request_id": request.id,
                },
            ),
        )

    async def _notify_requester_resolved(
        self,
        request: CrossPersonRequest,
        *,
        merged_labels: tuple[str, ...] = (),
    ) -> None:
        if request.requester_chat_ref is None:
            return
        counterpart = (
            request.counterpart_display_name or request.counterpart_id or "The counterpart"
        )
        note = _fit_note(request.note, _RESOLVED_NOTE_CHARS)
        text = (
            f"{_join_labels(merged_labels)} {'is' if len(merged_labels) == 1 else 'are'} "
            f"merged, so I marked your {request.kind.value} request to {counterpart} "
            f"resolved: {note}"
            if merged_labels
            else f"{counterpart} marked your {request.kind.value} request resolved: {note}"
        )
        await self.chat_provider.send_dm(
            ChatUserRef(tenant_id=request.tenant_id, external_id=request.requester_chat_ref),
            OutboundMessage(
                tenant_id=request.tenant_id,
                text=text,
                correlation_id=f"xreq-resolved-{request.id}",
                metadata={
                    "purpose": "cross_person_request_resolved",
                    "idempotency_key": requester_notice_key(
                        request.id, CrossPersonRequestStatus.RESOLVED
                    ),
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

    async def _append_fact(
        self,
        request: CrossPersonRequest,
        *,
        transition: str,
        superseded_by: str | None = None,
    ) -> None:
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
        if superseded_by is not None:
            # The copy of the same ask that stays open in this one's place.
            payload["superseded_by"] = superseded_by
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


def requester_notice_key(request_id: str, state: CrossPersonRequestStatus) -> str:
    """The send-once key of a notice to the requester: the request and its new state.

    The chat adapter posts a key once, so a retried or racing notice of the
    same transition is not posted again, while each transition of a request
    (acknowledged with an ETA, then resolved) gets its own notice.
    """
    return f"xreq-{state.value}:{request_id}"


_CLOSED_STATUSES = frozenset(
    {CrossPersonRequestStatus.RESOLVED, CrossPersonRequestStatus.DISMISSED}
)
# Asked of a named person and not yet done: what a repeat refreshes, and what
# a resolution, a copy's or a merge's, closes.
_STILL_OPEN = (CrossPersonRequestStatus.OPEN, CrossPersonRequestStatus.ACKNOWLEDGED)
# The transition a fact records for an older copy dismissed in favour of the
# newest copy of the same ask (N11b); the request row itself says dismissed.
_SUPERSEDED = "superseded"
# Merge request facts read per pass; one fact per update, so a few thousand
# cover every recent merge request of a tenant.
_MERGE_REQUEST_FACT_SCAN_LIMIT = 5000
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


def _join_labels(labels: tuple[str, ...]) -> str:
    if len(labels) <= 2:
        return " and ".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


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
    if status is CrossPersonRequestStatus.DISMISSED:
        # Closed without being done: a dismissed or superseded copy is not open.
        return "dismissed"
    return "open"
