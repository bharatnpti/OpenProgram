"""Closing a blocker once what it waits on is done.

In R3 Zoe's blocker "CHK-8 ... blocked until Noah reviews it" stayed open
after Noah approved (her request to him resolved at 00:05:48) and after
storefront-web !1 merged (00:05:39). Only her next check-in could clear it,
so the 00:15 and 01:15 rollups kept Zoe red and Payments, Checkout and the
program amber on a wait that was over.

A blocker now resolves when a request it waits on resolves, or when every
merge request it names, or that names its issue, is merged -- provided the
person last stated it before that happened. A blocker restated after the
review or the merge is what the person still reports, and stays open; so
does one that another of their still-open requests matches as well.

Resolving writes the rows with the reason ``reported_resolved`` (the
counterpart or the tracker reported the wait over; the table's CHECK allows no
other reason without a migration) and takes the blockers out of the person's
latest status, so the rollup's flat-status fallback cannot bring them back.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta

from core.application.merge_request_links import MergeRequestIndex, is_merged_merge_request
from core.domain.blockers import (
    BlockerResolutionReason,
    DeveloperBlocker,
    normalize_blocker_key,
)
from core.domain.cross_person import (
    CrossPersonRequest,
    RequestSubject,
    request_subject,
    subject_of_text,
)
from core.ports.repositories import StatusRepository

__all__ = ["BlockerSettlement", "blocker_subject", "request_match_score"]

# A request whose counterpart the blocker names, and which shares its issue
# or merge request, matches best; sharing the work alone, next; naming the
# person where neither side names an issue, least.
_NAMED_AND_SHARED = 3
_SHARED = 2
_NAMED = 1


class BlockerSettlement:
    def __init__(self, status_repository: StatusRepository) -> None:
        self._status_repository = status_repository

    async def settle_for_request(
        self,
        request: CrossPersonRequest,
        *,
        resolved_at: datetime,
        open_requests: Sequence[CrossPersonRequest] = (),
    ) -> tuple[DeveloperBlocker, ...]:
        """Resolve the requester's open blockers that wait on ``request``.

        ``open_requests`` are the requester's other requests still open: a
        blocker one of them matches as well or better is still waiting.
        """
        blockers = await self._status_repository.open_blockers(
            request.tenant_id, request.requester_id, _probe_day(resolved_at)
        )
        settled = [
            blocker
            for blocker in blockers
            if _stated_before(blocker, resolved_at) and _waits_on(blocker, request, open_requests)
        ]
        return await self._resolve(request.tenant_id, request.requester_id, settled, resolved_at)

    async def settle_for_merged_work(
        self,
        tenant_id: str,
        index: MergeRequestIndex,
        *,
        as_of: datetime,
    ) -> tuple[DeveloperBlocker, ...]:
        """Resolve open blockers on issues whose merge requests are all merged."""
        candidates: dict[str, DeveloperBlocker] = {}
        merged_keys = [
            key
            for key, facts in index.by_issue.items()
            if any(is_merged_merge_request(fact) for fact in facts)
        ]
        for key in merged_keys:
            for blocker in await self._status_repository.blockers_for_work_item(
                tenant_id, key, _probe_day(as_of)
            ):
                candidates[blocker.blocker_id] = blocker
        by_developer: dict[str, list[tuple[DeveloperBlocker, datetime]]] = {}
        for blocker in candidates.values():
            subject = blocker_subject(blocker)
            merged = index.merged_work(
                issue_keys=subject.issue_keys,
                refs=[(ref.repo, ref.number) for ref in subject.merge_requests if ref.repo],
            )
            if merged is None:
                continue
            merged_at = max(fact.observed_at for fact in merged)
            if _stated_before(blocker, merged_at):
                by_developer.setdefault(blocker.developer_id, []).append((blocker, merged_at))
        resolved: list[DeveloperBlocker] = []
        for developer_id, items in by_developer.items():
            when = max(at for _, at in items)
            resolved.extend(
                await self._resolve(tenant_id, developer_id, [item for item, _ in items], when)
            )
        return tuple(resolved)

    async def _resolve(
        self,
        tenant_id: str,
        developer_id: str,
        blockers: Sequence[DeveloperBlocker],
        resolved_at: datetime,
    ) -> tuple[DeveloperBlocker, ...]:
        if not blockers:
            return ()
        rows = tuple(_resolved(blocker, resolved_at.date()) for blocker in blockers)
        day = max(row.resolved_on or row.last_seen_on for row in rows)
        status = await self._status_repository.latest_developer_status(tenant_id, developer_id, day)
        gone = {blocker.normalized_key for blocker in blockers}
        if status is None or not any(_is_gone(text, gone) for text in status.blockers):
            await self._status_repository.record_developer_blockers(tenant_id, rows)
            return rows
        remaining = tuple(text for text in status.blockers if not _is_gone(text, gone))
        await self._status_repository.record_developer_status_with_blockers(
            replace(status, blockers=remaining), rows
        )
        return rows


def blocker_subject(blocker: DeveloperBlocker) -> RequestSubject:
    """What a blocker is about, as an ask is read: its issues, merge requests, words."""
    extra = (blocker.work_item_id,) if blocker.work_item_id else ()
    return subject_of_text(blocker.description, extra_issue_keys=extra)


def request_match_score(blocker: DeveloperBlocker, request: CrossPersonRequest) -> int:
    """How clearly a blocker waits on a request of the same person; 0 when it does not."""
    if blocker.developer_id not in {request.requester_id, request.requester_chat_ref}:
        return 0
    ours = blocker_subject(blocker)
    theirs = request_subject(request)
    named_refs = {ref for ref in ours.merge_requests if ref.repo} & {
        ref for ref in theirs.merge_requests if ref.repo
    }
    shared = bool(ours.issue_keys & theirs.issue_keys) or bool(named_refs)
    named = _names_counterpart(blocker.description, request)
    if shared:
        return _NAMED_AND_SHARED if named else _SHARED
    if named and not (ours.issue_keys and theirs.issue_keys):
        return _NAMED
    return 0


def _waits_on(
    blocker: DeveloperBlocker,
    request: CrossPersonRequest,
    open_requests: Sequence[CrossPersonRequest],
) -> bool:
    score = request_match_score(blocker, request)
    if score == 0:
        return False
    return all(
        request_match_score(blocker, other) < score
        for other in open_requests
        if other.id != request.id
    )


def _names_counterpart(text: str, request: CrossPersonRequest) -> bool:
    """Whether the blocker names the person asked: their first name or full name, whole."""
    words = _words(text)
    spoken = f" {' '.join(words)} "
    for name in (request.counterpart_display_name, request.raw_name):
        parts = _words(name or "")
        if parts and (parts[0] in words or f" {' '.join(parts)} " in spoken):
            return True
    return False


def _words(text: str) -> list[str]:
    return [
        word
        for word in "".join(char if char.isalnum() else " " for char in text.casefold()).split()
        if len(word) > 1
    ]


def _stated_before(blocker: DeveloperBlocker, at: datetime) -> bool:
    """The person last stated it before ``at``: then ``at`` supersedes it."""
    return blocker.updated_at is None or blocker.updated_at < at


def _resolved(blocker: DeveloperBlocker, day: date) -> DeveloperBlocker:
    resolved_on = max(day, blocker.last_seen_on)
    return replace(
        blocker,
        resolved_on=resolved_on,
        resolved_reason=BlockerResolutionReason.REPORTED_RESOLVED,
        last_seen_on=resolved_on,
    )


def _probe_day(at: datetime) -> date:
    # A blocker's days are the person's local dates, which can run a day
    # ahead of UTC; the stated-before check keeps the order honest.
    return (at + timedelta(days=1)).date()


def _is_gone(text: str, gone: set[str]) -> bool:
    return normalize_blocker_key(text) in gone or _status_key(text) in gone


def _status_key(text: str) -> str:
    # A status string may carry the work item it was attributed to, as
    # "... (CHK-8)"; the row's key is the text without it.
    key = normalize_blocker_key(text)
    if key.endswith(")") and " (" in key:
        head, _, tail = key.rpartition(" (")
        if tail[:-1].replace("-", "").isalnum():
            return normalize_blocker_key(head)
    return key
