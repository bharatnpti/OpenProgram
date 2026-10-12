"""Jira hygiene: per-issue rules a tenant keeps, and the issues that break them.

A rule reads one scope's issues (each project, release or pod, the sets
release readiness builds), narrows them with readiness's matchers and a stage
filter, and tests one condition on each issue: added after planning without a
change label, a missing label or field, no acceptance criteria found by the
gates' scan, no Jira change for some working days in a stage, past its due
date while open, or too big. Each issue that breaks it is a finding attached
to that issue, with an owner and a suggested fix.

A rule lists only, by default: its findings show on Overall and in the day
report's count line. Only a rule an admin switches to ask turns into asks, one
per owner per rule per scope, which climb the escalation matrix. None is on
until an admin adds one.

Findings are about issues, never people: nothing counts or ranks anyone, and
nothing keeps Jira text beyond keys, titles, field names and dates. The change
label is the tenant's own word; "CR" is only the generic default.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum

from core.domain.auth import Role
from core.domain.baseline import DEFAULT_CHANGE_LABEL
from core.domain.delivery import STAGE_LABELS, STAGE_ORDER, DeliveryStage
from core.domain.errors import OpenProgramError
from core.domain.release_readiness import (
    Matcher,
    MatcherKind,
    RunStatus,
    RunTrigger,
    ScopeIssue,
    ScopeKind,
    ScopeRef,
    Strength,
    fold,
    match,
    working_days_left,
)

MAX_NAME = 80
MAX_SELECT = 8
MAX_MATCHER_VALUE = 80
MAX_LABELS = 10
MAX_LABEL = 255
MAX_FIX = 300
MAX_RULES = 30
MAX_WORKING_DAYS = 60
MAX_POINTS = 1000.0
MIN_REASON = 3
MAX_REASON = 300
DEFAULT_STALE_WORKING_DAYS = 5
#: How many keys an ask names before "and N more".
MAX_ASK_KEYS = 5
#: Who marks a finding of a blocking rule not applicable; an admin always may.
WAIVE_ROLES = frozenset({Role.MGR})
#: The only words a suggested fix may fill in. Each is a key, a title, a stage,
#: a name OpenProgram holds, a field name, a number or a date.
FIX_PLACEHOLDERS = ("key", "title", "stage", "release", "label", "field", "days", "baseline_date")

_PLACEHOLDER = re.compile(r"\{([A-Za-z_]+)\}")
_PROFILE_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_GATE_KIND = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SLUG = re.compile(r"[^a-z0-9]+")


class HygieneError(OpenProgramError):
    """A rule or setting that cannot be saved, or an action that cannot be taken."""


class RuleScope(StrEnum):
    """Which issue set a rule reads: readiness's scope builders."""

    PROJECT = "project"
    RELEASE = "release"
    POD = "pod"

    @property
    def kind(self) -> ScopeKind:
        return ScopeKind(self.value)


class StageFilter(StrEnum):
    ANY = "any"
    #: The issue sits in a stage before the named one.
    BEFORE = "before"
    #: The issue sits in the named stage or a later one.
    AT_OR_PAST = "at_or_past"


class ConditionKind(StrEnum):
    #: In scope, not in the planning baseline, joined after it and its grace,
    #: without a change label. Needs a baseline.
    ADDED_AFTER_BASELINE_WITHOUT_LABEL = "added_after_baseline_without_label"
    MISSING_LABEL = "missing_label"
    MISSING_FIELD = "missing_field"
    #: The gates' scan read the issue and found no item of the kind.
    MISSING_GATE_KIND = "missing_gate_kind"
    STALE_IN_STATUS = "stale_in_status"
    PAST_DUE_OPEN = "past_due_open"
    TOO_BIG = "too_big"


class IssueField(StrEnum):
    STORY_POINTS = "story_points"
    DUE_DATE = "due_date"
    PARENT = "parent"
    FIX_VERSION = "fix_version"
    PRIORITY = "priority"
    ASSIGNEE = "assignee"
    COMPONENTS = "components"
    #: The connection's severity field (Admin › Integrations, Jira).
    SEVERITY = "severity"


class RuleSeverity(StrEnum):
    #: Shown on Overall and counted in the day report; nobody is asked.
    LIST_ONLY = "list_only"
    #: One ask per owner per rule per scope, which climbs the escalation matrix.
    ASK = "ask"


class RuleNeed(StrEnum):
    FIX = "fix"
    DECISION = "decision"


class OwnerKind(StrEnum):
    ASSIGNEE = "assignee"
    REPORTER = "reporter"
    #: The holder of a profile in the issue's pod (``Owner.profile``).
    PROFILE = "profile"
    DECISION_OWNER = "decision_owner"
    POD_SCRUM_MASTER = "pod_scrum_master"


class HygieneState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    WAIVED = "waived"
    NOT_APPLICABLE = "not_applicable"


#: The states a person set; a run never reopens or closes them.
DECIDED = frozenset({HygieneState.WAIVED, HygieneState.NOT_APPLICABLE})


class Resolution(StrEnum):
    #: The breach is gone: the issue was fixed in Jira.
    FIXED = "fixed"
    #: The issue is no longer in the rule's scope.
    LEFT_SCOPE = "left_scope"


# ---- configuration ----------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Select:
    """Which of a scope's issues a rule looks at."""

    #: All must match (readiness's matchers; their strength is not read here).
    matchers: tuple[Matcher, ...] = ()
    stage_filter: StageFilter = StageFilter.ANY
    stage: DeliveryStage | None = None
    #: False looks only at the requirements the stage mapping counts.
    every_type: bool = False


@dataclass(frozen=True, kw_only=True)
class Condition:
    kind: ConditionKind
    #: added_after_baseline_without_label: the change labels (empty: the baseline's
    #: and the tenant's); missing_label: any of these.
    labels: tuple[str, ...] = ()
    field: IssueField | None = None
    #: A gate item kind's key, such as acceptance.
    gate_kind: str = ""
    #: stale_in_status: the stage the issue sits in.
    stage: DeliveryStage | None = None
    #: stale_in_status: days without a change; past_due_open: grace after the due day.
    working_days: int = 0
    points_over: float = 0.0


@dataclass(frozen=True, kw_only=True)
class Owner:
    kind: OwnerKind
    #: A profile catalog slug, for OwnerKind.PROFILE.
    profile: str = ""


@dataclass(frozen=True, kw_only=True)
class HygieneRule:
    tenant_id: str
    rule_id: str
    name: str
    scope: RuleScope
    condition: Condition
    select: Select = field(default_factory=Select)
    severity: RuleSeverity = RuleSeverity.LIST_ONLY
    need: RuleNeed = RuleNeed.FIX
    owner: Owner = field(default_factory=lambda: Owner(kind=OwnerKind.POD_SCRUM_MASTER))
    #: A breach is blocking once the issue reaches this stage (the readiness tie).
    blocking_before: DeliveryStage | None = None
    suggested_fix: str = ""
    enabled: bool = True
    #: +1 on every save; findings record the version they were judged on.
    version: int = 1
    updated_at: datetime | None = None
    updated_by: str | None = None
    deleted_at: datetime | None = None

    @property
    def asks(self) -> bool:
        return self.severity is RuleSeverity.ASK

    @property
    def blocking(self) -> bool:
        return self.blocking_before is not None


@dataclass(frozen=True, kw_only=True)
class HygieneSettings:
    tenant_id: str
    #: Off by default: a tick does nothing and Check now is refused.
    enabled: bool = False
    #: The tenant's change labels: what marks scope added after planning as agreed.
    change_labels: tuple[str, ...] = (DEFAULT_CHANGE_LABEL,)
    stale_working_days: int = DEFAULT_STALE_WORKING_DAYS
    updated_at: datetime | None = None
    updated_by: str | None = None


# ---- what a rule reads per issue ----------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BaselineFacts:
    """The planning baseline in force for the issue's scope."""

    planned_on: date
    keys: frozenset[str]
    change_label: str
    grace_working_days: int


@dataclass(frozen=True, kw_only=True)
class IssueContext:
    """What a rule needs about one issue beyond its synced fields. No Jira text."""

    today: date
    baseline: BaselineFacts | None = None
    #: The tenant's change labels, beside the baseline's own.
    change_labels: tuple[str, ...] = ()
    #: The day it joined the scope, from its history; None when not known.
    joined_on: date | None = None
    #: The day of its last Jira change, from its history or its update time.
    last_changed_on: date | None = None
    #: The gates' scan read it.
    gate_scanned: bool = False
    #: The gate item kinds suggested or confirmed on it.
    gate_kinds: frozenset[str] = frozenset()
    #: Each gate item kind's label, for the words.
    gate_kind_labels: Mapping[str, str] = field(default_factory=dict)
    #: The kinds a gate already asks for on it (it waits at that gate).
    gate_asks: frozenset[str] = frozenset()


Detail = Mapping[str, object]


@dataclass(frozen=True, kw_only=True)
class Breach:
    """What is wrong with one issue, in fixed words, with the keys and dates behind it."""

    what: str
    #: Field names, stages, labels, dates and numbers only.
    detail: Detail = field(default_factory=dict)
    #: The day an ask about it waits from; None: the day the finding was first seen.
    waits_from: date | None = None


# ---- findings ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PersonDecision:
    by: str
    at: datetime
    reason: str


@dataclass(frozen=True, kw_only=True)
class HygieneFinding:
    """One rule against one issue in one scope."""

    tenant_id: str
    finding_id: str
    rule_id: str
    rule_version: int
    issue_key: str
    scope: ScopeRef
    #: The project a project or release scope belongs to; None for a pod.
    project_id: str | None
    state: HygieneState
    #: The member asked; None when nobody could be named.
    owner_member_id: str | None
    #: The breach's detail, how the owner was found, and the issue's pods and releases.
    detail: Detail
    fingerprint: str
    #: Opened on stale Jira data: shown, but kept out of the day report until fresh.
    held: bool
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None = None
    resolution: Resolution | None = None
    decision: PersonDecision | None = None

    @property
    def what(self) -> str:
        value = self.detail.get("what")
        return value if isinstance(value, str) else ""

    @property
    def stage(self) -> DeliveryStage | None:
        value = self.detail.get("stage")
        return DeliveryStage(value) if isinstance(value, str) and value in _STAGES else None

    @property
    def waits_from(self) -> date:
        value = self.detail.get("waits_from")
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        return self.first_seen_at.date()

    @property
    def pod_ids(self) -> tuple[str, ...]:
        return _strings(self.detail.get("pod_ids"))

    @property
    def release_ids(self) -> tuple[str, ...]:
        return _strings(self.detail.get("release_ids"))

    @property
    def owner_via(self) -> str | None:
        value = self.detail.get("owner_via")
        return value if isinstance(value, str) else None

    @property
    def gate_asks(self) -> bool:
        return self.detail.get("gate_asks") is True


@dataclass(frozen=True, kw_only=True)
class HygieneAction:
    """One row of the append-only audit: a person's action or the scan's change."""

    tenant_id: str
    action_id: str
    at: datetime
    #: A member id, or "agent".
    actor: str
    action: str
    rule_id: str | None = None
    finding_id: str | None = None
    note: str | None = None
    detail: Mapping[str, object] | None = None


@dataclass(frozen=True, kw_only=True)
class HygieneRun:
    tenant_id: str
    run_id: str
    #: The tick's time, or manual:<scope>:<minute>; one run per slot.
    slot: str
    trigger: RunTrigger
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    counts: Mapping[str, int] = field(default_factory=dict)
    #: The Jira sync the run read.
    data_as_of: datetime | None = None
    error_category: str | None = None
    actor: str | None = None


# ---- the rules --------------------------------------------------------------------------


def selected(rule: HygieneRule, issue: ScopeIssue) -> bool:
    """Whether the rule looks at this issue: its type, its stage and every matcher."""
    if not rule.select.every_type and not issue.requirement:
        return False
    if not _stage_passes(rule.select, issue.stage):
        return False
    return all(match(matcher, issue) for matcher in rule.select.matchers)


def _stage_passes(select: Select, stage: DeliveryStage | None) -> bool:
    if select.stage_filter is StageFilter.ANY or select.stage is None:
        return True
    if stage is None:
        return False
    position, guard = STAGE_ORDER.index(stage), STAGE_ORDER.index(select.stage)
    if select.stage_filter is StageFilter.BEFORE:
        return position < guard
    return position >= guard


def needs_history(rule: HygieneRule) -> bool:
    """Whether the rule reads the issue's changes (when it joined, when it last moved)."""
    return rule.condition.kind in {
        ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL,
        ConditionKind.STALE_IN_STATUS,
    }


def needs_gates(rule: HygieneRule) -> bool:
    return rule.condition.kind is ConditionKind.MISSING_GATE_KIND


def needs_baseline(rule: HygieneRule) -> bool:
    return rule.condition.kind is ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL


def evaluate_rule(rule: HygieneRule, issue: ScopeIssue, context: IssueContext) -> Breach | None:
    """What is wrong with one issue under one rule, or None. Pure."""
    if not selected(rule, issue):
        return None
    return _CONDITIONS[rule.condition.kind](rule.condition, issue, context)


def _missing_label(
    condition: Condition, issue: ScopeIssue, _context: IssueContext
) -> Breach | None:
    wanted = {label.casefold() for label in condition.labels}
    if any(label.casefold() in wanted for label in issue.labels):
        return None
    return Breach(
        what=f"Has no label {_joined_or(list(condition.labels))}",
        detail={"labels": list(condition.labels)},
    )


def _missing_field(
    condition: Condition, issue: ScopeIssue, _context: IssueContext
) -> Breach | None:
    if condition.field is None or _has_field(issue, condition.field):
        return None
    return Breach(
        what=FIELD_MISSING_WORDS[condition.field], detail={"field": condition.field.value}
    )


def _too_big(condition: Condition, issue: ScopeIssue, _context: IssueContext) -> Breach | None:
    points = issue.story_points
    if points is None or points <= condition.points_over:
        return None
    return Breach(
        what=f"{_number(points)} story points, over {_number(condition.points_over)}",
        detail={"field": IssueField.STORY_POINTS.value, "points": points},
    )


def change_labels_for(condition: Condition, context: IssueContext) -> tuple[str, ...]:
    """The labels that mark an addition as an agreed change: the rule's, else the
    baseline's and the tenant's."""
    if condition.labels:
        return condition.labels
    labels = [context.baseline.change_label] if context.baseline else []
    labels.extend(context.change_labels)
    return _clean_labels(labels) or (DEFAULT_CHANGE_LABEL,)


def _added_without_label(
    condition: Condition, issue: ScopeIssue, context: IssueContext
) -> Breach | None:
    baseline = context.baseline
    if baseline is None or issue.key in baseline.keys:
        return None
    labels = change_labels_for(condition, context)
    wanted = {label.casefold() for label in labels}
    if any(label.casefold() in wanted for label in issue.labels):
        return None
    grace_ends = plus_working_days(baseline.planned_on, baseline.grace_working_days)
    joined = context.joined_on
    if joined is not None and joined <= grace_ends:
        # Inside planning's grace: still planned work.
        return None
    if joined is None and context.today <= grace_ends:
        # When it joined is not known, and the grace has not run out yet.
        return None
    planned = _day(baseline.planned_on)
    label = _label_words(labels)
    what = (
        f"Joined the scope on {_day(joined)}, after planning on {planned}, without {label}"
        if joined is not None and joined > baseline.planned_on
        else f"Added after planning on {planned}, without {label}"
    )
    return Breach(
        what=what,
        detail={
            "baseline_on": baseline.planned_on.isoformat(),
            "joined_on": joined.isoformat() if joined else None,
            "labels": list(labels),
            "grace_working_days": baseline.grace_working_days,
        },
        waits_from=max(joined or grace_ends, grace_ends),
    )


def _missing_gate_kind(
    condition: Condition, _issue: ScopeIssue, context: IssueContext
) -> Breach | None:
    kind = condition.gate_kind
    if not context.gate_scanned or kind in context.gate_kinds:
        # Not read yet: nothing is known about its text.
        return None
    label = context.gate_kind_labels.get(kind) or kind.replace("_", " ")
    return Breach(
        what=f"No {name_in_sentence(label)} found in its Jira text",
        detail={"gate_kind": kind, "gate_asks": kind in context.gate_asks},
    )


def _stale(condition: Condition, issue: ScopeIssue, context: IssueContext) -> Breach | None:
    if condition.stage is None or issue.stage is not condition.stage or issue.done:
        return None
    last = context.last_changed_on
    if last is None:
        return None
    days = max(0, working_days_left(last, context.today))
    if days < condition.working_days:
        return None
    stage = STAGE_LABELS[condition.stage]
    return Breach(
        what=f"No Jira change for {_days(days)} in {stage}",
        detail={
            "stage_held": condition.stage.value,
            "last_changed_on": last.isoformat(),
            "days": days,
        },
    )


def _past_due(condition: Condition, issue: ScopeIssue, context: IssueContext) -> Breach | None:
    due = _iso_date(issue.due_date)
    if due is None or issue.done:
        return None
    late = working_days_left(due, context.today)
    if late <= condition.working_days:
        return None
    return Breach(
        what=f"Due {_day(due)}, {_days(late)} ago, and still open",
        detail={"field": IssueField.DUE_DATE.value, "due_on": due.isoformat(), "days": late},
        waits_from=due,
    )


def _has_field(issue: ScopeIssue, wanted: IssueField) -> bool:
    if wanted is IssueField.STORY_POINTS:
        return issue.story_points is not None
    if wanted is IssueField.DUE_DATE:
        return bool(issue.due_date)
    if wanted is IssueField.PARENT:
        return bool(issue.parent_key)
    if wanted is IssueField.FIX_VERSION:
        return bool(issue.fix_versions)
    if wanted is IssueField.PRIORITY:
        return bool(issue.priority)
    if wanted is IssueField.ASSIGNEE:
        return issue.assigned
    if wanted is IssueField.COMPONENTS:
        return bool(issue.components)
    return bool(issue.severity)


_CONDITIONS: Mapping[
    ConditionKind, Callable[[Condition, ScopeIssue, IssueContext], Breach | None]
] = {
    ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL: lambda c, i, x: _added_without_label(c, i, x),
    ConditionKind.MISSING_LABEL: lambda c, i, x: _missing_label(c, i, x),
    ConditionKind.MISSING_FIELD: lambda c, i, x: _missing_field(c, i, x),
    ConditionKind.MISSING_GATE_KIND: lambda c, i, x: _missing_gate_kind(c, i, x),
    ConditionKind.STALE_IN_STATUS: lambda c, i, x: _stale(c, i, x),
    ConditionKind.PAST_DUE_OPEN: lambda c, i, x: _past_due(c, i, x),
    ConditionKind.TOO_BIG: lambda c, i, x: _too_big(c, i, x),
}

FIELD_MISSING_WORDS: Mapping[IssueField, str] = {
    IssueField.STORY_POINTS: "No story points",
    IssueField.DUE_DATE: "No due date",
    IssueField.PARENT: "No epic or parent",
    IssueField.FIX_VERSION: "No fix version",
    IssueField.PRIORITY: "No priority",
    IssueField.ASSIGNEE: "Nobody assigned",
    IssueField.COMPONENTS: "No component",
    IssueField.SEVERITY: "No severity",
}
#: A field as "without ..." says it.
FIELD_WITHOUT_WORDS: Mapping[IssueField, str] = {
    IssueField.STORY_POINTS: "story points",
    IssueField.DUE_DATE: "a due date",
    IssueField.PARENT: "an epic",
    IssueField.FIX_VERSION: "a fix version",
    IssueField.PRIORITY: "a priority",
    IssueField.ASSIGNEE: "an assignee",
    IssueField.COMPONENTS: "a component",
    IssueField.SEVERITY: "a severity",
}
FIELD_WORDS: Mapping[IssueField, str] = {
    IssueField.STORY_POINTS: "story points",
    IssueField.DUE_DATE: "due date",
    IssueField.PARENT: "epic",
    IssueField.FIX_VERSION: "fix version",
    IssueField.PRIORITY: "priority",
    IssueField.ASSIGNEE: "assignee",
    IssueField.COMPONENTS: "component",
    IssueField.SEVERITY: "severity",
}


def finding_fingerprint(
    *,
    rule_version: int,
    issue_key: str,
    scope: ScopeRef,
    detail: Detail,
    owner_member_id: str | None,
    issue_updated_at: str | None,
    held: bool,
) -> str:
    """One finding's identity across runs: unchanged means nothing is written or audited."""
    parts = {
        "version": rule_version,
        "key": issue_key,
        "scope": scope.key,
        "detail": detail,
        "owner": owner_member_id,
        "updated": issue_updated_at,
        "held": held,
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


# ---- words ------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class FixValues:
    """What a suggested fix may say: keys, titles, stages, names and dates OpenProgram holds."""

    key: str
    title: str = ""
    stage: str = ""
    release: str = ""
    label: str = ""
    field: str = ""
    days: str = ""
    baseline_date: str = ""


def render_fix(rule: HygieneRule, values: FixValues) -> str:
    """The rule's suggested fix with its placeholders filled; an unknown one stays as typed."""
    template = rule.suggested_fix.strip() or default_fix(rule)
    filled = {name: getattr(values, name) for name in FIX_PLACEHOLDERS}
    return " ".join(
        _PLACEHOLDER.sub(lambda m: filled.get(m.group(1), m.group(0)) or "", template).split()
    )


def default_fix(rule: HygieneRule) -> str:
    condition = rule.condition
    kind = condition.kind
    if kind is ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL:
        return "Label {key} {label} if it was agreed as a change, or take it out of {release}."
    if kind is ConditionKind.MISSING_LABEL:
        return "Give {key} the label {label}."
    if kind is ConditionKind.MISSING_FIELD:
        return "Set {key}'s {field}."
    if kind is ConditionKind.MISSING_GATE_KIND:
        return "Write {key}'s {field} under its heading in the description."
    if kind is ConditionKind.STALE_IN_STATUS:
        return (
            "{key} has had no Jira change for {days} working days in {stage}: move it on, or "
            "say what holds it."
        )
    if kind is ConditionKind.PAST_DUE_OPEN:
        return "Finish {key}, or give it a new due date."
    return "Split {key} into smaller pieces."


def fix_values(
    rule: HygieneRule,
    finding_detail: Detail,
    *,
    key: str,
    title: str,
    release: str,
    gate_kind_label: str = "",
) -> FixValues:
    """The suggested fix's words for one finding, from its detail."""
    condition = rule.condition
    labels = _strings(finding_detail.get("labels")) or condition.labels
    field_name = ""
    if condition.field is not None:
        field_name = FIELD_WORDS[condition.field]
    elif condition.kind is ConditionKind.MISSING_GATE_KIND:
        field_name = name_in_sentence(gate_kind_label or condition.gate_kind.replace("_", " "))
    elif condition.kind is ConditionKind.TOO_BIG:
        field_name = FIELD_WORDS[IssueField.STORY_POINTS]
    stage_value = finding_detail.get("stage_held") or finding_detail.get("stage")
    stage = (
        STAGE_LABELS[DeliveryStage(stage_value)]
        if isinstance(stage_value, str) and stage_value in _STAGES
        else ""
    )
    days = finding_detail.get("days")
    baseline_on = finding_detail.get("baseline_on")
    return FixValues(
        key=key,
        title=title,
        stage=stage,
        release=release,
        label=labels[0] if labels else "",
        field=field_name,
        days=str(days) if isinstance(days, int) else "",
        baseline_date=_day(date.fromisoformat(baseline_on)) if isinstance(baseline_on, str) else "",
    )


def rule_words(rule: HygieneRule, *, gate_kind_label: str = "") -> str:
    """The rule stated first, as gates and readiness state theirs.

    "Requirements before In development without story points; lists them."
    """
    select = rule.select
    what = "issues" if select.every_type else "requirements"
    kinds = [m.value for m in select.matchers if m.kind is MatcherKind.ISSUE_TYPE]
    if kinds:
        what = f"{_joined_or(kinds)} issues"
    others = [m for m in select.matchers if m.kind is not MatcherKind.ISSUE_TYPE]
    where = ""
    if select.stage is not None and select.stage_filter is StageFilter.BEFORE:
        where = f" before {STAGE_LABELS[select.stage]}"
    elif select.stage is not None and select.stage_filter is StageFilter.AT_OR_PAST:
        where = f" in {STAGE_LABELS[select.stage]} or later"
    narrowed = f" that {_joined_and([_select_words(m) for m in others])}" if others else ""
    condition = _condition_words(rule.condition, gate_kind_label)
    tail = "asks its owner" if rule.asks else "lists them"
    return f"{what[0].upper()}{what[1:]}{where}{narrowed} {condition}; {tail}."


def _select_words(matcher: Matcher) -> str:
    words = {
        MatcherKind.LABEL: "carry the label {}",
        MatcherKind.TITLE_PHRASE: 'say "{}"',
        MatcherKind.TITLE_WORDS: "have the words {}",
        MatcherKind.EPIC: "sit under {}",
        MatcherKind.ISSUE_TYPE: "are a {}",
    }
    return words[matcher.kind].format(matcher.value)


def _condition_words(condition: Condition, gate_kind_label: str) -> str:
    kind = condition.kind
    if kind is ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL:
        labels = (
            _label_words(condition.labels) if condition.labels else "the planning's change label"
        )
        return f"added after planning without {labels}"
    if kind is ConditionKind.MISSING_LABEL:
        return f"without the label {_joined_or(list(condition.labels))}"
    if kind is ConditionKind.MISSING_FIELD and condition.field is not None:
        return f"without {FIELD_WITHOUT_WORDS[condition.field]}"
    if kind is ConditionKind.MISSING_GATE_KIND:
        label = gate_kind_label or condition.gate_kind.replace("_", " ")
        return f"with no {name_in_sentence(label)} in their Jira text"
    if kind is ConditionKind.STALE_IN_STATUS and condition.stage is not None:
        return (
            f"with no Jira change for {_days(condition.working_days)} in "
            f"{STAGE_LABELS[condition.stage]}"
        )
    if kind is ConditionKind.PAST_DUE_OPEN:
        grace = condition.working_days
        after = f" by more than {_days(grace)}" if grace else ""
        return f"still open past their due date{after}"
    return f"with more than {_number(condition.points_over)} story points"


def ask_text(rule: HygieneRule, keys: Sequence[str], scope_name: str) -> str:
    """One ask's text, naming up to five keys.

    "Checkout Revamp: label 2 additions after planning as changes or take them
    out: CHK-20, CHK-21". "Story without an estimate in Checkout Revamp: CHK-3,
    CHK-7 and 2 more".
    """
    listed = ", ".join(keys[:MAX_ASK_KEYS])
    more = len(keys) - MAX_ASK_KEYS
    named = f"{listed} and {more} more" if more > 0 else listed
    if rule.condition.kind is ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL:
        if len(keys) == 1:
            return (
                f"{scope_name}: label 1 addition after planning as a change or take it out: {named}"
            )
        return (
            f"{scope_name}: label {len(keys)} additions after planning as changes or take them "
            f"out: {named}"
        )
    return f"{rule.name} in {scope_name}: {named}"


def count_line(open_count: int, new_today: int) -> str:
    """'Jira hygiene: 7 open (2 new today).'"""
    if open_count == 0:
        return "Jira hygiene: nothing open."
    new = f" ({new_today} new today)" if new_today else ""
    return f"Jira hygiene: {open_count} open{new}."


def name_in_sentence(name: str) -> str:
    """The name lower-cased to sit in a sentence, unless it starts with an acronym."""
    clean = " ".join(name.split())
    if len(clean) > 1 and clean[1].isupper():
        return clean
    return clean[:1].lower() + clean[1:]


def plus_working_days(day: date, days: int) -> date:
    current = day
    counted = 0
    while counted < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            counted += 1
    return current


# ---- validation -------------------------------------------------------------------------


def validated_rule(rule: HygieneRule) -> HygieneRule:
    """The rule tidied, or HygieneError naming every problem in one sentence."""
    problems: list[str] = []
    name = " ".join(rule.name.split())
    if not name or len(name) > MAX_NAME:
        problems.append(f"a rule needs a name of 1 to {MAX_NAME} characters")
    select = _select(rule.select, problems)
    condition = _condition(rule.condition, problems)
    owner = rule.owner
    if owner.kind is OwnerKind.PROFILE:
        slug = owner.profile.strip().lower()
        if not _PROFILE_SLUG.match(slug):
            problems.append("the owner's profile is one of the profile list's ids, such as qa")
        owner = Owner(kind=OwnerKind.PROFILE, profile=slug)
    else:
        owner = Owner(kind=owner.kind)
    fix = " ".join(rule.suggested_fix.split())
    if len(fix) > MAX_FIX:
        problems.append(f"the suggested fix is at most {MAX_FIX} characters")
    unknown = sorted({n for n in _PLACEHOLDER.findall(fix) if n not in FIX_PLACEHOLDERS})
    if unknown:
        problems.append(
            f"the suggested fix uses {', '.join('{' + n + '}' for n in unknown)}, which "
            "OpenProgram does not fill in"
        )
    if problems:
        raise HygieneError(_sentence_of(problems))
    return replace(
        rule,
        name=name,
        select=select,
        condition=condition,
        owner=owner,
        suggested_fix=fix,
    )


def _select(select: Select, problems: list[str]) -> Select:
    if len(select.matchers) > MAX_SELECT:
        problems.append(f"a rule narrows its issues with at most {MAX_SELECT} matchers")
    kept: list[Matcher] = []
    seen: set[tuple[MatcherKind, str]] = set()
    for matcher in select.matchers:
        value = " ".join(matcher.value.split())
        if not value or len(value) > MAX_MATCHER_VALUE:
            problems.append(f"each matcher needs a value of 1 to {MAX_MATCHER_VALUE} characters")
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
    stage = select.stage
    if select.stage_filter is not StageFilter.ANY and stage is None:
        problems.append("name the stage the issues sit before, or in or after")
    if select.stage_filter is StageFilter.ANY:
        stage = None
    return Select(
        matchers=tuple(kept),
        stage_filter=select.stage_filter,
        stage=stage,
        every_type=select.every_type,
    )


def _condition(condition: Condition, problems: list[str]) -> Condition:
    labels = _clean_labels(condition.labels)
    if any(not _label_ok(label) for label in labels) or len(labels) > MAX_LABELS:
        problems.append(f"name at most {MAX_LABELS} labels, each without spaces")
    return _CHECKS[condition.kind](replace(condition, labels=labels), problems)


def _check_added(condition: Condition, _problems: list[str]) -> Condition:
    return Condition(kind=condition.kind, labels=condition.labels)


def _check_label(condition: Condition, problems: list[str]) -> Condition:
    if not condition.labels:
        problems.append("name the label, or labels, an issue should have")
    return Condition(kind=condition.kind, labels=condition.labels)


def _check_field(condition: Condition, problems: list[str]) -> Condition:
    if condition.field is None:
        problems.append("name the field an issue should have")
    return Condition(kind=condition.kind, field=condition.field)


def _check_gate_kind(condition: Condition, problems: list[str]) -> Condition:
    gate_kind = condition.gate_kind.strip().lower()
    if not _GATE_KIND.match(gate_kind):
        problems.append("name the gate item kind, such as acceptance")
    return Condition(kind=condition.kind, gate_kind=gate_kind)


def _check_stale(condition: Condition, problems: list[str]) -> Condition:
    if condition.stage is None:
        problems.append("name the stage an issue sits in")
    if not 1 <= condition.working_days <= MAX_WORKING_DAYS:
        problems.append(f"the days without a change are 1 to {MAX_WORKING_DAYS}")
    return Condition(
        kind=condition.kind, stage=condition.stage, working_days=condition.working_days
    )


def _check_past_due(condition: Condition, problems: list[str]) -> Condition:
    if not 0 <= condition.working_days <= MAX_WORKING_DAYS:
        problems.append(f"the grace after the due date is 0 to {MAX_WORKING_DAYS} working days")
    return Condition(kind=condition.kind, working_days=condition.working_days)


def _check_too_big(condition: Condition, problems: list[str]) -> Condition:
    if not 0 < condition.points_over <= MAX_POINTS:
        problems.append(
            f"the story points an issue may have are above 0 and at most {MAX_POINTS:g}"
        )
    return Condition(kind=condition.kind, points_over=condition.points_over)


_CHECKS: Mapping[ConditionKind, Callable[[Condition, list[str]], Condition]] = {
    ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL: _check_added,
    ConditionKind.MISSING_LABEL: _check_label,
    ConditionKind.MISSING_FIELD: _check_field,
    ConditionKind.MISSING_GATE_KIND: _check_gate_kind,
    ConditionKind.STALE_IN_STATUS: _check_stale,
    ConditionKind.PAST_DUE_OPEN: _check_past_due,
    ConditionKind.TOO_BIG: _check_too_big,
}


def validated_settings(settings: HygieneSettings) -> HygieneSettings:
    problems: list[str] = []
    labels = _clean_labels(settings.change_labels)
    if len(labels) > MAX_LABELS or any(not _label_ok(label) for label in labels):
        problems.append(f"name at most {MAX_LABELS} change labels, each without spaces")
    if not 1 <= settings.stale_working_days <= MAX_WORKING_DAYS:
        problems.append(f"the days without a change are 1 to {MAX_WORKING_DAYS}")
    if problems:
        raise HygieneError(_sentence_of(problems))
    return replace(settings, change_labels=labels or (DEFAULT_CHANGE_LABEL,))


def validated_reason(reason: str) -> str:
    clean = " ".join(reason.split())
    if not MIN_REASON <= len(clean) <= MAX_REASON:
        raise HygieneError(f"Give a reason of {MIN_REASON} to {MAX_REASON} characters.")
    return clean


def rule_slug(name: str, taken: Iterable[str]) -> str:
    base = _SLUG.sub("-", fold(name)).strip("-")[:60] or "rule"
    used = set(taken)
    candidate, number = base, 2
    while candidate in used:
        candidate = f"{base}-{number}"
        number += 1
    return candidate


# ---- the examples a tenant can add ------------------------------------------------------


def default_examples(
    tenant_id: str, *, stale_working_days: int = DEFAULT_STALE_WORKING_DAYS
) -> tuple[HygieneRule, ...]:
    """Eight generic rules an admin may add. None is active until one is added, and
    each lists only until an admin switches it to ask."""

    def m(kind: MatcherKind, value: str) -> Matcher:
        return Matcher(kind=kind, value=value, strength=Strength.EVIDENCE)

    def example(
        rule_id: str,
        name: str,
        condition: Condition,
        owner: Owner,
        fix: str,
        *,
        select: Select | None = None,
        need: RuleNeed = RuleNeed.FIX,
    ) -> HygieneRule:
        return HygieneRule(
            tenant_id=tenant_id,
            rule_id=rule_id,
            name=name,
            scope=RuleScope.PROJECT,
            select=select or Select(),
            condition=condition,
            need=need,
            owner=owner,
            suggested_fix=fix,
        )

    in_development = DeliveryStage.IN_DEVELOPMENT
    return (
        example(
            "added-after-planning-without-change-label",
            "Added after planning without a change label",
            Condition(kind=ConditionKind.ADDED_AFTER_BASELINE_WITHOUT_LABEL),
            Owner(kind=OwnerKind.DECISION_OWNER),
            "Label {key} {label} if it was agreed as a change, or take it out of {release}.",
            need=RuleNeed.DECISION,
        ),
        example(
            "story-without-estimate",
            "Story without an estimate",
            Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.STORY_POINTS),
            Owner(kind=OwnerKind.POD_SCRUM_MASTER),
            "Estimate {key} before it starts.",
            select=Select(
                matchers=(m(MatcherKind.ISSUE_TYPE, "Story"),),
                stage_filter=StageFilter.BEFORE,
                stage=in_development,
            ),
        ),
        example(
            "requirement-without-acceptance-criteria",
            "Requirement without acceptance criteria",
            Condition(kind=ConditionKind.MISSING_GATE_KIND, gate_kind="acceptance"),
            Owner(kind=OwnerKind.PROFILE, profile="product"),
            "Write {key}'s acceptance criteria under an 'Acceptance criteria' heading.",
            select=Select(stage_filter=StageFilter.BEFORE, stage=in_development),
        ),
        example(
            "in-development-without-due-date",
            "Requirement without a due date in development",
            Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.DUE_DATE),
            Owner(kind=OwnerKind.ASSIGNEE),
            "Give {key} a due date, or an ETA in your check-in.",
            select=Select(stage_filter=StageFilter.AT_OR_PAST, stage=in_development),
        ),
        example(
            "bug-without-severity",
            "Bug without a severity",
            Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.SEVERITY),
            Owner(kind=OwnerKind.REPORTER),
            "Set {key}'s severity.",
            select=Select(matchers=(m(MatcherKind.ISSUE_TYPE, "Bug"),), every_type=True),
        ),
        example(
            "orphan-without-epic",
            "Story or task without an epic",
            Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.PARENT),
            Owner(kind=OwnerKind.PROFILE, profile="product"),
            "Link {key} to its epic.",
        ),
        example(
            "stale-in-development",
            "No movement in development",
            Condition(
                kind=ConditionKind.STALE_IN_STATUS,
                stage=in_development,
                working_days=stale_working_days,
            ),
            Owner(kind=OwnerKind.ASSIGNEE),
            "{key} has had no Jira change for {days} working days in {stage}: move it on, or "
            "say what holds it.",
        ),
        example(
            "unassigned-in-development",
            "Unassigned work in development",
            Condition(kind=ConditionKind.MISSING_FIELD, field=IssueField.ASSIGNEE),
            Owner(kind=OwnerKind.POD_SCRUM_MASTER),
            "Give {key} an owner.",
            select=Select(stage_filter=StageFilter.AT_OR_PAST, stage=in_development),
        ),
    )


# ---- small helpers ----------------------------------------------------------------------


_STAGES = frozenset(stage.value for stage in DeliveryStage)


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _day(day: date) -> str:
    return f"{day.day} {day:%b}"


def _days(days: int) -> str:
    return "1 working day" if days == 1 else f"{days} working days"


def _number(value: float) -> str:
    return f"{value:g}"


def _label_words(labels: Sequence[str]) -> str:
    if len(labels) == 1:
        return f"the {labels[0]} label"
    return f"a change label ({_joined_or(list(labels))})"


def _joined_or(parts: Sequence[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " or " + parts[-1]


def _joined_and(parts: Sequence[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _sentence_of(problems: Sequence[str]) -> str:
    text = "; ".join(problems)
    return text[0].upper() + text[1:] + "."


def _label_ok(label: str) -> bool:
    return bool(label) and len(label) <= MAX_LABEL and not any(ch.isspace() for ch in label)


def _clean_labels(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        clean = value.strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            kept.append(clean)
    return tuple(kept)


__all__ = [
    "DECIDED",
    "FIELD_MISSING_WORDS",
    "FIELD_WORDS",
    "FIX_PLACEHOLDERS",
    "MAX_ASK_KEYS",
    "MAX_RULES",
    "WAIVE_ROLES",
    "BaselineFacts",
    "Breach",
    "Condition",
    "ConditionKind",
    "FixValues",
    "HygieneAction",
    "HygieneError",
    "HygieneFinding",
    "HygieneRule",
    "HygieneRun",
    "HygieneSettings",
    "HygieneState",
    "IssueContext",
    "IssueField",
    "Owner",
    "OwnerKind",
    "PersonDecision",
    "Resolution",
    "RuleNeed",
    "RuleScope",
    "RuleSeverity",
    "Select",
    "StageFilter",
    "ask_text",
    "change_labels_for",
    "count_line",
    "default_examples",
    "default_fix",
    "evaluate_rule",
    "finding_fingerprint",
    "fix_values",
    "needs_baseline",
    "needs_gates",
    "needs_history",
    "plus_working_days",
    "render_fix",
    "rule_slug",
    "rule_words",
    "selected",
    "validated_reason",
    "validated_rule",
    "validated_settings",
]
