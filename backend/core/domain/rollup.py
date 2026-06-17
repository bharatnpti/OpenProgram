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


@dataclass(frozen=True, kw_only=True)
class RollupFactor:
    description: str
    contributes: Rag
    source_ref: EntityRef


@dataclass(frozen=True, kw_only=True)
class NodeStatus:
    entity_ref: EntityRef
    rag: Rag
    source: StatusSource
    factors: tuple[RollupFactor, ...]
    as_of: date
