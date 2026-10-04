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

The status's summary says what cleared, too (N34): in R4 the merge pass took
Zoe's CHK-11 wait out of her blockers, but her summary still read "CHK-11 is
still blocked on CHK-17" twice, so Ask, the digest and her person view
contradicted the green colour. Each sentence that states a cleared blocker is
marked where it stands, "CHK-11 is still blocked on CHK-17 (cleared: CHK-17
merged).", by plain text rules and no model. A summary written on an earlier
day is that day's record and keeps its words.

Each row resolves once (N24): the six per-repository syncs each run the merge
pass, so the row is resolved by one conditional update, in one transaction
with the status revision, and only the pass that changed it revises the status.

A resolution here changes what today's rollup shows, outside any check-in, so
the pass that changed a row asks for today's rollup to be recorded again (N27:
in R4 the 06:15 rollup ran 3 s before the merge pass resolved Zoe's CHK-11
wait, and Storefront, Checkout and the program read amber on it until 07:15).
The requests of one burst of passes run as one rollup.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import structlog

from core.application.merge_request_links import (
    MergeRequestIndex,
    is_merged_merge_request,
    merge_request_label,
    merge_requests_by_issue_key,
)
from core.domain.blockers import (
    BlockerResolutionReason,
    DeveloperBlocker,
    normalize_blocker_key,
)
from core.domain.cross_person import (
    CrossPersonRequest,
    MergeRequestRef,
    RequestSubject,
    issue_keys_in,
    merge_request_refs_in,
    request_subject,
    subject_of_text,
)
from core.domain.graph import FactEvent
from core.domain.status import DeveloperStatus
from core.ports.repositories import StatusRepository
from core.ports.workflows import RollupRefresher

__all__ = [
    "CLEARED_LEAD",
    "BlockerSettlement",
    "blocker_subject",
    "cleared_by_merge",
    "request_match_score",
    "summary_with_cleared",
]

# Opens the mark a summary sentence gets when the blocker it states has cleared
# outside a check-in (N34): "... blocked on CHK-17 (cleared: CHK-17 merged)."
CLEARED_LEAD = "cleared:"

# Words that say a sentence is about a wait: "blocked on", "waiting for", ...
_WAIT_WORDS = re.compile(
    r"\b(?:block(?:ed|er|ers|ing|s)?|wait(?:s|ing)?\s+(?:on|for)|stuck|held\s+up|on\s+hold"
    r"|depend(?:s|ing|ent)?\s+on|dependency|pending|awaiting)\b",
    re.IGNORECASE,
)
# Where a summary sentence ends: its closing punctuation, then a space or the end.
# "storefront-web !1" is not an end: no space follows the "!".
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")

# A request whose counterpart the blocker names, and which shares its issue
# or merge request, matches best; sharing the work alone, next; naming the
# person where neither side names an issue, least.
_NAMED_AND_SHARED = 3
_SHARED = 2
_NAMED = 1

_logger = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class BlockerSettlement:
    def __init__(
        self,
        status_repository: StatusRepository,
        *,
        rollup_refresher: RollupRefresher | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._status_repository = status_repository
        # Records today's rollup again after a pass changed a blocker; None
        # leaves the change to the next hourly rollup.
        self._rollup_refresher = rollup_refresher
        self._clock = clock

    async def settle_for_request(
        self,
        request: CrossPersonRequest,
        *,
        resolved_at: datetime,
        open_requests: Sequence[CrossPersonRequest] = (),
        cleared_by: str | None = None,
    ) -> tuple[DeveloperBlocker, ...]:
        """Resolve the requester's open blockers that wait on ``request``.

        ``open_requests`` are the requester's other requests still open: a
        blocker one of them matches as well or better is still waiting.
        ``cleared_by`` says how the wait ended, for the person's summary
        ("CHK-17 merged"); by default, that the request was resolved.
        """
        blockers = await self._status_repository.open_blockers(
            request.tenant_id, request.requester_id, _probe_day(resolved_at)
        )
        settled = [
            blocker
            for blocker in blockers
            if _stated_before(blocker, resolved_at) and _waits_on(blocker, request, open_requests)
        ]
        how = cleared_by or _cleared_by_request(request)
        return await self._resolve(
            request.tenant_id,
            request.requester_id,
            settled,
            resolved_at,
            cleared_by={blocker.blocker_id: how for blocker in settled},
        )

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
        by_developer: dict[str, list[tuple[DeveloperBlocker, datetime, str]]] = {}
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
                by_developer.setdefault(blocker.developer_id, []).append(
                    (blocker, merged_at, cleared_by_merge(subject.issue_keys, merged))
                )
        resolved: list[DeveloperBlocker] = []
        for developer_id, items in by_developer.items():
            when = max(at for _, at, _ in items)
            resolved.extend(
                await self._resolve(
                    tenant_id,
                    developer_id,
                    [item for item, _, _ in items],
                    when,
                    cleared_by={item.blocker_id: how for item, _, how in items},
                )
            )
        return tuple(resolved)

    async def _resolve(
        self,
        tenant_id: str,
        developer_id: str,
        blockers: Sequence[DeveloperBlocker],
        resolved_at: datetime,
        *,
        cleared_by: Mapping[str, str],
    ) -> tuple[DeveloperBlocker, ...]:
        if not blockers:
            return ()
        rows = tuple(_resolved(blocker, resolved_at.date()) for blocker in blockers)
        day = max(row.resolved_on or row.last_seen_on for row in rows)
        # One conditional update per row (N24): the six per-repository syncs
        # each run a merge pass, and of two passes over the same blocker only
        # the one that changed it gets it back and revises the status.
        changed = await self._status_repository.resolve_developer_blockers(
            tenant_id,
            developer_id,
            rows,
            status_as_of=day,
            revise_status=_revision(cleared_by),
        )
        if changed:
            await self._refresh_rollup(tenant_id)
        return changed

    async def _refresh_rollup(self, tenant_id: str) -> None:
        """Ask for today's rollup again: the blockers it counted have changed (N27).

        Only the pass that changed a row asks, and the requests of one burst
        run once. Never fails the resolution, which is stored already: the
        hourly rollup records it otherwise.
        """
        if self._rollup_refresher is None:
            return
        try:
            await self._rollup_refresher.refresh_rollup(tenant_id, self._clock().date())
        except Exception as error:
            _logger.warning(
                "rollup_refresh_request_failed",
                tenant_id=tenant_id,
                error=type(error).__name__,
            )


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


def _revision(
    cleared_by: Mapping[str, str],
) -> Callable[[DeveloperStatus, Sequence[DeveloperBlocker]], DeveloperStatus | None]:
    """The status revision for one resolution; ``cleared_by`` says how, per blocker id."""

    def revise(
        status: DeveloperStatus, resolved: Sequence[DeveloperBlocker]
    ) -> DeveloperStatus | None:
        return _without_resolved(status, resolved, cleared_by)

    return revise


def _without_resolved(
    status: DeveloperStatus,
    resolved: Sequence[DeveloperBlocker],
    cleared_by: Mapping[str, str],
) -> DeveloperStatus | None:
    """The person's status without the blockers just resolved, its summary saying so.

    None when the status neither lists nor states any of them. Pure: it runs
    inside the blocker transaction, under the status row lock.
    """
    gone = {blocker.normalized_key for blocker in resolved}
    remaining = tuple(text for text in status.blockers if not _is_gone(text, gone))
    listed = {
        blocker.blocker_id
        for blocker in resolved
        if any(_is_gone(text, {blocker.normalized_key}) for text in status.blockers)
    }
    # A summary written on an earlier day is that day's record: only the
    # status of the day the blocker cleared, or a later one, is marked.
    current = [
        (blocker, cleared_by.get(blocker.blocker_id) or "resolved")
        for blocker in resolved
        if (blocker.resolved_on or blocker.last_seen_on) <= status.as_of
    ]
    summary = summary_with_cleared(status.summary, current, still_open=remaining, listed=listed)
    if len(remaining) == len(status.blockers) and summary == status.summary:
        return None
    return replace(status, blockers=remaining, summary=summary)


def summary_with_cleared(
    summary: str,
    cleared: Sequence[tuple[DeveloperBlocker, str]],
    *,
    still_open: Sequence[str] = (),
    listed: Collection[str] = (),
) -> str:
    """``summary`` with each sentence that states a cleared blocker marked as cleared.

    ``cleared`` pairs each blocker with how it cleared ("CHK-17 merged"). A
    sentence states a blocker when it quotes it, or when it speaks of a wait
    ("blocked on", "waiting for", "pending", ...) and names an issue or merge
    request of the blocker that no blocker in ``still_open`` names. The mark
    closes the sentence: "CHK-11 is still blocked on CHK-17 (cleared: CHK-17
    merged)." A blocker in ``listed`` (the status's own blockers) that no
    sentence states gets a sentence of its own at the end. Nothing else
    changes, and a mark already there is not added again.
    """
    spans = _sentence_spans(summary)
    open_keys: set[str] = set()
    open_refs: set[MergeRequestRef] = set()
    for text in still_open:
        subject = subject_of_text(text)
        open_keys |= subject.issue_keys
        open_refs |= {ref for ref in subject.merge_requests if ref.repo}
    marks: dict[int, list[str]] = {}
    closing: list[str] = []
    for blocker, how in cleared:
        mark = f"({CLEARED_LEAD} {how})"
        stated = False
        for start, end in spans:
            sentence = summary[start:end]
            if not _states(sentence, blocker, open_keys, open_refs):
                continue
            stated = True
            if mark not in sentence and mark not in marks.get(end, []):
                marks.setdefault(end, []).append(mark)
        if not stated and blocker.blocker_id in listed and mark not in summary:
            closing.append(f"{blocker.description.strip().rstrip('.!')} {mark}.")
    revised = summary
    for end in sorted(marks, reverse=True):
        revised = f"{revised[:end]} {' '.join(marks[end])}{revised[end:]}"
    if closing:
        revised = " ".join((revised.rstrip(), *closing)).strip()
    return revised


def cleared_by_merge(issue_keys: Collection[str], merged: Sequence[FactEvent]) -> str:
    """How merged requests ended a wait: "CHK-17 merged", else "storefront-web !1 merged".

    Names the issues of ``issue_keys`` that the merged requests name, read with
    the merge pass's own matcher, else the merged requests themselves.
    """
    named = sorted(merge_requests_by_issue_key(merged, {key.upper() for key in issue_keys}))
    labels = named or sorted({merge_request_label(fact) for fact in merged})
    if len(labels) > 2:
        return f"{', '.join(labels[:-1])} and {labels[-1]} merged"
    return f"{' and '.join(labels) or 'the merge requests'} merged"


def _cleared_by_request(request: CrossPersonRequest) -> str:
    """How a resolved request cleared a blocker: "review request to Noah Weber resolved"."""
    name = request.counterpart_display_name or request.raw_name or "the counterpart"
    return f"{request.kind.value} request to {name} resolved"


def _states(
    sentence: str,
    blocker: DeveloperBlocker,
    open_keys: set[str],
    open_refs: set[MergeRequestRef],
) -> bool:
    """Whether a summary sentence states this blocker: quotes it, or names its wait."""
    quoted = normalize_blocker_key(blocker.description)
    if quoted and quoted in normalize_blocker_key(sentence):
        return True
    if not _WAIT_WORDS.search(sentence):
        return False
    subject = blocker_subject(blocker)
    # Only what sets it apart: an issue a blocker still open names as well
    # would mark that one's sentences too.
    keys = subject.issue_keys - open_keys
    refs = {ref for ref in subject.merge_requests if ref.repo} - open_refs
    named_refs = {ref for ref in merge_request_refs_in(sentence) if ref.repo}
    return bool(issue_keys_in(sentence) & keys) or bool(named_refs & refs)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Where each sentence of ``text`` starts, and where it ends before its punctuation."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if text[start : match.start()].strip():
            spans.append((start, match.start()))
        start = match.end()
    if text[start:].strip():
        spans.append((start, len(text.rstrip())))
    return spans


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
