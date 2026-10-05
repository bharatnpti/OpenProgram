"""How long an ask waits before it goes up a level, and who stands at each level.

Everything a day report lists under "What we need, and from whom" is an ask:
something the project needs from one person, of one of four kinds. Its owner,
the person who can do it, is level 1. The matrix says, per project, after how
many days of waiting each kind of ask reaches each further level, and who
stands there: the ask's team's scrum master, its team's manager, or a named
member.

A project without a matrix of its own uses the tenant's; a tenant without one
uses ``default_matrix``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from core.domain.errors import OpenProgramError

MAX_LEVELS = 5
MAX_DAYS = 365
MAX_LABEL = 60
#: The project id under which the tenant's own matrix is kept.
TENANT_SCOPE = ""


class EscalationMatrixError(OpenProgramError):
    """A matrix that cannot be saved."""


class NeedType(StrEnum):
    #: Something blocked or broken to put right.
    FIX = "fix"
    #: A call only its owner can make, such as accepting a requirement.
    DECISION = "decision"
    #: A question waiting for a reply.
    ANSWER = "answer"
    #: Work waiting to be looked at: a code review, suggestions to keep.
    REVIEW = "review"


NEED_ORDER: tuple[NeedType, ...] = (
    NeedType.FIX,
    NeedType.DECISION,
    NeedType.ANSWER,
    NeedType.REVIEW,
)

NEED_LABELS: Mapping[NeedType, str] = {
    NeedType.FIX: "Fix",
    NeedType.DECISION: "Decision",
    NeedType.ANSWER: "Answer",
    NeedType.REVIEW: "Review",
}


class ContactSource(StrEnum):
    #: The scrum master of the team the ask belongs to.
    TEAM_SCRUM_MASTER = "team_scrum_master"
    #: The manager of the team the ask belongs to.
    TEAM_MANAGER = "team_manager"
    #: One named member, whatever the team.
    MEMBER = "member"


@dataclass(frozen=True, kw_only=True)
class EscalationLevel:
    label: str
    source: ContactSource
    member_id: str | None = None
    #: Days an ask of each kind waits before it reaches this level; a kind
    #: left out never reaches it.
    after_days: Mapping[NeedType, int] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class EscalationMatrix:
    tenant_id: str
    #: ``TENANT_SCOPE`` for the tenant's own matrix.
    project_id: str
    #: Who owns a decision nothing else names an owner for: the product owner.
    decision_owner_id: str | None
    #: Level 2 first; level 1 is always the ask's owner.
    levels: tuple[EscalationLevel, ...]
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReachedLevel:
    #: 2 for the first level above the owner.
    number: int
    level: EscalationLevel


def default_matrix(tenant_id: str, project_id: str = TENANT_SCOPE) -> EscalationMatrix:
    """Up to the team's scrum master after two or three days, its manager after four to six."""
    return EscalationMatrix(
        tenant_id=tenant_id,
        project_id=project_id,
        decision_owner_id=None,
        levels=(
            EscalationLevel(
                label="Scrum master",
                source=ContactSource.TEAM_SCRUM_MASTER,
                after_days={
                    NeedType.FIX: 2,
                    NeedType.DECISION: 2,
                    NeedType.ANSWER: 3,
                    NeedType.REVIEW: 2,
                },
            ),
            EscalationLevel(
                label="Manager",
                source=ContactSource.TEAM_MANAGER,
                after_days={
                    NeedType.FIX: 5,
                    NeedType.DECISION: 4,
                    NeedType.ANSWER: 6,
                    NeedType.REVIEW: 5,
                },
            ),
        ),
    )


def reached_levels(
    matrix: EscalationMatrix, need: NeedType, waited_days: int
) -> tuple[ReachedLevel, ...]:
    """The levels an ask that has waited ``waited_days`` has reached, the highest last."""
    return tuple(
        ReachedLevel(number=index + 2, level=level)
        for index, level in enumerate(matrix.levels)
        if need in level.after_days and waited_days >= level.after_days[need]
    )


def validated_matrix(matrix: EscalationMatrix) -> EscalationMatrix:
    """The matrix tidied, or EscalationMatrixError naming every problem."""
    problems: list[str] = []
    if len(matrix.levels) > MAX_LEVELS:
        problems.append(f"a matrix has at most {MAX_LEVELS} levels above the owner")
    levels: list[EscalationLevel] = []
    for index, level in enumerate(matrix.levels):
        number = index + 2
        label = " ".join(level.label.split())
        if not label:
            problems.append(f"level {number} needs a name")
        elif len(label) > MAX_LABEL:
            problems.append(f"level {number}'s name is longer than {MAX_LABEL} characters")
        member = (level.member_id or "").strip() or None
        if level.source is ContactSource.MEMBER and member is None:
            problems.append(f"level {number} needs the member it goes to")
        days = {need: level.after_days[need] for need in NEED_ORDER if need in level.after_days}
        if any(value < 0 or value > MAX_DAYS for value in days.values()):
            problems.append(f"level {number} waits between 0 and {MAX_DAYS} days")
        levels.append(
            EscalationLevel(
                label=label,
                source=level.source,
                member_id=member if level.source is ContactSource.MEMBER else None,
                after_days=days,
            )
        )
    problems.extend(_order_problems(levels))
    if problems:
        text = "; ".join(dict.fromkeys(problems))
        raise EscalationMatrixError(text[0].upper() + text[1:] + ".")
    return EscalationMatrix(
        tenant_id=matrix.tenant_id,
        project_id=matrix.project_id.strip(),
        decision_owner_id=(matrix.decision_owner_id or "").strip() or None,
        levels=tuple(levels),
        updated_at=matrix.updated_at,
        updated_by=matrix.updated_by,
    )


def _order_problems(levels: Sequence[EscalationLevel]) -> list[str]:
    """A higher level is never reached sooner than the one below it."""
    problems: list[str] = []
    for need in NEED_ORDER:
        last: tuple[int, int] | None = None
        for index, level in enumerate(levels):
            if need not in level.after_days:
                continue
            days = level.after_days[need]
            if last is not None and days < last[1]:
                problems.append(
                    f"level {index + 2} is reached before level {last[0]} for "
                    f"{NEED_LABELS[need].lower()}"
                )
            last = (index + 2, days)
    return problems
