"""One check-in's signals across all of the person's messages in it.

A check-in can take several messages: the first reply, then an answer to each
follow-up question. Each message is read on its own (with the earlier turns as
context only), so its signals say what that message said and nothing more.
``merge_checkin_signals`` folds the newest message's signals into what the
earlier messages of the same check-in said, deterministically: the newest
message updates what it mentions, and every fact from an earlier message that
it does not contradict stays. Write-backs, cross-person requests, the summary
and the status all read the merged result, so a short answer to a follow-up
("Monday") can no longer drop what the first reply said.

Blockers are not merged here. The blocker lifecycle already carries each
message's blockers forward (they are persisted with the partial status a
follow-up records) and resolves them only when a message says so, so the
newest message's blocker fields are kept as they are and the open set comes
from the reconciliation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from core.application.counterparts import name_words
from core.domain.status import CheckInSignals, CrossPersonMention, IssueClaim

_SENTENCE_END = (".", "!", "?", "…")


def merge_checkin_signals(earlier: CheckInSignals | None, later: CheckInSignals) -> CheckInSignals:
    """``later`` (the newest message) folded into ``earlier`` (the messages before it).

    - Issue claims are keyed by issue key. A later claim that states a state
      (``claimed_state``, or ``claimed_done``) replaces the earlier claim's state
      and note; one that only mentions the issue keeps the earlier state and adds
      its note. Issues only one side names are kept, earlier ones first.
    - Cross-person requests are keyed like the request ledger (kind + name); a
      later one replaces an earlier one with the same key.
    - The progress notes are joined in message order, without repeating a note
      already contained in the other.
    - An ETA change from the later message wins; otherwise the earlier one stays.
    - Blockers and ETA count as answered once any message answered them.
    - The merge is only as confident as its least confident message: an
      unparseable message never lets the check-in read as confirmed.
    - Blocker fields are the later message's own (see the module docstring).

    Merging the same ``later`` twice gives the same result, so a durable retry of
    a message whose signals were already stored does not double anything.
    """
    if earlier is None:
        return later
    return replace(
        later,
        progress_note=_joined_notes(earlier.progress_note, later.progress_note),
        eta_change_days=(
            later.eta_change_days if later.eta_change_days is not None else earlier.eta_change_days
        ),
        blockers_answered=earlier.blockers_answered or later.blockers_answered,
        eta_answered=earlier.eta_answered or later.eta_answered,
        requests=_merged_requests(earlier.requests, later.requests),
        issue_updates=_merged_claims(earlier.issue_updates, later.issue_updates),
        parser_confident=earlier.parser_confident and later.parser_confident,
    )


def _merged_claims(
    earlier: Iterable[IssueClaim], later: Iterable[IssueClaim]
) -> tuple[IssueClaim, ...]:
    merged: dict[str, IssueClaim] = {}
    for claim in (*earlier, *later):
        key = _claim_key(claim.issue_key)
        if not key:
            continue
        held = merged.get(key)
        merged[key] = claim if held is None else _updated_claim(held, claim)
    return tuple(merged.values())


def _updated_claim(earlier: IssueClaim, later: IssueClaim) -> IssueClaim:
    # The issue keeps the key as first written; only state and note move.
    if _states_a_state(later):
        return replace(later, issue_key=earlier.issue_key, note=later.note.strip() or earlier.note)
    return replace(earlier, note=_joined_notes(earlier.note, later.note))


def _states_a_state(claim: IssueClaim) -> bool:
    return claim.claimed_done or bool(claim.claimed_state and claim.claimed_state.strip())


def _claim_key(issue_key: str) -> str:
    return issue_key.strip().upper()


def _merged_requests(
    earlier: Iterable[CrossPersonMention], later: Iterable[CrossPersonMention]
) -> tuple[CrossPersonMention, ...]:
    merged: dict[tuple[str, tuple[str, ...]], CrossPersonMention] = {}
    for mention in (*earlier, *later):
        merged[(mention.kind, name_words(mention.raw_name))] = mention
    return tuple(merged.values())


def _joined_notes(earlier: str, later: str) -> str:
    first, second = earlier.strip(), later.strip()
    if not first:
        return second
    if not second or second.casefold() in first.casefold():
        return first
    if first.casefold() in second.casefold():
        return second
    if not first.endswith(_SENTENCE_END):
        first = f"{first}."
    return f"{first} {second}"
