"""Where each requirement stands on its way to production.

A requirement is a tracker issue of a requirement type. Its stage is read from
the tracker's own status name through the tenant's stage mapping, so the six
stages mean the same thing on every project whatever each team's workflow
calls them. A status the mapping does not name falls back on the tracker's
broad state (to do, in progress, done) and is reported as unmapped, so an admin
can see which statuses still need placing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from core.domain.errors import OpenProgramError


class DeliveryStage(StrEnum):
    RAISED = "raised"
    GROOMED = "groomed"
    IN_DEVELOPMENT = "in_development"
    IN_TESTING = "in_testing"
    BUSINESS_TESTING = "business_testing"
    PRODUCTION = "production"


STAGE_ORDER: tuple[DeliveryStage, ...] = tuple(DeliveryStage)

STAGE_LABELS: Mapping[DeliveryStage, str] = {
    DeliveryStage.RAISED: "Raised",
    DeliveryStage.GROOMED: "Groomed",
    DeliveryStage.IN_DEVELOPMENT: "In development",
    DeliveryStage.IN_TESTING: "In testing",
    DeliveryStage.BUSINESS_TESTING: "Business testing",
    DeliveryStage.PRODUCTION: "Production",
}

# Where an unmapped status lands, by the tracker's broad state.
_STATE_FALLBACK: Mapping[str, DeliveryStage] = {
    "todo": DeliveryStage.RAISED,
    "in_progress": DeliveryStage.IN_DEVELOPMENT,
    "blocked": DeliveryStage.IN_DEVELOPMENT,
    "done": DeliveryStage.PRODUCTION,
}

MAX_STATUS_NAME_LENGTH = 120
MAX_NAMES_PER_LIST = 100


class StageMappingError(OpenProgramError):
    """The stage mapping an admin entered cannot be used."""


@dataclass(frozen=True, kw_only=True)
class StageMapping:
    """Which tracker statuses count as which stage, compared case-insensitively.

    ``excluded_statuses`` are dropped from every count (won't do, duplicate).
    ``requirement_types`` limits which issue types count as requirements; empty
    counts every type.
    """

    statuses: Mapping[DeliveryStage, tuple[str, ...]]
    excluded_statuses: tuple[str, ...] = ()
    requirement_types: tuple[str, ...] = ()

    def stage_of(self, status: str) -> DeliveryStage | None:
        wanted = _key(status)
        for stage in STAGE_ORDER:
            if any(_key(name) == wanted for name in self.statuses.get(stage, ())):
                return stage
        return None

    def excludes(self, status: str) -> bool:
        wanted = _key(status)
        return any(_key(name) == wanted for name in self.excluded_statuses)

    def counts_type(self, issue_type: str | None) -> bool:
        if not self.requirement_types:
            return True
        return issue_type is not None and any(
            _key(name) == _key(issue_type) for name in self.requirement_types
        )


DEFAULT_STAGE_MAPPING = StageMapping(
    statuses={
        DeliveryStage.RAISED: ("Open", "New", "To Do", "Backlog", "Funnel"),
        DeliveryStage.GROOMED: (
            "Groomed",
            "Refined",
            "Ready",
            "Ready for Development",
            "Selected for Development",
        ),
        DeliveryStage.IN_DEVELOPMENT: (
            "In Progress",
            "In Development",
            "In Review",
            "Code Review",
        ),
        DeliveryStage.IN_TESTING: ("Ready for QA", "In QA", "QA", "Testing", "In Testing"),
        DeliveryStage.BUSINESS_TESTING: (
            "UAT",
            "In UAT",
            "Business Testing",
            "Acceptance",
            "Business Acceptance",
        ),
        DeliveryStage.PRODUCTION: (
            "Done",
            "Closed",
            "Resolved",
            "Released",
            "Deployed",
            "In Production",
        ),
    },
    excluded_statuses=("Won't Do", "Won't Fix", "Cancelled", "Canceled", "Rejected", "Duplicate"),
)


@dataclass(frozen=True, kw_only=True)
class DeliverySettings:
    tenant_id: str
    mapping: StageMapping
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass(frozen=True, kw_only=True)
class StagePlacement:
    #: None when the status is excluded from every count.
    stage: DeliveryStage | None
    #: False when the status is not in the mapping and its broad state decided.
    mapped: bool


def place(mapping: StageMapping, *, status: str | None, state: str | None) -> StagePlacement:
    """The stage a requirement with this tracker status and broad state is in."""
    if status:
        if mapping.excludes(status):
            return StagePlacement(stage=None, mapped=True)
        stage = mapping.stage_of(status)
        if stage is not None:
            return StagePlacement(stage=stage, mapped=True)
    return StagePlacement(
        stage=_STATE_FALLBACK.get((state or "").strip().lower(), DeliveryStage.RAISED),
        mapped=False,
    )


def validated_mapping(
    statuses: Mapping[DeliveryStage, Iterable[str]],
    *,
    excluded_statuses: Iterable[str],
    requirement_types: Iterable[str],
) -> StageMapping:
    """The mapping an admin entered, tidied, or StageMappingError naming each problem.

    Names are trimmed and de-duplicated. A status may sit in one stage only,
    and an excluded status in none.
    """
    problems: list[str] = []
    cleaned: dict[DeliveryStage, tuple[str, ...]] = {}
    seen: dict[str, str] = {}
    for stage in STAGE_ORDER:
        names = _clean_names(statuses.get(stage, ()), STAGE_LABELS[stage], problems)
        for name in names:
            owner = seen.get(_key(name))
            if owner is not None:
                problems.append(f'"{name}" is in both {owner} and {STAGE_LABELS[stage]}')
            seen[_key(name)] = STAGE_LABELS[stage]
        cleaned[stage] = names
    excluded = _clean_names(excluded_statuses, "Not counted", problems)
    for name in excluded:
        owner = seen.get(_key(name))
        if owner is not None:
            problems.append(f'"{name}" is in {owner} and also not counted')
    types = _clean_names(requirement_types, "Requirement types", problems)
    if problems:
        text = "; ".join(problems)
        raise StageMappingError(text[0].upper() + text[1:] + ".")
    return StageMapping(statuses=cleaned, excluded_statuses=excluded, requirement_types=types)


@dataclass(frozen=True, kw_only=True)
class RequirementItem:
    key: str
    title: str
    stage: DeliveryStage
    status: str | None
    mapped: bool
    assignee_id: str | None = None
    story_points: float | None = None
    due_date: str | None = None


@dataclass(frozen=True, kw_only=True)
class RequirementsSnapshot:
    """One project's requirements on one day: how many sit in each stage.

    ``items`` keeps each requirement's stage that day, so two snapshots show
    which requirements moved and which joined or left the scope.
    """

    tenant_id: str
    project_id: str
    day: date
    stage_counts: Mapping[DeliveryStage, int]
    stage_points: Mapping[DeliveryStage, float]
    #: True when every requirement counted carries story points.
    has_points: bool
    excluded: int
    unmapped_statuses: tuple[str, ...]
    items: Mapping[str, DeliveryStage]
    computed_at: datetime
    titles: Mapping[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.stage_counts.values())

    @property
    def done(self) -> int:
        return self.stage_counts.get(DeliveryStage.PRODUCTION, 0)

    @property
    def points_total(self) -> float:
        return sum(self.stage_points.values())

    @property
    def points_done(self) -> float:
        return self.stage_points.get(DeliveryStage.PRODUCTION, 0.0)

    @property
    def percent_complete(self) -> float | None:
        """Share in production: by story points when every requirement has them, else by count."""
        if self.has_points and self.points_total > 0:
            return self.points_done / self.points_total * 100.0
        if self.total == 0:
            return None
        return self.done / self.total * 100.0


@dataclass(frozen=True, kw_only=True)
class StageMove:
    key: str
    title: str
    from_stage: DeliveryStage | None
    to_stage: DeliveryStage | None


def stage_moves(
    before: RequirementsSnapshot | None, after: RequirementsSnapshot
) -> tuple[StageMove, ...]:
    """What changed between two snapshots: moves, new requirements, and ones gone.

    A requirement that moved backwards is a move like any other. Nothing is
    reported against a missing earlier snapshot: a first snapshot is a
    starting point, not a day on which everything arrived.
    """
    if before is None:
        return ()
    moves: list[StageMove] = []
    for key, stage in sorted(after.items.items()):
        earlier = before.items.get(key)
        if earlier != stage:
            moves.append(
                StageMove(
                    key=key,
                    title=after.titles.get(key, key),
                    from_stage=earlier,
                    to_stage=stage,
                )
            )
    for key, stage in sorted(before.items.items()):
        if key not in after.items:
            moves.append(
                StageMove(
                    key=key, title=before.titles.get(key, key), from_stage=stage, to_stage=None
                )
            )
    return tuple(moves)


def _clean_names(names: Iterable[str], label: str, problems: list[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    keys: set[str] = set()
    for raw in names:
        name = " ".join(raw.split())
        if not name or _key(name) in keys:
            continue
        if len(name) > MAX_STATUS_NAME_LENGTH:
            problems.append(f"{label} has a name longer than {MAX_STATUS_NAME_LENGTH} characters")
            continue
        keys.add(_key(name))
        cleaned.append(name)
    if len(cleaned) > MAX_NAMES_PER_LIST:
        problems.append(f"{label} lists more than {MAX_NAMES_PER_LIST} names")
    return tuple(cleaned)


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()
