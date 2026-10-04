"""Drift a check-in states and OpenProgram's own facts contradict.

A finalized check-in records these as ``checkin_drift`` facts, and the
project's drift findings (``RiskService._detect_project_drift``) show them next
to the other drift signals:

- ``said_in_review_no_mr``: someone said an issue is in review (or ready for
  review) and no open merge request names it (R1-10, SC6: Omar's IDP-6 "up for
  review" with only a branch). The merge requests are read again on every
  drift read, so the signal clears once a request for the issue is synced.
  Only code work counts (N26, :func:`code_work_keys`): Mina's (PO) acceptance
  criteria review of CHK-10 has no merge request to find, so it is neither
  asked about nor flagged, and a signal recorded before is dropped on read.
- ``eta_disagreement``: two people gave different ETAs for one issue on the
  same day (N23: Ira said CHK-4 by Friday, its owner Liam by Tuesday). Each
  check-in records the ETA it states per issue (``eta_stated``); the drift read
  compares them, names both people and dates, and says the owner's ETA is the
  one used. Nothing else changes: each person's own status keeps their ETA.

Merge requests are linked to issues with :mod:`merge_request_links`, the
matcher the ``merged_issue_open`` drift and the write-back use. A fact carries
issue keys, ids, display names and dates only, never reply text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from core.application.merge_request_links import (
    ISSUE_KEY,
    is_open_merge_request,
    merge_requests_by_issue_key,
)
from core.application.status_summaries import day_label
from core.application.writeback_service import canonical_target_state
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from core.domain.status import IssueClaim, StatusSource
from core.domain.writeback import WriteBackTarget

CHECKIN_DRIFT_FACT_SOURCE = "checkin_drift"
SAID_IN_REVIEW_NO_MR = "said_in_review_no_mr"
ETA_STATED = "eta_stated"
ETA_DISAGREEMENT = "eta_disagreement"
# The follow-up asked when an issue said to be in review has no open merge request.
REVIEW_MR_QUESTION_LEAD = "I can't find a merge request for"
COMMIT_FACT_SOURCE = "vcs_commit"
# A member's app roles. A developer's review is code review; a product owner's,
# scrum master's or exec's review is not (N26). A member without roles is a
# developer, as sign-in reads them.
DEVELOPER_ROLE = "dev"
NON_CODE_REVIEW_ROLES = frozenset({"po", "sm", "exec"})
# A project's issues "normally have" merge requests when at least this share of
# them is named by one.
_PROJECT_MERGE_REQUEST_SHARE = 0.5


def member_roles(metadata: Mapping[str, JsonScalar] | None) -> frozenset[str]:
    """The app roles on a member's node (``"mgr,admin"``); none means a developer."""
    value = (metadata or {}).get("app_roles")
    if not isinstance(value, str):
        return frozenset({DEVELOPER_ROLE})
    roles = frozenset(item.strip().lower() for item in value.split(",") if item.strip())
    return roles or frozenset({DEVELOPER_ROLE})


def code_work_keys(
    keys: Sequence[str],
    *,
    developer_id: str,
    roles: frozenset[str],
    merge_request_facts: Sequence[FactEvent],
    commit_facts: Sequence[FactEvent],
    project_task_keys: Callable[[str], Collection[str]],
) -> tuple[str, ...]:
    """Those of ``keys`` whose "in review" is code work, so a merge request is expected.

    A developer's review is always code work, and a product owner's, scrum
    master's or exec's never is. For anyone else (a manager, say) an issue is
    code work when a branch, commit or merge request has named it before, or
    when its project's issues normally have merge requests and the person has
    Git activity of their own. ``project_task_keys`` gives the keys of the
    project an issue belongs to (empty when unknown).
    """
    if DEVELOPER_ROLE in roles:
        return tuple(keys)
    if roles & NON_CODE_REVIEW_ROLES or not keys:
        return ()
    with_code = _keys_with_code_activity(set(keys), merge_request_facts, commit_facts)
    person_has_git_activity = any(
        fact.entity_ref.kind is NodeKind.DEVELOPER and fact.entity_ref.id == developer_id
        for fact in (*merge_request_facts, *commit_facts)
    )
    return tuple(
        key
        for key in keys
        if key in with_code
        or (
            person_has_git_activity
            and _project_uses_merge_requests(project_task_keys(key), merge_request_facts)
        )
    )


def _keys_with_code_activity(
    keys: set[str], merge_request_facts: Sequence[FactEvent], commit_facts: Sequence[FactEvent]
) -> set[str]:
    """The keys a merge request (any state, by branch or title) or a commit message names."""
    found = set(merge_requests_by_issue_key(merge_request_facts, keys))
    upper = {key.upper(): key for key in keys}
    for fact in commit_facts:
        message = fact.payload.get("message")
        if isinstance(message, str):
            found.update(
                upper[match.upper()]
                for match in ISSUE_KEY.findall(message)
                if match.upper() in upper
            )
    return found


def _project_uses_merge_requests(
    task_keys: Collection[str], merge_request_facts: Sequence[FactEvent]
) -> bool:
    if not task_keys:
        return False
    named = merge_requests_by_issue_key(merge_request_facts, set(task_keys))
    return len(named) >= _PROJECT_MERGE_REQUEST_SHARE * len(task_keys)


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


@dataclass(frozen=True, kw_only=True)
class IssueEta:
    """An ETA a check-in states for one issue: as said, and the day it means."""

    label: str
    day: date


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})"
    r"(?:st|nd|rd|th)?\b"
)
_WEEKDAY = re.compile(r"\b(next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b")
_TOMORROW = re.compile(r"\btomorrow\b")
_TODAY = re.compile(r"\b(?:today|tonight|eod|end of (?:the )?day)\b")
_END_OF_WEEK = re.compile(r"\b(?:end of (?:the )?week|eow|this week)\b")
# A day is an ETA only when the words look ahead: "started today" or "reviewed
# on Monday" names a day that is no ETA.
_ETA_CUE = re.compile(
    r"\b(?:by|until|before|eta|due|target\w*|aim\w*|expect\w*|should|will|plan\w*|ready|"
    r"wrap\w*|finish\w*|complete|land\w*|ship\w*|deliver\w*|tomorrow|next|end of)\b"
)


def issue_eta(claim: IssueClaim, stated_on: date) -> IssueEta | None:
    """The ETA a claim states for its issue, read deterministically from its words.

    The most specific wording wins: a date, then a weekday (the next one on or
    after ``stated_on``; "next Tuesday" a week later), then tomorrow or today,
    then the end of the week (its Friday). None when the claim names no day,
    when its words do not look ahead, and for an issue said to be done.
    """
    if claim.claimed_done or canonical_target_state(claim.claimed_state) is WriteBackTarget.DONE:
        return None
    text = " ".join(part for part in (claim.note, claim.claimed_state or "") if part).lower()
    if not text or not _ETA_CUE.search(text):
        return None
    for read in (_dated_eta, _weekday_eta, _relative_eta):
        eta = read(text, stated_on)
        if eta is not None:
            return eta
    return None


def _dated_eta(text: str, stated_on: date) -> IssueEta | None:
    day: date | None = None
    if match := _ISO_DATE.search(text):
        try:
            day = date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            day = None
    if day is None and (match := _MONTH_DAY.search(text)):
        day = _month_day(stated_on, _MONTHS.index(match[1][:3]) + 1, int(match[2]))
    return IssueEta(label=day_label(day), day=day) if day is not None else None


def _weekday_eta(text: str, stated_on: date) -> IssueEta | None:
    match = _WEEKDAY.search(text)
    if match is None:
        return None
    weekday = _WEEKDAYS.index(match[2])
    day = stated_on + timedelta(days=(weekday - stated_on.weekday()) % 7)
    label = match[2].capitalize()
    if match[1]:
        return IssueEta(label=f"next {label}", day=day + timedelta(days=7))
    return IssueEta(label=label, day=day)


def _relative_eta(text: str, stated_on: date) -> IssueEta | None:
    if _TOMORROW.search(text):
        return IssueEta(label="tomorrow", day=stated_on + timedelta(days=1))
    if _TODAY.search(text):
        return IssueEta(label="today", day=stated_on)
    if _END_OF_WEEK.search(text):
        friday = stated_on + timedelta(days=(4 - stated_on.weekday()) % 7)
        return IssueEta(label="end of week", day=friday)
    return None


def eta_stated_fact(
    *,
    tenant_id: str,
    issue_key: str,
    eta: IssueEta,
    developer_id: str,
    developer_name: str,
    as_of: date,
    observed_at: datetime,
    correlation_id: str,
) -> FactEvent:
    payload: dict[str, JsonScalar] = {
        "kind": ETA_STATED,
        "issue_key": issue_key,
        "developer_id": developer_id,
        "developer_name": developer_name,
        "as_of": as_of.isoformat(),
        "eta_label": eta.label,
        "eta_date": eta.day.isoformat(),
    }
    return FactEvent(
        tenant_id=tenant_id,
        source=CHECKIN_DRIFT_FACT_SOURCE,
        entity_ref=EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=issue_key),
        payload=payload,
        observed_at=observed_at,
        correlation_id=f"{correlation_id}:{ETA_STATED}:{issue_key}",
    )


def checkin_drift_signals(
    facts: Iterable[FactEvent],
    *,
    issue_keys: set[str],
    as_of: date,
    merge_request_facts: Iterable[FactEvent],
    owners: Mapping[str, str] | None = None,
    is_code_work: Callable[[str, str], bool] | None = None,
) -> list[CheckInDriftSignal]:
    """The check-in drift signals for ``issue_keys`` stated on ``as_of``.

    A "said in review" signal, the latest per issue, is dropped once an open
    merge request names the issue, and when ``is_code_work(issue_key,
    developer_id)`` says the review was not code work (N26), so a signal
    recorded before that rule clears on the next read. ETAs are compared per
    issue across people, each person's latest; ``owners`` maps an issue key to
    its assignee.
    """
    in_review: dict[str, FactEvent] = {}
    etas: dict[str, dict[str, FactEvent]] = {}
    for fact in sorted(facts, key=lambda item: (item.observed_at, item.ingested_at)):
        if fact.source != CHECKIN_DRIFT_FACT_SOURCE or _text(fact.payload, "as_of") != (
            as_of.isoformat()
        ):
            continue
        kind = _text(fact.payload, "kind")
        key = _text(fact.payload, "issue_key")
        if key is None or key not in issue_keys:
            continue
        if kind == SAID_IN_REVIEW_NO_MR:
            stated_by = _text(fact.payload, "developer_id") or ""
            if is_code_work is None or is_code_work(key, stated_by):
                in_review[key] = fact
        elif kind == ETA_STATED and (speaker := _text(fact.payload, "developer_id")):
            etas.setdefault(key, {})[speaker] = fact
    still_missing = set(keys_without_open_merge_request(list(in_review), merge_request_facts))
    signals = [
        CheckInDriftSignal(
            kind=SAID_IN_REVIEW_NO_MR,
            issue_key=key,
            reason=(
                f"{_speaker(fact.payload)} said {key} is in review, but no open merge "
                "request names it."
            ),
            stated_by=_text(fact.payload, "developer_id") or "",
            stated_source=_status_source(fact.payload),
        )
        for key, fact in sorted(in_review.items())
        if key in still_missing
    ]
    for key, by_person in sorted(etas.items()):
        signal = _eta_disagreement(key, by_person, (owners or {}).get(key))
        if signal is not None:
            signals.append(signal)
    return signals


def _eta_disagreement(
    issue_key: str, by_person: Mapping[str, FactEvent], owner_id: str | None
) -> CheckInDriftSignal | None:
    """``ETAs disagree for CHK-4: Liam Chen (owner) said Tuesday, Oct 6; ...``."""
    stated = [
        (person, fact, eta_day)
        for person, fact in by_person.items()
        if (eta_day := _text(fact.payload, "eta_date")) is not None
    ]
    if len({eta_day for _, _, eta_day in stated}) < 2:
        return None
    # The owner first, then the others in the order they said it.
    stated.sort(key=lambda item: (item[0] != owner_id, item[1].observed_at))
    parts = [
        f"{_speaker(fact.payload)}{' (owner)' if person == owner_id else ''} said "
        f"{_eta_words(fact.payload, eta_day)}"
        for person, fact, eta_day in stated
    ]
    used = " The owner's ETA is the one used." if owner_id in by_person else ""
    return CheckInDriftSignal(
        kind=ETA_DISAGREEMENT,
        issue_key=issue_key,
        reason=f"ETAs disagree for {issue_key}: {'; '.join(parts)}.{used}",
        stated_by=stated[0][0],
        stated_source=None,
    )


def _eta_words(payload: Mapping[str, JsonScalar], eta_day: str) -> str:
    day = day_label(date.fromisoformat(eta_day))
    label = _text(payload, "eta_label")
    return day if label is None or label == day else f"{label}, {day}"


def _month_day(stated_on: date, month: int, day: int) -> date | None:
    """That day of the month next on or after ``stated_on`` (give or take a month)."""
    for year in (stated_on.year, stated_on.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= stated_on - timedelta(days=31):
            return candidate
    return None


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
