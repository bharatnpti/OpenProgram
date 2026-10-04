"""Drift a check-in states and OpenProgram's own facts contradict.

A finalized check-in records these as ``checkin_drift`` facts, and the
project's drift findings (``RiskService._detect_project_drift``) show them next
to the other drift signals:

- ``said_in_review_no_mr``: someone said an issue is in review (or ready for
  review) and no open merge request names it (R1-10, SC6: Omar's IDP-6 "up for
  review" with only a branch). The merge requests are read again on every
  drift read, so the signal clears once a request for the issue is synced.

Merge requests are linked to issues with :mod:`merge_request_links`, the
matcher the ``merged_issue_open`` drift and the write-back use. A fact carries
issue keys, ids, display names and dates only, never reply text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from core.application.merge_request_links import (
    ISSUE_KEY,
    is_open_merge_request,
    merge_requests_by_issue_key,
)
from core.application.writeback_service import canonical_target_state
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from core.domain.status import IssueClaim, StatusSource
from core.domain.writeback import WriteBackTarget

CHECKIN_DRIFT_FACT_SOURCE = "checkin_drift"
SAID_IN_REVIEW_NO_MR = "said_in_review_no_mr"
# The follow-up asked when an issue said to be in review has no open merge request.
REVIEW_MR_QUESTION_LEAD = "I can't find a merge request for"


@dataclass(frozen=True, kw_only=True)
class CheckInDriftSignal:
    """One drift signal for an issue, worded for the drift views."""

    kind: str
    issue_key: str
    reason: str
    stated_by: str
    stated_source: StatusSource | None


def in_review_claim_keys(claims: Iterable[IssueClaim]) -> tuple[str, ...]:
    """The issues a check-in says are in review or ready for review now.

    The claimed state is read with the write-back's canonical mapping, so
    "up for review" and "waiting for review" count, and "ready for review by
    Friday" (a state still to come) does not.
    """
    return tuple(
        dict.fromkeys(
            claim.issue_key
            for claim in claims
            if claim.issue_key
            and not claim.claimed_done
            and canonical_target_state(claim.claimed_state) is WriteBackTarget.IN_REVIEW
        )
    )


def keys_without_open_merge_request(
    keys: Sequence[str], merge_request_facts: Iterable[FactEvent]
) -> tuple[str, ...]:
    """Those of ``keys`` that no open (or draft) merge request names."""
    linked = merge_requests_by_issue_key(merge_request_facts, set(keys))
    return tuple(
        key for key in keys if not any(is_open_merge_request(fact) for fact in linked.get(key, []))
    )


def review_without_merge_request_question(keys: Sequence[str]) -> str:
    """``I can't find a merge request for IDP-6 yet. Is it opened?``"""
    if len(keys) == 1:
        return f"{REVIEW_MR_QUESTION_LEAD} {keys[0]} yet. Is it opened?"
    named = f"{', '.join(keys[:-1])} or {keys[-1]}"
    return f"{REVIEW_MR_QUESTION_LEAD} {named} yet. Are they opened?"


def keys_asked_for_merge_request(agent_texts: Iterable[str]) -> set[str]:
    """The issues an earlier follow-up of the check-in already asked the merge request for."""
    return {
        key
        for text in agent_texts
        if text.startswith(REVIEW_MR_QUESTION_LEAD)
        for key in ISSUE_KEY.findall(text)
    }


def review_without_merge_request_fact(
    *,
    tenant_id: str,
    issue_key: str,
    developer_id: str,
    developer_name: str,
    as_of: date,
    status_source: StatusSource,
    observed_at: datetime,
    correlation_id: str,
) -> FactEvent:
    payload: dict[str, JsonScalar] = {
        "kind": SAID_IN_REVIEW_NO_MR,
        "issue_key": issue_key,
        "developer_id": developer_id,
        "developer_name": developer_name,
        "as_of": as_of.isoformat(),
        "status_source": status_source.value,
    }
    return FactEvent(
        tenant_id=tenant_id,
        source=CHECKIN_DRIFT_FACT_SOURCE,
        entity_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=issue_key),
        payload=payload,
        observed_at=observed_at,
        correlation_id=f"{correlation_id}:{SAID_IN_REVIEW_NO_MR}:{issue_key}",
    )


def checkin_drift_signals(
    facts: Iterable[FactEvent],
    *,
    issue_keys: set[str],
    as_of: date,
    merge_request_facts: Iterable[FactEvent],
) -> list[CheckInDriftSignal]:
    """The check-in drift signals for ``issue_keys`` stated on ``as_of``, latest per issue.

    A "said in review" signal is dropped once an open merge request names the
    issue.
    """
    latest: dict[tuple[str, str], FactEvent] = {}
    for fact in sorted(facts, key=lambda item: (item.observed_at, item.ingested_at)):
        if fact.source != CHECKIN_DRIFT_FACT_SOURCE or _text(fact.payload, "as_of") != (
            as_of.isoformat()
        ):
            continue
        kind = _text(fact.payload, "kind")
        key = _text(fact.payload, "issue_key")
        if kind is None or key is None or key not in issue_keys:
            continue
        latest[(kind, key)] = fact
    in_review = [key for kind, key in latest if kind == SAID_IN_REVIEW_NO_MR]
    still_missing = set(keys_without_open_merge_request(in_review, merge_request_facts))
    signals: list[CheckInDriftSignal] = []
    for (kind, key), fact in sorted(latest.items()):
        if kind == SAID_IN_REVIEW_NO_MR and key in still_missing:
            signals.append(
                CheckInDriftSignal(
                    kind=kind,
                    issue_key=key,
                    reason=(
                        f"{_speaker(fact.payload)} said {key} is in review, but no open merge "
                        "request names it."
                    ),
                    stated_by=_text(fact.payload, "developer_id") or "",
                    stated_source=_status_source(fact.payload),
                )
            )
    return signals


def _speaker(payload: Mapping[str, JsonScalar]) -> str:
    return _text(payload, "developer_name") or _text(payload, "developer_id") or "Someone"


def _status_source(payload: Mapping[str, JsonScalar]) -> StatusSource | None:
    value = _text(payload, "status_source")
    try:
        return StatusSource(value) if value is not None else None
    except ValueError:
        return None


def _text(payload: Mapping[str, JsonScalar], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None
