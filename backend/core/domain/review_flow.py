"""How a pull or merge request moves from its first commit to its merge, and what work it is.

A request passes four stages, each read from a timestamp the Git provider keeps:

- **Coding**: the first commit, to when it was ready for review (when it was
  opened, if it was never a draft).
- **Awaiting review**: ready, to the first note or approval by someone other
  than its author.
- **In review**: that first review, to the last approval still standing (or the
  last review note, when nobody approved).
- **Awaiting merge**: that approval, to the merge.

Notes by the author, by bots and by nobody (system notes) are not review.
Activity after the merge does not move a stage. A request merged with no review
has a coding stage and no review stages: it skipped them, it did not wait.

Each request also gets one type, from the first rule that names one: a
dependency bot wrote it; the type of the Jira issue its title or branch names;
a label; a conventional prefix in its title ("feat:", "fix(api):"); a prefix in
its branch ("feat/..."); otherwise it is unclassified.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from core.domain.graph import JsonScalar
from core.domain.integrations import PullRequestEvent, PullRequestEventKind


class ReviewStage(StrEnum):
    CODING = "coding"
    AWAITING_REVIEW = "awaiting_review"
    IN_REVIEW = "in_review"
    AWAITING_MERGE = "awaiting_merge"


REVIEW_STAGE_ORDER: tuple[ReviewStage, ...] = tuple(ReviewStage)

REVIEW_STAGE_LABELS: Mapping[ReviewStage, str] = {
    ReviewStage.CODING: "Coding",
    ReviewStage.AWAITING_REVIEW: "Awaiting review",
    ReviewStage.IN_REVIEW: "In review",
    ReviewStage.AWAITING_MERGE: "Awaiting merge",
}


class RequestType(StrEnum):
    FEATURE = "feature"
    DEPENDENCY_UPDATE = "dependency_update"
    BUG_FIX = "bug_fix"
    REFACTOR = "refactor"
    #: Chores and internal tooling (CI, build scripts, developer tools) are one
    #: type: no rule but a label tells them apart.
    CHORE = "chore"
    DOCUMENTATION = "documentation"
    TEST = "test"
    PERFORMANCE = "performance"
    UNCLASSIFIED = "unclassified"


REQUEST_TYPE_ORDER: tuple[RequestType, ...] = tuple(RequestType)

REQUEST_TYPE_LABELS: Mapping[RequestType, str] = {
    RequestType.FEATURE: "Feature",
    RequestType.DEPENDENCY_UPDATE: "Dependency update",
    RequestType.BUG_FIX: "Bug fix",
    RequestType.REFACTOR: "Refactor",
    RequestType.CHORE: "Chore or tooling",
    RequestType.DOCUMENTATION: "Documentation",
    RequestType.TEST: "Test only",
    RequestType.PERFORMANCE: "Performance",
    RequestType.UNCLASSIFIED: "Unclassified",
}


class TypeSource(StrEnum):
    """Which rule gave a request its type."""

    AUTHOR = "author"
    ISSUE = "issue"
    LABEL = "label"
    TITLE = "title"
    BRANCH = "branch"
    NONE = "none"


@dataclass(frozen=True, kw_only=True)
class RequestClassification:
    request_type: RequestType
    source: TypeSource
    #: What the type was read from: "Bug CHK-3", "bug", "fix(api):", "renovate[bot]".
    evidence: str | None = None


UNCLASSIFIED = RequestClassification(request_type=RequestType.UNCLASSIFIED, source=TypeSource.NONE)

# An issue key in a title or branch ("CHK-3-payment-intent", "INS-2: backfill"),
# matched whole, as merge_request_links reads it.
_ISSUE_KEY = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9_]*-\d+)(?![0-9])")
_DEPENDENCY_BOTS = ("renovate", "dependabot")

# Tracker issue types that say what the work is. Task, sub-task and spike say
# nothing about it, so the next rule decides.
_ISSUE_TYPES: Mapping[str, RequestType] = {
    "bug": RequestType.BUG_FIX,
    "defect": RequestType.BUG_FIX,
    "incident": RequestType.BUG_FIX,
    "story": RequestType.FEATURE,
    "user story": RequestType.FEATURE,
    "feature": RequestType.FEATURE,
    "new feature": RequestType.FEATURE,
    "epic": RequestType.FEATURE,
    "improvement": RequestType.FEATURE,
    "enhancement": RequestType.FEATURE,
    "requirement": RequestType.FEATURE,
    "documentation": RequestType.DOCUMENTATION,
    "tech debt": RequestType.REFACTOR,
    "technical debt": RequestType.REFACTOR,
    "refactoring": RequestType.REFACTOR,
    "test": RequestType.TEST,
    "chore": RequestType.CHORE,
    "maintenance": RequestType.CHORE,
}

_LABELS: Mapping[str, RequestType] = {
    **{name: RequestType.FEATURE for name in ("feature", "feat", "enhancement", "story")},
    **{
        name: RequestType.BUG_FIX
        for name in ("bug", "bugfix", "bug fix", "fix", "defect", "hotfix", "regression")
    },
    **{
        name: RequestType.DEPENDENCY_UPDATE
        for name in ("dependencies", "dependency", "deps", "dependency update")
    },
    **{
        name: RequestType.REFACTOR
        for name in ("refactor", "refactoring", "tech debt", "tech-debt", "technical debt")
    },
    **{
        name: RequestType.CHORE
        for name in ("chore", "maintenance", "tooling", "internal tooling", "ci", "devops")
    },
    **{name: RequestType.DOCUMENTATION for name in ("documentation", "docs", "doc")},
    **{name: RequestType.TEST for name in ("test", "tests", "testing", "test only")},
    **{name: RequestType.PERFORMANCE for name in ("performance", "perf")},
}

# When labels name several types, the one that says most about what changed for
# users wins: a "bug" + "tests" request is a bug fix.
_LABEL_PRECEDENCE: tuple[RequestType, ...] = (
    RequestType.BUG_FIX,
    RequestType.FEATURE,
    RequestType.DEPENDENCY_UPDATE,
    RequestType.PERFORMANCE,
    RequestType.REFACTOR,
    RequestType.TEST,
    RequestType.DOCUMENTATION,
    RequestType.CHORE,
)

_PREFIXES: Mapping[str, RequestType] = {
    "feat": RequestType.FEATURE,
    "feature": RequestType.FEATURE,
    "fix": RequestType.BUG_FIX,
    "bugfix": RequestType.BUG_FIX,
    "hotfix": RequestType.BUG_FIX,
    "chore": RequestType.CHORE,
    "ci": RequestType.CHORE,
    "style": RequestType.CHORE,
    "docs": RequestType.DOCUMENTATION,
    "doc": RequestType.DOCUMENTATION,
    "refactor": RequestType.REFACTOR,
    "test": RequestType.TEST,
    "tests": RequestType.TEST,
    "perf": RequestType.PERFORMANCE,
    "build": RequestType.DEPENDENCY_UPDATE,
    "deps": RequestType.DEPENDENCY_UPDATE,
    "renovate": RequestType.DEPENDENCY_UPDATE,
    "dependabot": RequestType.DEPENDENCY_UPDATE,
}
_DEPENDENCY_SCOPES = frozenset({"deps", "dep", "dependencies", "dependency"})

# "Draft: [CHK-3] feat(api)!: ..." -> type "feat", scope "api".
_TITLE_LEAD = re.compile(
    r"^\s*(?:(?:draft|wip)\s*:\s*)?(?:\[[^\]]*\]\s*)*(?:[A-Za-z][A-Za-z0-9_]*-\d+[\s:,]*)*",
    re.IGNORECASE,
)
_CONVENTIONAL = re.compile(r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)]*)\))?!?\s*:")


def is_bot_login(login: str) -> bool:
    """A service account by its login: "renovate[bot]", "gitlab-bot", "project_7_bot_x"."""
    name = login.strip().casefold()
    return name.endswith("[bot]") or re.search(r"(^|[-_.])bot($|[-_.\d])", name) is not None


def is_dependency_bot(*names: str | None) -> bool:
    return any(bot in name.casefold() for name in names if name for bot in _DEPENDENCY_BOTS)


def issue_keys_named(*texts: str | None) -> list[str]:
    """The issue keys a title and branch name, upper-cased, in the order they appear."""
    keys: list[str] = []
    for text in texts:
        for match in _ISSUE_KEY.findall(text or ""):
            key = match.upper()
            if key not in keys:
                keys.append(key)
    return keys


def classify_request(
    *,
    title: str,
    branch: str | None,
    labels: Sequence[str],
    author: str | None,
    author_name: str | None,
    issue_types: Mapping[str, str],
) -> RequestClassification:
    """The request's type, from the first rule that names one (see the module doc).

    ``author`` and ``author_name`` are the provider's login and name of who
    opened it; ``issue_types`` maps an upper-cased issue key to its tracker type.
    A dependency bot is asked first, before Jira or the title: its requests are
    dependency updates whatever they say ("fix(deps): update dependency x").
    """
    if is_dependency_bot(author, author_name) or is_dependency_bot((branch or "").split("/", 1)[0]):
        return RequestClassification(
            request_type=RequestType.DEPENDENCY_UPDATE,
            source=TypeSource.AUTHOR,
            evidence=author or author_name or branch,
        )
    for key in issue_keys_named(title, branch):
        issue_type = issue_types.get(key)
        mapped = _ISSUE_TYPES.get((issue_type or "").strip().casefold())
        if mapped is not None:
            return RequestClassification(
                request_type=mapped, source=TypeSource.ISSUE, evidence=f"{issue_type} {key}"
            )
    by_label: dict[RequestType, str] = {}
    for label in labels:
        mapped = _LABELS.get(_label_name(label))
        if mapped is not None:
            by_label.setdefault(mapped, label)
    for candidate in _LABEL_PRECEDENCE:
        if candidate in by_label:
            return RequestClassification(
                request_type=candidate, source=TypeSource.LABEL, evidence=by_label[candidate]
            )
    title_type = _conventional_type(title)
    if title_type is not None:
        return RequestClassification(
            request_type=title_type[0], source=TypeSource.TITLE, evidence=title_type[1]
        )
    branch_prefix = (
        (branch or "").split("/", 1)[0].strip().casefold() if "/" in (branch or "") else ""
    )
    if branch_prefix in _PREFIXES:
        return RequestClassification(
            request_type=_PREFIXES[branch_prefix],
            source=TypeSource.BRANCH,
            evidence=f"{branch_prefix}/",
        )
    return UNCLASSIFIED


def _label_name(label: str) -> str:
    """A label without its scope: "type::bug", "kind/bug" and "Type: Bug" are "bug"."""
    name = label.strip().casefold()
    for separator in ("::", "/", ":"):
        if separator in name:
            name = name.rsplit(separator, 1)[1]
    return name.strip()


def _conventional_type(title: str) -> tuple[RequestType, str] | None:
    lead = _TITLE_LEAD.match(title)
    rest = title[lead.end() :] if lead else title
    match = _CONVENTIONAL.match(rest.strip())
    if match is None:
        return None
    prefix = match.group("type").casefold()
    scope = (match.group("scope") or "").strip().casefold()
    evidence = match.group(0).strip()
    if scope in _DEPENDENCY_SCOPES:
        return RequestType.DEPENDENCY_UPDATE, evidence
    mapped = _PREFIXES.get(prefix)
    return (mapped, evidence) if mapped is not None else None


@dataclass(frozen=True, kw_only=True)
class ReviewTimeline:
    """The moments a request's stages start and end. Any of them may be unknown."""

    first_commit_at: datetime | None
    ready_at: datetime | None
    first_review_at: datetime | None
    #: The latest approval still standing at the merge (or now).
    approved_at: datetime | None
    last_review_at: datetime | None
    #: People other than the author who reviewed it.
    reviewer_count: int

    @property
    def review_end(self) -> datetime | None:
        """Where In review ends: the last approval, else the last review note."""
        return self.approved_at or self.last_review_at or self.first_review_at


_REVIEW_KINDS = frozenset(
    {PullRequestEventKind.COMMENT, PullRequestEventKind.REVIEW, PullRequestEventKind.APPROVAL}
)


def review_timeline(
    events: Iterable[PullRequestEvent],
    *,
    author: str,
    opened_at: datetime | None,
    draft: bool,
    merged_at: datetime | None,
) -> ReviewTimeline:
    """Read the stage boundaries off a request's history (see the module doc).

    A request opened as a draft has a "ready" mark before any "draft" mark; it
    is ready at the last ready mark before the first review that follows one
    (marking it draft again and back does not restart a wait a reviewer already
    ended). A request opened ready is ready when it was opened. One that is a
    draft now and not merged is still being written.
    """
    ordered = sorted(
        (event for event in events if merged_at is None or event.at <= merged_at),
        key=lambda event: event.at,
    )
    commits = [event.at for event in ordered if event.kind is PullRequestEventKind.COMMIT]
    reviews = [
        event for event in ordered if event.kind in _REVIEW_KINDS and _is_reviewer(event, author)
    ]
    marks = [
        event
        for event in ordered
        if event.kind in (PullRequestEventKind.DRAFT, PullRequestEventKind.READY)
    ]
    ready_at = _ready_at(marks, reviews, opened_at=opened_at, draft=draft, merged=merged_at)
    after_ready = [event for event in reviews if ready_at is not None and event.at >= ready_at]
    standing: dict[str, datetime] = {}
    if ready_at is not None:
        for event in ordered:
            if event.at < ready_at or event.actor is None:
                continue
            if event.kind is PullRequestEventKind.APPROVAL and _is_reviewer(event, author):
                standing[event.actor.casefold()] = event.at
            elif event.kind is PullRequestEventKind.UNAPPROVAL:
                standing.pop(event.actor.casefold(), None)
    return ReviewTimeline(
        first_commit_at=commits[0] if commits else None,
        ready_at=ready_at,
        first_review_at=after_ready[0].at if after_ready else None,
        approved_at=max(standing.values()) if standing else None,
        last_review_at=after_ready[-1].at if after_ready else None,
        reviewer_count=len({(event.actor or "").casefold() for event in after_ready}),
    )


def _ready_at(
    marks: Sequence[PullRequestEvent],
    reviews: Sequence[PullRequestEvent],
    *,
    opened_at: datetime | None,
    draft: bool,
    merged: datetime | None,
) -> datetime | None:
    if draft and merged is None:
        return None
    opened_as_draft = bool(marks) and marks[0].kind is PullRequestEventKind.READY
    if not opened_as_draft:
        return opened_at
    ready_marks = [mark.at for mark in marks if mark.kind is PullRequestEventKind.READY]
    first_ready = ready_marks[0]
    reviewed = [event.at for event in reviews if event.at >= first_ready]
    if not reviewed:
        return ready_marks[-1]
    return max(mark for mark in ready_marks if mark <= reviewed[0])


def _is_reviewer(event: PullRequestEvent, author: str) -> bool:
    actor = event.actor
    if actor is None or not actor.strip():
        return False
    return actor.strip().casefold() != author.strip().casefold() and not is_bot_login(actor)


def stage_hours(
    timeline: ReviewTimeline, *, merged_at: datetime | None
) -> dict[ReviewStage, float | None]:
    """Hours spent in each stage the request has finished; None for one not finished or not known.

    Coding that would end before it starts (the request was opened before its
    first commit) is zero. A request with no review has no review stages.
    """
    hours: dict[ReviewStage, float | None] = dict.fromkeys(REVIEW_STAGE_ORDER)
    hours[ReviewStage.CODING] = _hours(timeline.first_commit_at, timeline.ready_at, floor=True)
    if timeline.first_review_at is None:
        return hours
    hours[ReviewStage.AWAITING_REVIEW] = _hours(timeline.ready_at, timeline.first_review_at)
    end = timeline.review_end
    if merged_at is not None or timeline.approved_at is not None:
        hours[ReviewStage.IN_REVIEW] = _hours(timeline.first_review_at, end)
    if merged_at is not None:
        hours[ReviewStage.AWAITING_MERGE] = _hours(end, merged_at)
    return hours


def current_stage(
    timeline: ReviewTimeline, *, draft: bool, opened_at: datetime | None
) -> tuple[ReviewStage, datetime | None]:
    """The stage an open request is in, and since when (None when that is not known)."""
    if draft or timeline.ready_at is None:
        return ReviewStage.CODING, timeline.first_commit_at or opened_at
    if timeline.first_review_at is None:
        return ReviewStage.AWAITING_REVIEW, timeline.ready_at
    if timeline.approved_at is not None:
        return ReviewStage.AWAITING_MERGE, timeline.approved_at
    return ReviewStage.IN_REVIEW, timeline.first_review_at


def _hours(start: datetime | None, end: datetime | None, *, floor: bool = False) -> float | None:
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    if seconds < 0:
        return 0.0 if floor else None
    return seconds / 3600.0


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """The value at ``fraction`` (0.5 for the median) of ``values``, interpolated; None for none."""
    if not values:
        return None
    ordered = sorted(values)
    rank = fraction * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


# Fact payload keys a pull request's history is kept under.
TIMELINE_READ_KEY = "timeline_read"
_TIMELINE_KEYS = (
    "first_commit_at",
    "ready_at",
    "first_review_at",
    "approved_at",
    "last_review_at",
)


def timeline_payload(timeline: ReviewTimeline) -> dict[str, JsonScalar]:
    """The timeline as fact payload fields: ISO times, the reviewer count and a read flag."""
    payload: dict[str, JsonScalar] = {
        TIMELINE_READ_KEY: True,
        "reviewer_count": timeline.reviewer_count,
    }
    for key in _TIMELINE_KEYS:
        value = getattr(timeline, key)
        payload[key] = value.isoformat() if value is not None else None
    return payload


def timeline_from_payload(payload: Mapping[str, JsonScalar]) -> ReviewTimeline | None:
    """The timeline a fact keeps, or None for a fact written before histories were read."""
    if payload.get(TIMELINE_READ_KEY) is not True:
        return None
    count = payload.get("reviewer_count")
    return ReviewTimeline(
        first_commit_at=payload_datetime(payload, "first_commit_at"),
        ready_at=payload_datetime(payload, "ready_at"),
        first_review_at=payload_datetime(payload, "first_review_at"),
        approved_at=payload_datetime(payload, "approved_at"),
        last_review_at=payload_datetime(payload, "last_review_at"),
        reviewer_count=count if isinstance(count, int) and not isinstance(count, bool) else 0,
    )


def labels_payload(labels: Sequence[str]) -> str | None:
    """Labels as one fact field: a JSON list, since a label may hold a comma."""
    names = [label for label in labels if label.strip()]
    return json.dumps(names, ensure_ascii=False) if names else None


def labels_from_payload(payload: Mapping[str, JsonScalar]) -> tuple[str, ...]:
    value = payload.get("labels")
    if not isinstance(value, str) or not value:
        return ()
    try:
        parsed = json.loads(value)
    except ValueError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(item for item in parsed if isinstance(item, str))


def payload_datetime(payload: Mapping[str, JsonScalar], key: str) -> datetime | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
