from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from core.domain.graph import EntityRef
from core.domain.status import StatusSource


class Rag(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    UNKNOWN = "unknown"


class FactorKind(StrEnum):
    """Typed factor category so aggregation never string-matches descriptions."""

    BLOCKER = "blocker"
    STATUS = "status"
    TASK = "task"
    TARGET_DATE = "target_date"
    AGGREGATE = "aggregate"


@dataclass(frozen=True, kw_only=True)
class RollupFactor:
    description: str
    contributes: Rag
    source_ref: EntityRef
    kind: FactorKind = FactorKind.STATUS
    # Blocker attribution: which pods this factor applies to. Empty means the
    # factor is visible at every scope (all non-blocker factors, and legacy
    # blocker rows predating attribution).
    blocker_id: str | None = None
    work_item_ref: EntityRef | None = None
    applies_to_pod_ids: tuple[str, ...] = ()
    unattributed: bool = False


@dataclass(frozen=True, kw_only=True)
class NodeStatus:
    entity_ref: EntityRef
    rag: Rag
    source: StatusSource
    factors: tuple[RollupFactor, ...]
    as_of: date
