from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from core.domain.graph import EntityRef, NodeKind
from core.domain.status import CrossPersonMention


class CrossPersonRequestKind(StrEnum):
    DEPENDENCY = "dependency"
    REVIEW = "review"
    INPUT = "input"


class CrossPersonRequestStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    NEEDS_RESOLUTION = "needs_resolution"


class CrossPersonDelivery(StrEnum):
    """Whether the counterpart has been sent the DM about a request."""

    SENT = "sent"
    RETRYING = "retrying"
    NOT_DELIVERED = "not_delivered"


@dataclass(frozen=True, kw_only=True)
class CrossPersonRequest:
    tenant_id: str
    id: str
    requester_id: str
    requester_chat_ref: str | None
    counterpart_id: str | None
    kind: CrossPersonRequestKind
    note: str
    source_correlation_id: str
    status: CrossPersonRequestStatus
    created_at: datetime
    updated_at: datetime
    task_ref: EntityRef | None = None
    raw_name: str | None = None
    email: str | None = None
    counterpart_display_name: str | None = None
    counterpart_email: str | None = None
    notify_message_id: str | None = None
    notify_correlation_id: str | None = None
    # Counterpart DM attempts. The next attempt time is set when an attempt is
    # claimed and cleared once the DM is recorded or the attempts run out.
    notify_attempts: int = 0
    notify_last_attempt_at: datetime | None = None
    notify_next_attempt_at: datetime | None = None

    @property
    def notified(self) -> bool:
        return self.notify_message_id is not None or self.notify_correlation_id is not None

    @property
    def delivery(self) -> CrossPersonDelivery | None:
        """Where the counterpart DM has got to; None when none was attempted.

        No attempt means there was nobody to tell (unmatched, oneself) or
        notification was off when the request was recorded.
        """
        if self.notified:
            return CrossPersonDelivery.SENT
        if self.notify_attempts == 0:
            return None
        if self.status is CrossPersonRequestStatus.OPEN and self.notify_next_attempt_at is not None:
            return CrossPersonDelivery.RETRYING
        return CrossPersonDelivery.NOT_DELIVERED


@dataclass(frozen=True, kw_only=True)
class CrossPersonNotifyRetrySummary:
    """One retry pass over counterpart DMs that have not been sent."""

    due: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    given_up: int = 0


@dataclass(frozen=True, kw_only=True)
class CrossPersonRequestResolution:
    mention: CrossPersonMention
    status: CrossPersonRequestStatus
    counterpart_id: str | None = None
    counterpart_display_name: str | None = None
    counterpart_email: str | None = None
    # Set when this settles a request already recorded as needs_resolution
    # (the requester answered "who did you mean?"), rather than a new one.
    request_id: str | None = None


def new_cross_person_request(
    *,
    tenant_id: str,
    id: str,
    requester_id: str,
    requester_chat_ref: str | None,
    source_correlation_id: str,
    resolution: CrossPersonRequestResolution,
    created_at: datetime | None = None,
) -> CrossPersonRequest:
    observed_at = created_at or datetime.now(tz=UTC)
    issue_key = first_issue_key(resolution.mention.note)
    return CrossPersonRequest(
        tenant_id=tenant_id,
        id=id,
        requester_id=requester_id,
        requester_chat_ref=requester_chat_ref,
        counterpart_id=resolution.counterpart_id,
        kind=CrossPersonRequestKind(resolution.mention.kind),
        note=resolution.mention.note,
        source_correlation_id=source_correlation_id,
        status=resolution.status,
        created_at=observed_at,
        updated_at=observed_at,
        raw_name=resolution.mention.raw_name,
        email=resolution.mention.email,
        counterpart_display_name=resolution.counterpart_display_name,
        counterpart_email=resolution.counterpart_email,
        task_ref=(
            EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=issue_key)
            if issue_key is not None
            else None
        ),
    )


# An issue key as people type it in an ask: "CHK-10", "IDP-3". Upper-case
# project keys only, so prose like "follow-up" or "covid-19" is not one.
_ISSUE_KEY = re.compile(r"(?<![A-Za-z0-9_-])([A-Z][A-Z0-9]+-\d+)(?![A-Za-z0-9])")
# A merge request as people name it: "storefront-web !1", "checkout-api MR !3",
# or just "!3". The repository is the word before the sigil, when there is one.
_MERGE_REQUEST = re.compile(
    r"(?:(?<![\w./-])([A-Za-z0-9][\w.-]*/)?([A-Za-z0-9][\w.-]*)\s+(?:(?:mr|pr)\s+)?)?!(\d+)\b",
    re.IGNORECASE,
)
_SUBJECT_WORD = re.compile(r"[a-z][a-z0-9]+")
# Words every ask has, which say nothing about what it is about.
_GENERIC_ASK_WORDS = frozenset(
    {
        "and",
        "the",
        "for",
        "with",
        "from",
        "this",
        "that",
        "his",
        "her",
        "their",
        "our",
        "your",
        "its",
        "into",
        "onto",
        "about",
        "need",
        "needs",
        "needed",
        "please",
        "review",
        "reviews",
        "reviewed",
        "reviewing",
        "approve",
        "approval",
        "approved",
        "approving",
        "merge",
        "merged",
        "merging",
        "request",
        "requests",
        "look",
        "looking",
        "check",
        "waiting",
        "wait",
        "pending",
        "input",
        "help",
        "get",
        "give",
        "pr",
        "mr",
        "prs",
        "mrs",
        "change",
        "changes",
        "can",
        "could",
        "would",
        "will",
        "you",
        "him",
        "them",
        "take",
        "quick",
        "some",
        "any",
        "when",
        "what",
        "who",
        "how",
        "has",
        "have",
        "had",
        "was",
        "are",
        "been",
        "not",
        "yet",
        "still",
        "just",
        "also",
        "all",
        "now",
        "today",
        "tomorrow",
        "asap",
        "soon",
    }
)


@dataclass(frozen=True, kw_only=True)
class MergeRequestRef:
    """A merge request an ask names; ``repo`` is None for a bare "!3"."""

    repo: str | None
    number: str


@dataclass(frozen=True, kw_only=True)
class RequestSubject:
    """What one ask is about: the issues, merge requests and words it names."""

    issue_keys: frozenset[str]
    merge_requests: frozenset[MergeRequestRef]
    words: frozenset[str]

    @property
    def is_vague(self) -> bool:
        return not (self.issue_keys or self.merge_requests or self.words)


def issue_keys_in(text: str) -> frozenset[str]:
    return frozenset(match.upper() for match in _ISSUE_KEY.findall(text))


def first_issue_key(text: str) -> str | None:
    match = _ISSUE_KEY.search(text)
    return match.group(1).upper() if match is not None else None


def merge_request_refs_in(text: str) -> frozenset[MergeRequestRef]:
    refs: set[MergeRequestRef] = set()
    for _owner, repo, number in _MERGE_REQUEST.findall(text):
        name = repo.casefold() if repo else None
        if name in _GENERIC_ASK_WORDS or (name is not None and not _looks_like_repo(name)):
            name = None
        refs.add(MergeRequestRef(repo=name, number=number))
    return frozenset(refs)


def request_subject(request: CrossPersonRequest) -> RequestSubject:
    return subject_of_text(
        request.note,
        extra_issue_keys=(request.task_ref.id,) if request.task_ref is not None else (),
    )


def subject_of_text(text: str, *, extra_issue_keys: tuple[str, ...] = ()) -> RequestSubject:
    keys = issue_keys_in(text) | {key.upper() for key in extra_issue_keys}
    refs = merge_request_refs_in(text)
    plain = _MERGE_REQUEST.sub(" ", _ISSUE_KEY.sub(" ", text)).casefold()
    words = frozenset(
        word
        for word in _SUBJECT_WORD.findall(plain)
        if len(word) > 2 and word not in _GENERIC_ASK_WORDS
    )
    return RequestSubject(issue_keys=keys, merge_requests=refs, words=words)


def same_subject(first: RequestSubject, second: RequestSubject, *, vague_matches: bool) -> bool:
    """Whether two asks of the same person are about the same work.

    Issue keys decide when both name some, then merge requests named with
    their repository; otherwise their words must share two (or all of the
    shorter one's single word). A vague ask ("can you review?") names nothing
    to compare: with ``vague_matches`` it is taken to be the same ask, which
    is right for refreshing a repeated ask and too loose for closing one.
    """
    if first.issue_keys and second.issue_keys:
        return bool(first.issue_keys & second.issue_keys)
    first_named = {ref for ref in first.merge_requests if ref.repo is not None}
    second_named = {ref for ref in second.merge_requests if ref.repo is not None}
    if first_named and second_named:
        return bool(first_named & second_named)
    if first.is_vague or second.is_vague:
        return vague_matches
    shared = first.words & second.words
    needed = min(2, len(first.words), len(second.words))
    return needed > 0 and len(shared) >= needed


def is_repeat_of(
    request: CrossPersonRequest,
    other: CrossPersonRequest,
    *,
    vague_matches: bool,
) -> bool:
    """The same ask of the same person by the same requester, another row."""
    return (
        request.id != other.id
        and request.tenant_id == other.tenant_id
        and request.requester_id == other.requester_id
        and request.counterpart_id is not None
        and request.counterpart_id == other.counterpart_id
        and same_subject(
            request_subject(request), request_subject(other), vague_matches=vague_matches
        )
    )


def _looks_like_repo(name: str) -> bool:
    # "storefront-web", "platform_libs", "api.v2": a word with a joiner, or a
    # plain word that is not an everyday one ("merge !1", "in !3" are not).
    return any(joiner in name for joiner in "-_.") or name not in _COMMON_WORDS


_COMMON_WORDS = frozenset(
    {"in", "on", "of", "to", "at", "my", "is", "see", "and", "or", "the", "it", "via", "per"}
)


# Words that stand for a role, a group or nobody in particular. A request needs
# a named person: "waiting on reviewer" names no one, so it is not a request.
_PLACEHOLDER_NAMES = frozenset(
    {
        "someone",
        "somebody",
        "anyone",
        "anybody",
        "everyone",
        "everybody",
        "nobody",
        "no one",
        "reviewer",
        "reviewers",
        "approver",
        "approvers",
        "maintainer",
        "maintainers",
        "team",
        "qa",
        "devs",
        "developer",
        "developers",
        "engineer",
        "engineers",
        "tester",
        "testers",
        "lead",
        "tech lead",
        "team lead",
        "manager",
        "product owner",
        "scrum master",
        "code owner",
        "code owners",
    }
)
# Short words that are also first names ("Dev") count as a role only after a
# determiner: "a dev" is a role, "Dev" may be a person.
_PLACEHOLDER_AFTER_DETERMINER = frozenset({"dev", "pm", "po", "sm", "ops", "owner"})
_DETERMINERS = frozenset({"a", "an", "the", "my", "our", "your", "their", "some", "any"})
_INDEFINITE = frozenset({"someone", "somebody", "anyone", "anybody", "everyone", "nobody"})
_NAME_WORD = re.compile(r"[^\W_]+")


def is_placeholder_name(name: str) -> bool:
    """True when a request's "name" is a role or placeholder, not a person.

    Deliberately small: an exact role phrase ("reviewer", "the team", "QA",
    "my lead", "a dev"), a group ("payments team"), or an indefinite pronoun
    leading the phrase ("someone from QA"). Anything else is treated as a name.
    """
    words = _NAME_WORD.findall(name.casefold())
    determined = False
    while words and words[0] in _DETERMINERS:
        words = words[1:]
        determined = True
    if not words:
        return determined
    if words[0] in _INDEFINITE or (len(words) > 1 and words[-1] == "team"):
        return True
    phrase = " ".join(words)
    return phrase in _PLACEHOLDER_NAMES or (determined and phrase in _PLACEHOLDER_AFTER_DETERMINER)
