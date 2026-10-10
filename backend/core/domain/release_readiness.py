"""Release readiness: what a release, project or pod needs before production beyond its gates.

A gate guards a stage per requirement, with items written in that requirement's
text. A release criterion belongs to a scope instead (each release, or the
project while it has none; each project; each pod), and its evidence is a
separate Jira issue (a security review, a load test, a runbook) or a record
outside Jira. A tenant keeps its own criteria as data; none is on until an
admin adds one.

Evidence is found by rules over the issues OpenProgram already syncs: a label,
an issue type, a phrase in the title, the title's words, or the epic. A rule
either counts as evidence (the criterion is covered) or is only a candidate
(the criterion is unsure). A person's decision wins over every rule: an issue
or record they link covers it, and "not applicable" takes it out. A missing
criterion gets a drafted Jira issue that a person may create, edit or dismiss;
nothing here writes anywhere.

Everything is about scopes and work, never about people: no reason, draft or
line says who should have done something.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum

from core.domain.auth import Role
from core.domain.delivery import STAGE_LABELS, STAGE_ORDER, DeliveryStage
from core.domain.errors import OpenProgramError

MAX_NAME = 80
MAX_EVIDENCE = 600
MAX_MATCHERS = 12
MAX_MATCHER_VALUE = 80
MAX_WHEN = 10
MAX_LEAD_DAYS = 60
DEFAULT_LEAD_DAYS = 10
MAX_CRITERIA = 30
MAX_SUMMARY = 255
MAX_DESCRIPTION = 4000
MAX_LABELS = 10
MAX_LABEL = 255
MAX_ISSUE_TYPE = 60
MIN_REASON = 3
MAX_REASON = 300
MAX_NOTE = 300
MAX_URL = 2000
#: A finding is due soon this many working days before its due day, or its lead if longer.
MIN_DUE_SOON_DAYS = 5
#: Every issue OpenProgram creates carries one, so a retry finds what an earlier try made.
MARKER_PREFIX = "op-rr-"
DEFAULT_LABEL = "release-readiness"
DEFAULT_ISSUE_TYPE = "Task"
DEFAULT_SUMMARY = "{criterion} for {scope}"
#: Who marks a blocking criterion not applicable; an admin always may.
WAIVE_ROLES = frozenset({Role.MGR})
#: The only words a draft template may fill in. No Jira text is ever one of them.
PLACEHOLDERS = (
    "scope",
    "scope_kind",
    "project",
    "project_key",
    "release",
    "pod",
    "criterion",
    "evidence",
    "stage",
    "due_date",
    "delivery_date",
    "working_days",
    "console_link",
    "creator",
)

_PLACEHOLDER = re.compile(r"\{([A-Za-z_]+)\}")
_NON_WORD = re.compile(r"[^\w]+")
_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]+$")
_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
_SLUG = re.compile(r"[^a-z0-9]+")


class ReadinessError(OpenProgramError):
    """A criterion, draft, setting or decision that cannot be saved."""


class ScopeKind(StrEnum):
    PROJECT = "project"
    RELEASE = "release"
    POD = "pod"


class AppliesTo(StrEnum):
    #: Each release of a project, or the project itself while it has none.
    RELEASE = "release"
    PROJECT = "project"
    POD = "pod"


class Severity(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


class MatcherKind(StrEnum):
    LABEL = "label"
    ISSUE_TYPE = "issue_type"
    TITLE_PHRASE = "title_phrase"
    TITLE_WORDS = "title_words"
    EPIC = "epic"


class Strength(StrEnum):
    #: A match makes the criterion covered.
    EVIDENCE = "evidence"
    #: A match only makes it unsure: a person decides.
    CANDIDATE = "candidate"


class FindingState(StrEnum):
    COVERED = "covered"
    MISSING = "missing"
    UNSURE = "unsure"


class DecidedBy(StrEnum):
    RULES = "rules"
    MODEL = "model"
    PERSON = "person"


class DecisionKind(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    LINKED = "linked"


class UrgencyKind(StrEnum):
    LATER = "later"
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"
    #: A requirement of the scope is at or past the stage while the criterion is not ready.
    STAGE_REACHED = "stage_reached"
    NO_DATE = "no_date"


#: The kinds a day report raises: the date is close, gone, or the stage already reached.
URGENT = frozenset({UrgencyKind.DUE_SOON, UrgencyKind.OVERDUE, UrgencyKind.STAGE_REACHED})


class SuggestionStatus(StrEnum):
    OPEN = "open"
    #: Claimed by one Create press; nobody else may create it meanwhile.
    CREATING = "creating"
    CREATED = "created"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"


class RunTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    SCOPE_CHANGE = "scope_change"


class RunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    OFF = "off"


# ---- configuration ----------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Matcher:
    kind: MatcherKind
    value: str
    strength: Strength


@dataclass(frozen=True, kw_only=True)
class DraftTemplate:
    """How a missing criterion's Jira issue is drafted. Empty fields take the defaults."""

    #: A fixed Jira project key; empty drafts into the scope's own project.
    project_key: str = ""
    #: Empty takes the tenant's issue type.
    issue_type: str = ""
    summary: str = DEFAULT_SUMMARY
    #: Empty writes OpenProgram's own text.
    description: str = ""
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ReleaseCriterion:
    tenant_id: str
    criterion_id: str
    name: str
    #: What counts as evidence, in words.
    evidence: str
    applies_to: AppliesTo
    matchers: tuple[Matcher, ...]
    required_before: DeliveryStage = DeliveryStage.PRODUCTION
    lead_working_days: int = DEFAULT_LEAD_DAYS
    severity: Severity = Severity.BLOCKING
    #: Ready only once an evidence issue is done; off, an issue that exists is enough.
    needs_done: bool = True
    #: It applies only to a scope holding an issue with one of these labels or types.
    when_labels: tuple[str, ...] = ()
    when_types: tuple[str, ...] = ()
    draft: DraftTemplate = field(default_factory=DraftTemplate)
    enabled: bool = True
    #: +1 on every save; findings record the version they were judged on.
    version: int = 1
    updated_at: datetime | None = None
    updated_by: str | None = None
    deleted_at: datetime | None = None

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.BLOCKING


@dataclass(frozen=True, kw_only=True)
class ReadinessSettings:
    tenant_id: str
    #: Off by default: a tick does nothing and Run check now is refused.
    enabled: bool = False
    #: Draft an issue for every missing criterion.
    auto_suggest: bool = True
    #: Lets a person press Create in Jira; the tenant's write-back switch is needed too.
    create_in_jira: bool = False
    issue_type: str = DEFAULT_ISSUE_TYPE
    labels: tuple[str, ...] = (DEFAULT_LABEL,)
    updated_at: datetime | None = None
    updated_by: str | None = None


# ---- scopes and their issues ------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ScopeRef:
    kind: ScopeKind
    id: str

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.id}"


@dataclass(frozen=True, kw_only=True)
class ScopeIssue:
    """One synced issue a scope owns, as the rules read it."""

    key: str
    title: str
    issue_type: str | None = None
    labels: tuple[str, ...] = ()
    parent_key: str | None = None
    parent_title: str | None = None
    status: str | None = None
    done: bool = False
    #: False in a stage marked "not counted" (won't do, duplicate): never evidence.
    counted: bool = True
    updated_at: str | None = None


# ---- findings ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EvidenceRef:
    issue_key: str = ""
    title: str = ""
    status: str = ""
    done: bool = False
    #: How it was found: "label security-review", 'says "load test"', "linked", "created".
    how: str = ""
    #: A record outside Jira, linked by a person.
    url: str = ""
    note: str = ""


@dataclass(frozen=True, kw_only=True)
class CandidateRef:
    issue_key: str
    title: str
    #: Which weaker sign matched: a matcher kind.
    why: MatcherKind


@dataclass(frozen=True, kw_only=True)
class PersonDecision:
    kind: DecisionKind
    by: str
    at: datetime
    reason: str = ""
    issue_key: str = ""
    url: str = ""
    note: str = ""
    #: The linked issue was created from this finding's draft.
    created: bool = False


@dataclass(frozen=True, kw_only=True)
class Evaluation:
    state: FindingState
    done: bool
    decided_by: DecidedBy
    evidence: tuple[EvidenceRef, ...] = ()
    candidates: tuple[CandidateRef, ...] = ()
    #: Fixed words, never Jira text.
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class Urgency:
    kind: UrgencyKind
    #: The day the criterion is needed by: the scope's date less its lead.
    due_on: date | None = None
    delivery_date: date | None = None
    #: The requirement that reached the stage, for STAGE_REACHED.
    stage_key: str | None = None


@dataclass(frozen=True, kw_only=True)
class Finding:
    """One criterion against one scope, as the last run saw it."""

    tenant_id: str
    finding_id: str
    criterion_id: str
    criterion_version: int
    scope: ScopeRef
    #: The project a project or release scope belongs to; None for a pod.
    project_id: str | None
    state: FindingState
    done: bool
    decided_by: DecidedBy
    evidence: tuple[EvidenceRef, ...]
    candidates: tuple[CandidateRef, ...]
    reason: str
    urgency: Urgency
    #: Based on stale Jira data: shown, but kept out of the day report until fresh.
    held: bool
    #: False while the criterion's "only where" condition does not hold for the scope.
    applies: bool
    person: PersonDecision | None
    fingerprint: str
    first_seen_at: datetime
    #: When it became due soon or worse; an ask's wait counts from here.
    window_entered_at: datetime | None
    last_run_id: str | None
    updated_at: datetime

    @property
    def not_applicable(self) -> bool:
        return self.person is not None and self.person.kind is DecisionKind.NOT_APPLICABLE


@dataclass(frozen=True, kw_only=True)
class Draft:
    project_key: str
    issue_type: str
    summary: str
    description: str
    labels: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class Suggestion:
    """The drafted Jira issue for one finding, for the finding's whole life."""

    tenant_id: str
    suggestion_id: str
    finding_id: str
    status: SuggestionStatus
    version: int
    draft: Draft
    marker_label: str
    criterion_version: int
    updated_at: datetime
    edited_by: str | None = None
    created_issue_key: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    dismissed_by: str | None = None
    dismissed_reason: str | None = None
    dismissed_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class ReadinessAction:
    """One row of the append-only audit: a person's action or the agent's state change."""

    tenant_id: str
    action_id: str
    at: datetime
    #: A member id, or "agent".
    actor: str
    action: str
    finding_id: str | None = None
    suggestion_id: str | None = None
    criterion_id: str | None = None
    before: Mapping[str, object] | None = None
    after: Mapping[str, object] | None = None
    reason: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReadinessRun:
    tenant_id: str
    run_id: str
    #: The tick's time, or manual:<scope>:<minute>; one run per slot.
    slot: str
    trigger: RunTrigger
    status: RunStatus
    started_at: datetime
    scope: ScopeRef | None = None
    finished_at: datetime | None = None
    counts: Mapping[str, int] = field(default_factory=dict)
    #: The Jira sync the run read.
    data_as_of: datetime | None = None
    error_category: str | None = None
    actor: str | None = None


# ---- the rules --------------------------------------------------------------------------


def fold(text: str) -> str:
    """Case and punctuation folded: "Data-protection  Impact" -> "data protection impact"."""
    return " ".join(_NON_WORD.sub(" ", text.casefold()).replace("_", " ").split())


def _has_phrase(phrase: str, text: str | None) -> bool:
    folded = fold(phrase)
    return bool(folded) and text is not None and f" {folded} " in f" {fold(text)} "


def match(matcher: Matcher, issue: ScopeIssue) -> str | None:
    """How the issue matches, in words ("label security-review"), or None."""
    value = matcher.value
    wanted = value.casefold()
    if matcher.kind is MatcherKind.LABEL:
        return f"label {value}" if any(lab.casefold() == wanted for lab in issue.labels) else None
    if matcher.kind is MatcherKind.ISSUE_TYPE:
        found = issue.issue_type is not None and issue.issue_type.casefold() == wanted
        return f"type {issue.issue_type}" if found else None
    if matcher.kind is MatcherKind.TITLE_PHRASE:
        return f'says "{value}"' if _has_phrase(value, issue.title) else None
    if matcher.kind is MatcherKind.TITLE_WORDS:
        words = set(fold(value).split())
        return f"title words {value}" if words and words <= set(fold(issue.title).split()) else None
    if issue.parent_key and issue.parent_key.casefold() == wanted:
        return f"epic {issue.parent_key}"
    if issue.parent_key and _has_phrase(value, issue.parent_title):
        return f"epic {issue.parent_key}"
    return None


def applies_to_scope(criterion: ReleaseCriterion, issues: Sequence[ScopeIssue]) -> bool:
    """Whether the criterion's "only where an issue has" condition holds; none always does."""
    if not criterion.when_labels and not criterion.when_types:
        return True
    labels = {label.casefold() for label in criterion.when_labels}
    types = {name.casefold() for name in criterion.when_types}
    return any(
        issue.counted
        and (
            any(label.casefold() in labels for label in issue.labels)
            or (issue.issue_type is not None and issue.issue_type.casefold() in types)
        )
        for issue in issues
    )


def evaluate_criterion(
    criterion: ReleaseCriterion,
    issues: Sequence[ScopeIssue],
    *,
    scope_name: str,
    person: PersonDecision | None = None,
    known: Mapping[str, ScopeIssue] | None = None,
) -> Evaluation:
    """Where one scope stands against one criterion.

    In order: a person's link wins; then any evidence-strength match covers it
    (every match listed, done when any is); then candidate matches leave it
    unsure; else it is missing. "Not applicable" keeps the rules' state for
    when it is reopened, decided by the person.
    """
    if person is not None and person.kind is DecisionKind.LINKED:
        return _linked(person, known or {})
    evidence: list[EvidenceRef] = []
    candidates: list[CandidateRef] = []
    for issue in sorted(issues, key=lambda item: _natural(item.key)):
        if not issue.counted:
            continue
        strong = [
            how
            for matcher in criterion.matchers
            if matcher.strength is Strength.EVIDENCE and (how := match(matcher, issue))
        ]
        if strong:
            evidence.append(
                EvidenceRef(
                    issue_key=issue.key,
                    title=issue.title,
                    status=issue.status or "",
                    done=issue.done,
                    how=strong[0],
                )
            )
            continue
        weak = [
            matcher.kind
            for matcher in criterion.matchers
            if matcher.strength is Strength.CANDIDATE and match(matcher, issue)
        ]
        if weak:
            candidates.append(CandidateRef(issue_key=issue.key, title=issue.title, why=weak[0]))
    decided = DecidedBy.PERSON if person is not None else DecidedBy.RULES
    if evidence:
        return Evaluation(
            state=FindingState.COVERED,
            done=any(item.done for item in evidence),
            decided_by=decided,
            evidence=tuple(evidence),
        )
    if candidates:
        return Evaluation(
            state=FindingState.UNSURE,
            done=False,
            decided_by=decided,
            candidates=tuple(candidates),
            reason=unsure_reason(candidates),
        )
    return Evaluation(
        state=FindingState.MISSING,
        done=False,
        decided_by=decided,
        reason=missing_reason(criterion, scope_name),
    )


def _linked(person: PersonDecision, known: Mapping[str, ScopeIssue]) -> Evaluation:
    how = "created" if person.created else "linked"
    if person.url:
        return Evaluation(
            state=FindingState.COVERED,
            done=True,
            decided_by=DecidedBy.PERSON,
            evidence=(EvidenceRef(url=person.url, note=person.note, done=True, how=how),),
        )
    issue = known.get(person.issue_key)
    if issue is None:
        return Evaluation(
            state=FindingState.UNSURE,
            done=False,
            decided_by=DecidedBy.PERSON,
            reason=f"{person.issue_key} is no longer in Jira's synced issues.",
        )
    if not issue.counted:
        closed = f" as {issue.status}" if issue.status else ""
        return Evaluation(
            state=FindingState.MISSING,
            done=False,
            decided_by=DecidedBy.PERSON,
            reason=f"{issue.key} was closed{closed}.",
        )
    return Evaluation(
        state=FindingState.COVERED,
        done=issue.done,
        decided_by=DecidedBy.PERSON,
        evidence=(
            EvidenceRef(
                issue_key=issue.key,
                title=issue.title,
                status=issue.status or "",
                done=issue.done,
                how=how,
            ),
        ),
    )


_SIGN_WORDS = {
    MatcherKind.LABEL: "is labelled {}",
    MatcherKind.ISSUE_TYPE: "is a {}",
    MatcherKind.TITLE_PHRASE: 'says "{}"',
    MatcherKind.TITLE_WORDS: "has the words {}",
    MatcherKind.EPIC: "sits under {}",
}
_WEAK_WORDS = {
    MatcherKind.LABEL: "a label",
    MatcherKind.ISSUE_TYPE: "the issue type",
    MatcherKind.TITLE_PHRASE: "a phrase in the title",
    MatcherKind.TITLE_WORDS: "the title's words",
    MatcherKind.EPIC: "the epic",
}


def missing_reason(criterion: ReleaseCriterion, scope_name: str) -> str:
    """'No issue in Release 1 is labelled security-review or says "security review".'"""
    strong = [m for m in criterion.matchers if m.strength is Strength.EVIDENCE]
    signs = [_SIGN_WORDS[m.kind].format(m.value) for m in (strong or criterion.matchers)]
    return f"No issue in {scope_name} {_joined_or(signs)}."


def unsure_reason(candidates: Sequence[CandidateRef]) -> str:
    """'Only the title's words match: CHK-13.'"""
    kinds = list(dict.fromkeys(candidate.why for candidate in candidates))
    keys = ", ".join(candidate.issue_key for candidate in candidates[:5])
    more = f" and {len(candidates) - 5} more" if len(candidates) > 5 else ""
    return f"Only {_joined_or([_WEAK_WORDS[kind] for kind in kinds])} match: {keys}{more}."


def is_ready(
    criterion: ReleaseCriterion, evaluation: Evaluation, person: PersonDecision | None
) -> bool:
    if person is not None and person.kind is DecisionKind.NOT_APPLICABLE:
        return True
    if evaluation.state is not FindingState.COVERED:
        return False
    return evaluation.done or not criterion.needs_done


def urgency_of(
    criterion: ReleaseCriterion,
    *,
    ready: bool,
    target: date | None,
    stages: Sequence[tuple[str, DeliveryStage]],
    today: date,
) -> Urgency:
    """How close the scope is to needing the criterion.

    Reaching the stage itself is a breach, with or without a date. Without a
    date, a requirement waiting in the stage just before makes it due soon.
    Otherwise it is due soon within its lead (at least five working days) of
    the due day, the scope's date less the lead.
    """
    due_on = minus_working_days(target, criterion.lead_working_days) if target else None
    if ready:
        return Urgency(kind=UrgencyKind.LATER, due_on=due_on, delivery_date=target)
    guarded = STAGE_ORDER.index(criterion.required_before)
    reached = sorted(
        (key for key, stage in stages if STAGE_ORDER.index(stage) >= guarded), key=_natural
    )
    if reached:
        return Urgency(
            kind=UrgencyKind.STAGE_REACHED,
            due_on=due_on,
            delivery_date=target,
            stage_key=reached[0],
        )
    waiting = any(STAGE_ORDER.index(stage) == guarded - 1 for _key, stage in stages)
    if due_on is None:
        return Urgency(kind=UrgencyKind.DUE_SOON if waiting else UrgencyKind.NO_DATE)
    if today > due_on:
        return Urgency(kind=UrgencyKind.OVERDUE, due_on=due_on, delivery_date=target)
    window = max(criterion.lead_working_days, MIN_DUE_SOON_DAYS)
    soon = waiting or working_days_left(today, due_on) <= window
    return Urgency(
        kind=UrgencyKind.DUE_SOON if soon else UrgencyKind.LATER,
        due_on=due_on,
        delivery_date=target,
    )


def minus_working_days(day: date, days: int) -> date:
    current = day
    counted = 0
    while counted < days:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            counted += 1
    return current


def working_days_left(today: date, due_on: date) -> int:
    """Working days after today up to the due day; negative once it has passed."""
    if due_on >= today:
        return sum(
            1
            for offset in range(1, (due_on - today).days + 1)
            if (today + timedelta(days=offset)).weekday() < 5
        )
    return -sum(
        1
        for offset in range(1, (today - due_on).days + 1)
        if (due_on + timedelta(days=offset)).weekday() < 5
    )


def finding_fingerprint(
    *,
    criterion_version: int,
    scope: ScopeRef,
    evaluation: Evaluation,
    urgency: Urgency,
    person: PersonDecision | None,
    applies: bool,
    held: bool,
    updated: Mapping[str, str | None],
) -> str:
    """One finding's identity across runs: unchanged means nothing is written or audited."""
    parts = {
        "version": criterion_version,
        "scope": scope.key,
        "state": evaluation.state.value,
        "done": evaluation.done,
        "decided_by": evaluation.decided_by.value,
        "evidence": sorted(
            [item.issue_key, item.status, item.done, item.url, updated.get(item.issue_key)]
            for item in evaluation.evidence
        ),
        "candidates": sorted(
            [item.issue_key, item.why.value, updated.get(item.issue_key)]
            for item in evaluation.candidates
        ),
        "reason": evaluation.reason,
        "urgency": [
            urgency.kind.value,
            urgency.due_on.isoformat() if urgency.due_on else None,
            urgency.stage_key,
        ],
        "person": (
            [person.kind.value, person.issue_key, person.url, person.at.isoformat()]
            if person is not None
            else None
        ),
        "applies": applies,
        "held": held,
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def marker_label(suggestion_id: str) -> str:
    digest = hashlib.sha256(suggestion_id.encode()).hexdigest()[:8]
    return f"{MARKER_PREFIX}{digest}"


# ---- drafts -----------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class DraftContext:
    """What a draft may say about its scope: names and dates OpenProgram holds, no Jira text."""

    scope_kind: ScopeKind
    scope_name: str
    project_name: str = ""
    project_key: str = ""
    release_name: str = ""
    pod_name: str = ""
    due_on: date | None = None
    delivery_date: date | None = None
    console_link: str = ""


def scope_phrase(context: DraftContext) -> str:
    """'Release 1 of Checkout Revamp', 'Checkout Revamp', 'Payments Pod'."""
    if context.scope_kind is ScopeKind.RELEASE and context.project_name:
        return f"{context.release_name or context.scope_name} of {context.project_name}"
    return context.scope_name


def render_draft(
    criterion: ReleaseCriterion, settings: ReadinessSettings, context: DraftContext
) -> Draft:
    values = _values(criterion, context)
    summary = _render(criterion.draft.summary or DEFAULT_SUMMARY, values)
    description = (
        _render(criterion.draft.description, values)
        if criterion.draft.description.strip()
        else default_description(criterion, context)
    )
    labels = _clean_labels((*criterion.draft.labels, *settings.labels))[:MAX_LABELS]
    return Draft(
        project_key=criterion.draft.project_key or context.project_key,
        issue_type=criterion.draft.issue_type or settings.issue_type or DEFAULT_ISSUE_TYPE,
        summary=" ".join(summary.split())[:MAX_SUMMARY],
        description=description[:MAX_DESCRIPTION],
        labels=labels,
    )


def default_description(criterion: ReleaseCriterion, context: DraftContext) -> str:
    scope = context.scope_name
    stage = STAGE_LABELS[criterion.required_before].lower()
    lines = [
        f"{scope_phrase(context)} needs {with_article(criterion.name)} before {stage}. "
        f"No Jira issue in {scope} tracks one yet.",
        f"What counts as done: {_sentence(criterion.evidence)}",
    ]
    if context.due_on is not None and context.delivery_date is not None:
        lead = criterion.lead_working_days
        lines.append(
            f"Needed by {_day(context.due_on)}, {lead} working "
            f"{'day' if lead == 1 else 'days'} before the delivery date of "
            f"{_day(context.delivery_date)}."
        )
    return "\n\n".join(lines)


def created_footer(creator: str, console_link: str) -> str:
    """Said under every issue OpenProgram creates: who approved it, and the way back."""
    line = (
        "Drafted by OpenProgram's release readiness check. "
        f"Approved and created in OpenProgram by {creator}."
    )
    return f"{line}\n{console_link}" if console_link else line


def _values(criterion: ReleaseCriterion, context: DraftContext) -> dict[str, str]:
    lead = criterion.lead_working_days
    return {
        "scope": scope_phrase(context),
        "scope_kind": context.scope_kind.value,
        "project": context.project_name,
        "project_key": context.project_key,
        "release": context.release_name,
        "pod": context.pod_name,
        "criterion": criterion.name,
        "evidence": criterion.evidence,
        "stage": STAGE_LABELS[criterion.required_before].lower(),
        "due_date": _day(context.due_on) if context.due_on else "no date set",
        "delivery_date": _day(context.delivery_date) if context.delivery_date else "no date set",
        "working_days": str(lead),
        "console_link": context.console_link,
        # Filled when a person creates the issue; a draft says who may.
        "creator": "the person who creates it",
    }


def _render(template: str, values: Mapping[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def with_article(name: str) -> str:
    """'a security review', 'an accessibility check', 'a DPIA': the name, in a sentence."""
    words = name_in_sentence(name)
    return f"{'an' if words[:1].casefold() in 'aeiou' else 'a'} {words}"


def name_in_sentence(name: str) -> str:
    """The name lower-cased to sit in a sentence, unless it starts with an acronym."""
    clean = " ".join(name.split())
    if len(clean) > 1 and clean[1].isupper():
        return clean
    return clean[:1].lower() + clean[1:]


# ---- validation -------------------------------------------------------------------------


def validated_criterion(criterion: ReleaseCriterion) -> ReleaseCriterion:
    """The criterion tidied, or ReadinessError naming every problem in one sentence."""
    problems: list[str] = []
    name = " ".join(criterion.name.split())
    if not name or len(name) > MAX_NAME:
        problems.append(f"a criterion needs a name of 1 to {MAX_NAME} characters")
    evidence = " ".join(criterion.evidence.split())
    if not evidence or len(evidence) > MAX_EVIDENCE:
        problems.append(f"say what counts as evidence in 1 to {MAX_EVIDENCE} characters")
    if not 0 <= criterion.lead_working_days <= MAX_LEAD_DAYS:
        problems.append(f"the lead is 0 to {MAX_LEAD_DAYS} working days")
    matchers = _matchers(criterion.matchers, problems)
    when_labels = _clean_labels(criterion.when_labels)
    when_types = _clean(criterion.when_types)
    if len(when_labels) > MAX_WHEN or len(when_types) > MAX_WHEN:
        problems.append(f"name at most {MAX_WHEN} labels and {MAX_WHEN} types to apply to")
    if any(not _label_ok(label) for label in criterion.when_labels if label.strip()):
        problems.append("a label has no spaces")
    draft = _draft_template(criterion.draft, problems)
    if problems:
        raise ReadinessError(_sentence_of(problems))
    return replace(
        criterion,
        name=name,
        evidence=evidence,
        matchers=matchers,
        when_labels=when_labels,
        when_types=when_types,
        draft=draft,
    )


def _matchers(matchers: Sequence[Matcher], problems: list[str]) -> tuple[Matcher, ...]:
    if not 1 <= len(matchers) <= MAX_MATCHERS:
        problems.append(f"a criterion needs 1 to {MAX_MATCHERS} ways to find its evidence")
    kept: list[Matcher] = []
    seen: set[tuple[MatcherKind, str]] = set()
    for matcher in matchers:
        value = " ".join(matcher.value.split())
        if not value or len(value) > MAX_MATCHER_VALUE:
            problems.append(f"each way to find evidence needs a value of 1 to {MAX_MATCHER_VALUE}")
            continue
        if matcher.kind is MatcherKind.LABEL and not _label_ok(value):
            problems.append(f"the label {value!r} has a space; Jira labels have none")
            continue
        if matcher.kind in {MatcherKind.TITLE_PHRASE, MatcherKind.TITLE_WORDS} and not fold(value):
            problems.append(f"{value!r} has no words to look for")
            continue
        if (matcher.kind, value.casefold()) in seen:
            continue
        seen.add((matcher.kind, value.casefold()))
        kept.append(replace(matcher, value=value))
    return tuple(kept)


def _draft_template(draft: DraftTemplate, problems: list[str]) -> DraftTemplate:
    summary = " ".join(draft.summary.split()) or DEFAULT_SUMMARY
    for label, text in (("summary", summary), ("text", draft.description)):
        unknown = sorted({name for name in _PLACEHOLDER.findall(text) if name not in PLACEHOLDERS})
        if unknown:
            problems.append(
                f"the draft's {label} uses {', '.join('{' + n + '}' for n in unknown)}, which "
                "OpenProgram does not fill in"
            )
    longest = _render(summary, {name: "x" * MAX_NAME for name in PLACEHOLDERS})
    if len(longest) > MAX_SUMMARY:
        problems.append(
            f"the draft's summary can grow past {MAX_SUMMARY} characters once filled in"
        )
    if len(draft.description) > MAX_DESCRIPTION:
        problems.append(f"the draft's text is at most {MAX_DESCRIPTION} characters")
    project_key = draft.project_key.strip().upper()
    if project_key and not _PROJECT_KEY.match(project_key):
        problems.append("a Jira project key is capital letters and digits, such as CHK")
    issue_type = " ".join(draft.issue_type.split())
    if len(issue_type) > MAX_ISSUE_TYPE:
        problems.append(f"an issue type is at most {MAX_ISSUE_TYPE} characters")
    labels = _clean_labels(draft.labels)
    if len(labels) > MAX_LABELS or any(not _label_ok(label) for label in labels):
        problems.append(f"a draft has at most {MAX_LABELS} labels, each without spaces")
    return DraftTemplate(
        project_key=project_key,
        issue_type=issue_type,
        summary=summary,
        description=draft.description.strip(),
        labels=labels,
    )


def validated_draft(draft: Draft) -> Draft:
    """A person's edit of a draft, tidied, or ReadinessError."""
    problems: list[str] = []
    summary = " ".join(draft.summary.split())
    if not summary or len(summary) > MAX_SUMMARY:
        problems.append(f"a summary is 1 to {MAX_SUMMARY} characters")
    if len(draft.description) > MAX_DESCRIPTION:
        problems.append(f"the text is at most {MAX_DESCRIPTION} characters")
    labels = _clean_labels(draft.labels)
    if len(labels) > MAX_LABELS:
        problems.append(f"a draft has at most {MAX_LABELS} labels")
    if any(not _label_ok(label) for label in labels):
        problems.append("a Jira label has no spaces")
    project_key = draft.project_key.strip().upper()
    if not _PROJECT_KEY.match(project_key):
        problems.append("the Jira project key is capital letters and digits, such as CHK")
    issue_type = " ".join(draft.issue_type.split())
    if not issue_type or len(issue_type) > MAX_ISSUE_TYPE:
        problems.append(f"an issue type is 1 to {MAX_ISSUE_TYPE} characters")
    if problems:
        raise ReadinessError(_sentence_of(problems))
    return Draft(
        project_key=project_key,
        issue_type=issue_type,
        summary=summary,
        description=draft.description.strip(),
        labels=labels,
    )


def validated_settings(settings: ReadinessSettings) -> ReadinessSettings:
    problems: list[str] = []
    issue_type = " ".join(settings.issue_type.split())
    if not issue_type or len(issue_type) > MAX_ISSUE_TYPE:
        problems.append(f"the issue type is 1 to {MAX_ISSUE_TYPE} characters")
    labels = _clean_labels(settings.labels)
    if len(labels) > MAX_LABELS or any(not _label_ok(label) for label in labels):
        problems.append(f"new issues get at most {MAX_LABELS} labels, each without spaces")
    if problems:
        raise ReadinessError(_sentence_of(problems))
    return replace(settings, issue_type=issue_type, labels=labels)


def validated_reason(reason: str) -> str:
    clean = " ".join(reason.split())
    if not MIN_REASON <= len(clean) <= MAX_REASON:
        raise ReadinessError(f"Give a reason of {MIN_REASON} to {MAX_REASON} characters.")
    return clean


def validated_issue_key(key: str) -> str:
    clean = key.strip().upper()
    if not _ISSUE_KEY.match(clean):
        raise ReadinessError("An issue key looks like CHK-12.")
    return clean


def validated_record(url: str, note: str) -> tuple[str, str]:
    clean = url.strip()
    if not clean.startswith("https://") or len(clean) > MAX_URL or " " in clean:
        raise ReadinessError("A record outside Jira is a link starting with https://.")
    return clean, " ".join(note.split())[:MAX_NOTE]


def criterion_slug(name: str, taken: Iterable[str]) -> str:
    base = _SLUG.sub("-", fold(name)).strip("-")[:60] or "criterion"
    used = set(taken)
    candidate, number = base, 2
    while candidate in used:
        candidate = f"{base}-{number}"
        number += 1
    return candidate


# ---- the examples a tenant can add ------------------------------------------------------


def default_examples(tenant_id: str) -> tuple[ReleaseCriterion, ...]:
    """Six generic criteria an admin may add. None is active until one is added."""
    evidence, candidate = Strength.EVIDENCE, Strength.CANDIDATE

    def m(kind: MatcherKind, value: str, strength: Strength = evidence) -> Matcher:
        return Matcher(kind=kind, value=value, strength=strength)

    def example(
        criterion_id: str,
        name: str,
        what: str,
        applies_to: AppliesTo,
        matchers: tuple[Matcher, ...],
        labels: tuple[str, ...],
        *,
        severity: Severity = Severity.BLOCKING,
        when_labels: tuple[str, ...] = (),
    ) -> ReleaseCriterion:
        return ReleaseCriterion(
            tenant_id=tenant_id,
            criterion_id=criterion_id,
            name=name,
            evidence=what,
            applies_to=applies_to,
            matchers=matchers,
            severity=severity,
            when_labels=when_labels,
            draft=DraftTemplate(labels=labels),
        )

    return (
        example(
            "security-review",
            "Security review",
            "A security review of the release's changes, with its findings closed or accepted.",
            AppliesTo.RELEASE,
            (
                m(MatcherKind.LABEL, "security-review"),
                m(MatcherKind.TITLE_PHRASE, "security review"),
            ),
            ("security-review",),
        ),
        example(
            "load-test",
            "Load test",
            "A load test at the expected peak, with its results accepted.",
            AppliesTo.RELEASE,
            (
                m(MatcherKind.LABEL, "load-test"),
                m(MatcherKind.TITLE_PHRASE, "load test"),
                m(MatcherKind.TITLE_WORDS, "performance test", candidate),
            ),
            ("load-test",),
        ),
        example(
            "runbook-handover",
            "Runbook and handover",
            "A runbook for operating the solution, handed over to the team that runs it.",
            AppliesTo.PROJECT,
            (m(MatcherKind.TITLE_PHRASE, "runbook"), m(MatcherKind.TITLE_PHRASE, "handover")),
            ("runbook",),
        ),
        example(
            "data-protection-impact-assessment",
            "Data-protection impact assessment",
            "An assessment of how the solution handles personal data, signed off.",
            AppliesTo.PROJECT,
            (
                m(MatcherKind.TITLE_PHRASE, "DPIA"),
                m(MatcherKind.TITLE_PHRASE, "data protection impact"),
            ),
            ("dpia",),
            when_labels=("personal-data",),
        ),
        example(
            "accessibility-check",
            "Accessibility check",
            "An accessibility review of the release's user-facing changes.",
            AppliesTo.RELEASE,
            (
                m(MatcherKind.LABEL, "accessibility"),
                m(MatcherKind.TITLE_PHRASE, "accessibility review"),
                m(MatcherKind.TITLE_WORDS, "accessibility", candidate),
            ),
            ("accessibility",),
            severity=Severity.ADVISORY,
        ),
        example(
            "change-approval",
            "Change approval",
            "An approved change request for the production deployment.",
            AppliesTo.RELEASE,
            (
                m(MatcherKind.LABEL, "change-approval"),
                m(MatcherKind.TITLE_PHRASE, "change request"),
            ),
            ("change-approval",),
        ),
    )


# ---- words ------------------------------------------------------------------------------


def stage_words(stage: DeliveryStage) -> str:
    return STAGE_LABELS[stage].lower()


def _day(day: date) -> str:
    return f"{day.day} {day:%b}"


def _sentence(text: str) -> str:
    clean = " ".join(text.split())
    if not clean:
        return clean
    clean = clean[0].upper() + clean[1:]
    return clean if clean[-1] in ".!?" else clean + "."


def _sentence_of(problems: Sequence[str]) -> str:
    text = "; ".join(problems)
    return text[0].upper() + text[1:] + "."


def _joined_or(parts: Sequence[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " or " + parts[-1]


def _label_ok(label: str) -> bool:
    return bool(label) and len(label) <= MAX_LABEL and not any(ch.isspace() for ch in label)


def _clean(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        clean = " ".join(value.split())
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            kept.append(clean)
    return tuple(kept)


def _clean_labels(values: Iterable[str]) -> tuple[str, ...]:
    return _clean(value.strip() for value in values)


def _natural(key: str) -> tuple[str, int, str]:
    head, _, tail = key.rpartition("-")
    return (head, int(tail), key) if tail.isdigit() else (key, 0, key)
