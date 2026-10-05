"""Gates a requirement passes on its way to production, and the questions it waits on.

A gate template, set up by an admin, guards one delivery stage: before a
requirement enters that stage its gate should be passed. A template lists the
kinds of item it needs (business acceptance criteria, test cases, anything a
team adds) and, per kind, who signs an item off, whether evidence is needed,
and the headings under which Jira descriptions and comments write them.

Items are suggested from the Jira issue's text and count only once a person
confirms them; a dismissed suggestion is remembered so it is not suggested
again. A confirmed item is pending until it is met, failed or waived by
someone whose role may sign that kind off.

Questions are asked in Jira comments by mentioning someone; each is tracked
until it is answered, partly answered, or the issue closes without an answer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from core.domain.auth import Role
from core.domain.delivery import STAGE_LABELS, STAGE_ORDER, DeliveryStage
from core.domain.errors import OpenProgramError

MAX_ITEM_TEXT = 1000
MAX_KINDS = 10
MAX_HINTS = 20


class GateError(OpenProgramError):
    """A gate template or item that cannot be saved."""


class ItemStatus(StrEnum):
    SUGGESTED = "suggested"
    DISMISSED = "dismissed"
    PENDING = "pending"
    MET = "met"
    FAILED = "failed"
    WAIVED = "waived"


#: The statuses a person sets when signing an item off.
SIGN_OFF_STATUSES = frozenset({ItemStatus.MET, ItemStatus.FAILED, ItemStatus.WAIVED})
CONFIRMED_STATUSES = frozenset(
    {ItemStatus.PENDING, ItemStatus.MET, ItemStatus.FAILED, ItemStatus.WAIVED}
)


class ItemSource(StrEnum):
    DESCRIPTION = "description"
    COMMENT = "comment"
    MANUAL = "manual"


class GateState(StrEnum):
    PASSED = "passed"
    OPEN = "open"
    FAILED = "failed"
    #: A required kind has no confirmed item yet.
    MISSING = "missing"


class QuestionStatus(StrEnum):
    NOT_YET = "not_yet"
    PARTLY = "partly"
    ANSWERED = "answered"
    #: The issue closed, or moved on, without an answer to the question.
    CLOSED_UNANSWERED = "closed_unanswered"


@dataclass(frozen=True, kw_only=True)
class ItemKind:
    key: str
    label: str
    sign_off_roles: tuple[Role, ...]
    evidence_required: bool = False
    #: Headings that introduce this kind's items in a description or comment.
    headings: tuple[str, ...] = ()
    #: Read Given/When/Then scenarios as items of this kind.
    gherkin: bool = False


@dataclass(frozen=True, kw_only=True)
class GateTemplate:
    tenant_id: str
    template_id: str
    name: str
    #: The stage a requirement should not enter before the gate is passed.
    guards_stage: DeliveryStage
    kinds: tuple[ItemKind, ...]
    #: Issue types the gate applies to; empty applies to every requirement.
    issue_types: tuple[str, ...] = ()
    enabled: bool = True
    updated_at: datetime | None = None
    updated_by: str | None = None

    def kind(self, key: str) -> ItemKind | None:
        return next((kind for kind in self.kinds if kind.key == key), None)

    def applies_to(self, issue_type: str | None) -> bool:
        if not self.issue_types:
            return True
        return issue_type is not None and any(
            name.casefold() == issue_type.casefold() for name in self.issue_types
        )


@dataclass(frozen=True, kw_only=True)
class GateItem:
    tenant_id: str
    item_id: str
    issue_key: str
    template_id: str
    kind: str
    text: str
    status: ItemStatus
    source: ItemSource
    #: The comment id, "description", or "" for a manual item.
    source_ref: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    signed_by: str | None = None
    signed_at: datetime | None = None
    evidence_url: str | None = None
    note: str = ""

    @property
    def fingerprint(self) -> str:
        return item_fingerprint(self.issue_key, self.template_id, self.kind, self.text)


@dataclass(frozen=True, kw_only=True)
class TrackedQuestion:
    tenant_id: str
    question_id: str
    issue_key: str
    #: The Jira comment the question was asked in, or "" for one added by hand.
    comment_ref: str
    asked_by: str
    asked_to: str
    asked_at: datetime
    summary: str
    status: QuestionStatus
    asked_by_name: str = ""
    asked_to_name: str = ""
    #: False until a person confirms the parsed question belongs on the list.
    confirmed: bool = False
    #: True once a person set the status; parsing no longer changes it.
    status_set_by_person: bool = False
    answered_ref: str | None = None
    dismissed: bool = False
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass(frozen=True, kw_only=True)
class GateEvaluation:
    template: GateTemplate
    state: GateState
    met: int
    total: int
    suggested: int
    #: Kinds the template needs that have no confirmed item.
    missing_kinds: tuple[str, ...] = ()


def evaluate_gate(template: GateTemplate, items: Sequence[GateItem]) -> GateEvaluation:
    """Where one issue stands against one gate."""
    mine = [item for item in items if item.template_id == template.template_id]
    confirmed = [item for item in mine if item.status in CONFIRMED_STATUSES]
    suggested = sum(1 for item in mine if item.status is ItemStatus.SUGGESTED)
    missing = tuple(
        kind.key for kind in template.kinds if not any(item.kind == kind.key for item in confirmed)
    )
    met = sum(1 for item in confirmed if item.status in {ItemStatus.MET, ItemStatus.WAIVED})
    if any(item.status is ItemStatus.FAILED for item in confirmed):
        state = GateState.FAILED
    elif missing:
        state = GateState.MISSING
    elif met == len(confirmed):
        state = GateState.PASSED
    else:
        state = GateState.OPEN
    return GateEvaluation(
        template=template,
        state=state,
        met=met,
        total=len(confirmed),
        suggested=suggested,
        missing_kinds=missing,
    )


def past_its_gate(stage: DeliveryStage, template: GateTemplate) -> bool:
    """Whether a requirement in ``stage`` is at or past the stage the template guards."""
    return STAGE_ORDER.index(stage) >= STAGE_ORDER.index(template.guards_stage)


def item_fingerprint(issue_key: str, template_id: str, kind: str, text: str) -> str:
    """One item's identity across scans: the same text on the same issue and kind."""
    normal = " ".join(text.split()).casefold()
    return hashlib.sha256(f"{issue_key}|{template_id}|{kind}|{normal}".encode()).hexdigest()


def default_templates(tenant_id: str) -> tuple[GateTemplate, ...]:
    """What a tenant starts with: business acceptance before production, tests before UAT."""
    return (
        GateTemplate(
            tenant_id=tenant_id,
            template_id="business-acceptance",
            name="Business acceptance",
            guards_stage=DeliveryStage.PRODUCTION,
            kinds=(
                ItemKind(
                    key="acceptance",
                    label="Acceptance criterion",
                    sign_off_roles=(Role.PO, Role.MGR),
                    headings=(
                        "Acceptance criteria",
                        "Acceptance criterion",
                        "AC",
                        "Business acceptance",
                        "Definition of done",
                        "Abnahmekriterien",
                    ),
                ),
            ),
        ),
        GateTemplate(
            tenant_id=tenant_id,
            template_id="engineering-delivery",
            name="Engineering delivery",
            guards_stage=DeliveryStage.BUSINESS_TESTING,
            kinds=(
                ItemKind(
                    key="test_case",
                    label="Test case",
                    sign_off_roles=(Role.DEV, Role.SM),
                    evidence_required=True,
                    headings=("Test cases", "Test case", "Test plan", "Tests", "Testfälle"),
                    gherkin=True,
                ),
            ),
        ),
    )


def validated_template(template: GateTemplate) -> GateTemplate:
    problems: list[str] = []
    name = " ".join(template.name.split())
    if not name:
        problems.append("a gate needs a name")
    if not template.kinds:
        problems.append("a gate needs at least one kind of item")
    if len(template.kinds) > MAX_KINDS:
        problems.append(f"a gate has at most {MAX_KINDS} kinds of item")
    keys: set[str] = set()
    kinds: list[ItemKind] = []
    for kind in template.kinds:
        key = "_".join(kind.key.split()).casefold()
        label = " ".join(kind.label.split())
        if not key or not label:
            problems.append("every kind needs a key and a label")
            continue
        if key in keys:
            problems.append(f"the kind {key!r} appears twice")
            continue
        if not kind.sign_off_roles:
            problems.append(f"say who signs off {label}")
        keys.add(key)
        kinds.append(
            ItemKind(
                key=key,
                label=label,
                sign_off_roles=tuple(dict.fromkeys(kind.sign_off_roles)),
                evidence_required=kind.evidence_required,
                headings=_clean(kind.headings)[:MAX_HINTS],
                gherkin=kind.gherkin,
            )
        )
    if problems:
        text = "; ".join(problems)
        raise GateError(text[0].upper() + text[1:] + ".")
    return GateTemplate(
        tenant_id=template.tenant_id,
        template_id=template.template_id,
        name=name,
        guards_stage=template.guards_stage,
        kinds=tuple(kinds),
        issue_types=_clean(template.issue_types),
        enabled=template.enabled,
        updated_at=template.updated_at,
        updated_by=template.updated_by,
    )


def validated_item_text(text: str) -> str:
    clean = " ".join(text.split())
    if not clean:
        raise GateError("An item needs its text.")
    if len(clean) > MAX_ITEM_TEXT:
        raise GateError(f"An item is at most {MAX_ITEM_TEXT} characters.")
    return clean


def stage_label(stage: DeliveryStage) -> str:
    return STAGE_LABELS[stage]


def _clean(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        clean = " ".join(value.split())
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            kept.append(clean)
    return tuple(kept)


@dataclass(frozen=True, kw_only=True)
class ExtractedItem:
    """An item found in an issue's text, before it is stored as a suggestion."""

    template_id: str
    kind: str
    text: str
    source: ItemSource
    source_ref: str


@dataclass(frozen=True, kw_only=True)
class ExtractedQuestion:
    comment_ref: str
    asked_by: str
    asked_to: str
    asked_at: datetime
    summary: str
    status: QuestionStatus
    answered_ref: str | None = None
    asked_by_name: str = ""
    asked_to_name: str = ""


@dataclass(frozen=True, kw_only=True)
class Extraction:
    items: tuple[ExtractedItem, ...] = ()
    questions: tuple[ExtractedQuestion, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
